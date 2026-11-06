#!/usr/bin/env python
"""端到端测试：安装包全生命周期。

覆盖链路：
    全新安装 → 修复安装（同版本）→ 升级安装（旧版本）
    → 程序运行中拦截（返回码 2）→ 关闭后重试成功 → 卸载清理

用法::

    python tests/e2e_installer.py [安装包路径]

注意
----
1. 会临时改写 HKCU 卸载项、桌面与开始菜单快捷方式。脚本在 ``finally``
   里重装回原安装目录，把现场恢复成测试前的样子（原注册表值逐项还原）。
2. 全程用 ``--silent`` 走无人值守分支；GUI 的「重试」对话框另行人工验证，
   这里验证的是它背后同一套 ``running_instances`` 判定与返回码。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import winreg
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "installer"))

APP_NAME = "音序管家"
EXE_NAME = "音序管家.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SeqAudioManager"
REPORT = Path(tempfile.gettempdir()) / "seqaudio_install.json"

results: list[tuple[bool, str, str]] = []


# --------------------------------------------------------------------- 工具


def check(ok: bool, name: str, detail: str = "") -> bool:
    results.append((bool(ok), name, detail))
    mark = "✓" if ok else "✗"
    print(f"  {mark} {name}" + (f"  —  {detail}" if detail else ""))
    return ok


def desktop_lnk() -> Path:
    return Path(os.environ.get("USERPROFILE", "")) / "Desktop" / f"{APP_NAME}.lnk"


def startmenu_lnk() -> Path:
    base = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return base / f"{APP_NAME}.lnk"


def startmenu_uninstall_lnk() -> Path:
    base = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return base / f"卸载 {APP_NAME}.lnk"


def snapshot_registry() -> dict[str, tuple[object, int]] | None:
    """把 HKCU 卸载项读成 {值名: (值, 类型)}，键不存在返回 None。"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        return None
    values: dict[str, tuple[object, int]] = {}
    with key:
        index = 0
        while True:
            try:
                name, value, kind = winreg.EnumValue(key, index)
            except OSError:
                break
            values[name] = (value, kind)
            index += 1
    return values


def delete_registry() -> None:
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def write_registry_values(values: dict[str, tuple[object, int]]) -> None:
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    with key:
        for name, (value, kind) in values.items():
            winreg.SetValueEx(key, name, 0, kind, value)


def read_registry() -> dict[str, object]:
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        return {}
    with key:
        out: dict[str, object] = {}
        index = 0
        while True:
            try:
                name, value, _kind = winreg.EnumValue(key, index)
            except OSError:
                break
            out[name] = value
            index += 1
    return out


def registry_exists() -> bool:
    return bool(read_registry())


def run_setup(setup: Path, target: Path, extra: tuple[str, ...] = ()) -> tuple[int, dict]:
    """跑一次静默安装，返回 (退出码, 报告)。"""
    REPORT.unlink(missing_ok=True)
    proc = subprocess.run(
        [str(setup), "--silent", str(target), *extra],
        capture_output=True,
        timeout=900,
    )
    time.sleep(1.2)
    data: dict = {}
    if REPORT.exists():
        try:
            data = json.loads(REPORT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
    return proc.returncode, data


def running_pids(exe: Path) -> list[int]:
    import installer

    return installer.running_instances(exe)


def start_app(exe: Path) -> subprocess.Popen:
    proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
    deadline = time.time() + 60
    while time.time() < deadline:
        if running_pids(exe):
            return proc
        time.sleep(0.5)
    stop_app(proc)
    raise RuntimeError("主程序 60 秒内没有启动起来")


def stop_app(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def run_uninstall_script(target: Path, *, keep_data: bool) -> None:
    """走与产品一致的卸载路径：主进程清快捷方式+注册表，PowerShell 删目录。

    产品里 ``run_uninstall`` 会给脚本传 ``wait_pid`` 等自己退出；测试进程还活着，
    这里不传，直接用 EncodedCommand 同步执行，其余逻辑与产品完全相同。
    """
    from seqaudio.core.uninstall import (
        build_uninstall_script,
        remove_shortcuts_and_registry,
        script_command,
    )

    for item in remove_shortcuts_and_registry():
        print(f"    [主进程] {item}")
    script = build_uninstall_script(target, keep_data=keep_data)
    # 真实脚本要等主进程退出才动手，测试里没有待退出的进程，缩短起始等待
    script = script.replace("Start-Sleep -Milliseconds 600", "Start-Sleep -Milliseconds 200")
    # 与产品同一条路径：-EncodedCommand 传脚本，避免临时 .ps1 被 ANSI 代码页
    # 解释成乱码导致中文 lnk 路径匹配不上
    proc = subprocess.run(
        script_command(script),
        capture_output=True,
        timeout=180,
    )
    out = proc.stdout.decode("utf-8", "replace").strip()
    if out:
        print("    [卸载脚本输出] " + out[:300])
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        print(f"    [卸载脚本退出码 {proc.returncode}] {err[:300]}")


def wait_until(predicate, seconds: float = 40.0, step: float = 0.4) -> bool:
    """轮询等待条件成立。

    卸载是「先删 300MB 目录、再删注册表、最后删快捷方式」的顺序执行，固定
    ``sleep(1.5)`` 会在目录还没删完时就去检查快捷方式，三项断言必然抢跑报错
    （不是产品问题，是测试等太短）。
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


def find_setup() -> Path:
    import installer  # 只为拿版本号

    dist = ROOT / "dist"
    preferred = dist / f"{APP_NAME}-v{installer.VERSION}-x86-x64-Setup.exe"
    if preferred.exists():
        return preferred
    candidates = sorted(dist.glob("*-Setup.exe"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        raise SystemExit("dist/ 下找不到安装包，请先运行 build.py")
    return candidates[0]


# --------------------------------------------------------------------- 主流程


def main() -> int:
    setup = Path(sys.argv[1]) if len(sys.argv) > 1 else find_setup()
    print(f"安装包：{setup}")
    print(f"大小：{setup.stat().st_size / 1024 / 1024:.1f} MB")

    import installer

    version = installer.VERSION
    sandbox = Path(tempfile.mkdtemp(prefix="seqaudio_e2e_")) / "app"
    print(f"沙箱目录：{sandbox}")
    print(f"目标版本：{version}")

    snapshot = snapshot_registry()
    # snapshot 里每项是 (值, 类型)；InstallLocation 取 [0] 才是真路径
    restore_dir: Path | None = None
    if snapshot and "InstallLocation" in snapshot:
        restore_dir = Path(str(snapshot["InstallLocation"][0]))
    print(f"测试前安装状态：{'已安装 → ' + str(restore_dir) if snapshot else '未安装'}")

    try:
        # ---------------------------------------------------------- 1 全新安装
        print("\n[1] 全新安装")
        delete_registry()
        shutil.rmtree(sandbox, ignore_errors=True)
        code, report = run_setup(setup, sandbox)
        check(code == 0, "退出码 0", f"实际 {code}")
        check(report.get("ok") is True, "报告 ok", str(report.get("error") or ""))
        check(report.get("mode") == "fresh", "识别为全新安装", f"实际 {report.get('mode')}")
        check((sandbox / EXE_NAME).exists(), "主程序已就位")
        check((sandbox / "_internal").is_dir(), "运行库目录已就位")
        check((sandbox / f"卸载 {APP_NAME}.lnk").exists(), "安装目录内含卸载快捷方式")
        check(desktop_lnk().exists(), "桌面快捷方式")
        check(startmenu_lnk().exists(), "开始菜单快捷方式")
        check(startmenu_uninstall_lnk().exists(), "开始菜单卸载快捷方式")
        check(read_registry().get("DisplayVersion") == version, "注册表版本", f"{read_registry().get('DisplayVersion')}")
        check(str(read_registry().get("InstallLocation", "")).rstrip("\\") == str(sandbox).rstrip("\\"), "注册表安装路径")

        # ---------------------------------------------------------- 2 修复安装
        print("\n[2] 修复安装（同版本）")
        sentinel = sandbox / "data" / "config.json"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text('{"sentinel": "keep-me"}', encoding="utf-8")
        (sandbox / EXE_NAME).write_bytes(b"CORRUPTED")  # 模拟文件损坏
        code, report = run_setup(setup, sandbox)
        check(report.get("mode") == "repair", "识别为修复安装", f"实际 {report.get('mode')}")
        check(code == 0 and report.get("ok"), "修复安装成功")
        check((sandbox / EXE_NAME).stat().st_size > 1_000_000, "损坏的主程序被重新铺好")
        check(sentinel.exists() and "keep-me" in sentinel.read_text(encoding="utf-8"), "data/config.json 未被清除")

        # ------------------------------------------------------ 2.5 降级拒绝
        print("\n[2.5] 已装更高版本时拒绝安装")
        current = snapshot_registry() or {}
        current["DisplayVersion"] = ("9.9.9", winreg.REG_SZ)
        write_registry_values(current)
        exe_size_before = (sandbox / EXE_NAME).stat().st_size
        code, report = run_setup(setup, sandbox)
        check(code == 3, "返回码 3（拒绝降级的专用码）", f"实际 {code}")
        check(report.get("error") == "installed_version_newer", "报告 installed_version_newer",
              str(report.get("error") or ""))
        check(report.get("mode") == "downgrade", "识别为 downgrade", f"实际 {report.get('mode')}")
        check(
            "9.9.9" in (report.get("message") or "") and version in (report.get("message") or ""),
            "提示文案写明两个版本号",
            (report.get("message") or "").replace("\n", " ")[:60],
        )
        check(read_registry().get("DisplayVersion") == "9.9.9", "注册表版本未被改动",
              f"{read_registry().get('DisplayVersion')}")
        check((sandbox / EXE_NAME).stat().st_size == exe_size_before, "程序文件未被降级覆盖")

        # ---------------------------------------------------------- 3 升级安装
        print("\n[3] 升级安装（模拟已装 1.0.0）")
        # 注意格式：snapshot_registry 的每项是 (值, 类型)，不能直接塞字符串
        current = snapshot_registry() or {}
        current["DisplayVersion"] = ("1.0.0", winreg.REG_SZ)
        write_registry_values(current)
        (sandbox / EXE_NAME).write_bytes(b"OLD-VERSION-EXE")
        code, report = run_setup(setup, sandbox)
        check(report.get("mode") == "upgrade", "识别为升级安装", f"实际 {report.get('mode')}")
        check(report.get("installed_version") == "1.0.0", "读到旧版本号", f"{report.get('installed_version')}")
        check(code == 0 and report.get("ok"), "升级安装成功")
        check((sandbox / EXE_NAME).stat().st_size > 1_000_000, "主程序被真实替换")
        check(not any(p.is_dir() and p.name.startswith(f"{APP_NAME}-v") for p in sandbox.iterdir()), "无整包副本残留")
        check(sentinel.exists(), "升级后配置仍在")
        check(read_registry().get("DisplayVersion") == version, "注册表版本已更新", f"{read_registry().get('DisplayVersion')}")

        # ---------------------------------------------------- 4 运行中拦截
        print("\n[4] 程序运行中安装应被拦截")
        proc = start_app(sandbox / EXE_NAME)
        try:
            code, report = run_setup(setup, sandbox)
            check(code == 2, "退出码 2（占用专用码）", f"实际 {code}")
            check(report.get("error") == "program_running", "报告 program_running", str(report.get("error")))
            check(bool(report.get("running_pids")), "报告占用进程 PID", str(report.get("running_pids")))
            check(report.get("ok") is False, "未被误判为安装成功")
            check((sandbox / EXE_NAME).stat().st_size > 1_000_000, "拦截后未破坏现有安装")
        finally:
            stop_app(proc)
        code, report = run_setup(setup, sandbox)
        check(code == 0 and report.get("ok"), "关闭程序后重试成功", f"退出码 {code}")

        # ---------------------------------------------------------- 5 卸载
        print("\n[5] 卸载")
        run_uninstall_script(sandbox, keep_data=False)
        wait_until(lambda: not sandbox.exists())
        wait_until(lambda: not desktop_lnk().exists(), 15)
        check(not sandbox.exists(), "安装目录已删除")
        check(not registry_exists(), "注册表项已删除")
        check(not desktop_lnk().exists(), "桌面快捷方式已删除")
        check(not startmenu_lnk().exists(), "开始菜单快捷方式已删除")
        check(not startmenu_uninstall_lnk().exists(), "开始菜单卸载快捷方式已删除")

    finally:
        print("\n[恢复] 还原测试前的安装状态")
        shutil.rmtree(sandbox, ignore_errors=True)
        if snapshot is None:
            delete_registry()
            for path in (desktop_lnk(), startmenu_lnk(), startmenu_uninstall_lnk()):
                path.unlink(missing_ok=True)
            print("  测试前未安装，已清空注册表与快捷方式")
        else:
            target = restore_dir or installer.default_install_dir()
            code, report = run_setup(setup, target)
            restored = (Path(target) / EXE_NAME).exists() and read_registry().get("DisplayVersion") == version
            print(f"  已重装回 {target}（退出码 {code}，校验 {'通过' if restored else '失败'}）")
            if not restored:
                write_registry_values(snapshot)  # 兜底：至少把注册表还原

    # ----------------------------------------------------------------- 汇总
    failed = [name for ok, name, _ in results if not ok]
    total = len(results)
    print("\n" + "=" * 60)
    print(f"共 {total} 项检查，通过 {total - len(failed)} 项，失败 {len(failed)} 项")
    if failed:
        for name in failed:
            print("  ✗ " + name)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
