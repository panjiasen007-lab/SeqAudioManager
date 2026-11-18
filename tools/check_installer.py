"""安装器全场景验收：全新安装 / 降级拒绝 / 升级沿用路径 / 修复 / 卸载 / 数据回迁。

自包含：自己准备环境（装到自定义目录再改注册表版本模拟旧版），跑完整链路。
用法：python _test_installer.py
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

ROOT = Path("D:/SeqAudio Manager")
SETUP = ROOT / "dist" / "音序管家-v1.5.3-x86-x64-Setup.exe"
KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SeqAudioManager"
REPORT = Path(tempfile.gettempdir()) / "seqaudio_install.json"
SELFTEST = Path(tempfile.gettempdir()) / "seqaudio_selftest.json"
APP = "音序管家.exe"

INSTALL_A = ROOT / "_acc_install_a"      # 模拟「用户装在别的盘」
INSTALL_B = ROOT / "_acc_install_b"      # 升级时故意传错的目录
KEPT = Path(os.environ.get("APPDATA", "")) / "SeqAudioManager"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"{'[PASS]' if ok else '[FAIL]'} {name}" + (f" — {detail}" if detail else ""))


def reg_read() -> dict:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
            return {
                winreg.EnumValue(k, i)[0]: winreg.EnumValue(k, i)[1]
                for i in range(winreg.QueryInfoKey(k)[1])
            }
    except FileNotFoundError:
        return {}


def reg_set(name: str, value: str) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, value)


def run_setup(*args: str, timeout: int = 900) -> tuple[int, dict]:
    if REPORT.exists():
        REPORT.unlink()
    proc = subprocess.run([str(SETUP), *args], timeout=timeout)
    time.sleep(0.6)
    data = json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else {}
    return proc.returncode, data


def wait_gone(path: Path, seconds: int = 30) -> bool:
    for _ in range(seconds * 2):
        if not path.exists():
            return True
        time.sleep(0.5)
    return not path.exists()


def desktop_path() -> Path:
    """桌面真实路径（可能被 OneDrive 重定向）。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
        ) as k:
            return Path(os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0]))
    except OSError:
        return Path(os.environ.get("USERPROFILE", "")) / "Desktop"


def startmenu_path() -> Path:
    base = os.environ.get("APPDATA", "")
    return Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs"


def main() -> int:
    print(f"安装包：{SETUP}\n" + "=" * 74)
    if not SETUP.exists():
        print("安装包不存在，先 python build.py")
        return 1

    # 清场：目录 + 注册表，保证 T5 是真正的全新安装
    for d in (INSTALL_A, INSTALL_B):
        shutil.rmtree(d, ignore_errors=True)
    if reg_read():
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)
            print("已清理上次测试残留的注册表项")
        except OSError as exc:
            print("清理注册表失败：", exc)

    # ---------------------------------------------------------------- T5 全新
    # 带快捷方式安装，这样 T4 卸载时才能验证快捷方式真的被清掉
    code, rep = run_setup("--silent", str(INSTALL_A))
    check("T5 全新安装（自定义目录）", code == 0 and rep.get("mode") == "fresh" and rep.get("ok"),
          f"exit={code} mode={rep.get('mode')}")
    check("T5 主程序已落地", (INSTALL_A / APP).exists(), str(INSTALL_A / APP))
    check("T5 注册表指向该目录", Path(reg_read().get("InstallLocation", "")) == INSTALL_A,
          reg_read().get("InstallLocation", "-"))
    check(
        "T5 桌面快捷方式已创建",
        (desktop_path() / "音序管家.lnk").exists(),
        str(desktop_path() / "音序管家.lnk"),
    )

    if (INSTALL_A / APP).exists():
        if SELFTEST.exists():
            SELFTEST.unlink()
        st = subprocess.run([str(INSTALL_A / APP), "--selftest"], timeout=300)
        ok = SELFTEST.exists() and json.loads(SELFTEST.read_text(encoding="utf-8")).get("formats", 0) > 0
        check("T5 装的程序能跑 --selftest", st.returncode == 0 and ok, "formats>0" if ok else "失败")

    # 模拟「这个目录里装的是旧版 1.4.3」
    reg_set("DisplayVersion", "1.4.3")

    # ---------------------------------------------------------------- T1 降级
    reg_set("DisplayVersion", "9.9.9")
    before = reg_read().get("EstimatedSize")
    code, rep = run_setup("--silent", str(INSTALL_A), "--no-shortcuts")
    check("T1 已装更高版本 → 拒绝安装", code == 3 and rep.get("error") == "installed_version_newer",
          f"exit={code} error={rep.get('error')}")
    check("T1 提示文案含版本信息",
          "9.9.9" in str(rep.get("message", "")) and "1.5.3" in str(rep.get("message", "")),
          str(rep.get("message", ""))[:46].replace("\n", " "))
    check("T1 拒绝时没有改动注册表版本", reg_read().get("DisplayVersion") == "9.9.9",
          reg_read().get("DisplayVersion", "-"))
    check("T1 拒绝时没有动安装目录", (INSTALL_A / APP).exists(), str(INSTALL_A))

    # ---------------------------------------------------------------- T2 升级
    reg_set("DisplayVersion", "1.4.3")
    code, rep = run_setup("--silent", str(INSTALL_B), "--no-shortcuts")
    check("T2 升级忽略传入目录、装回老路径",
          code == 0 and Path(rep.get("target", "")) == INSTALL_A,
          f"target={rep.get('target')}")
    check("T2 未在错误目录装出第二份", not INSTALL_B.exists(), str(INSTALL_B))
    check("T2 模式为 upgrade", rep.get("mode") == "upgrade", f"mode={rep.get('mode')}")
    check("T2 注册表版本升到 1.5.3", reg_read().get("DisplayVersion") == "1.5.3",
          reg_read().get("DisplayVersion", "-"))

    # ---------------------------------------------------------------- T3 修复
    marker = INSTALL_A / "data" / "_acc_marker.txt"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("keep", encoding="utf-8")
    code, rep = run_setup("--silent", str(INSTALL_A), "--no-shortcuts")
    check("T3 同版本 → 修复模式", code == 0 and rep.get("mode") == "repair", f"mode={rep.get('mode')}")
    check("T3 修复保留 data 目录", marker.exists(), str(marker))

    # ---------------------------------------------------------------- T4 卸载
    exe = INSTALL_A / APP
    proc = subprocess.run([str(exe), "--uninstall", "--silent"], timeout=180)
    gone = wait_gone(INSTALL_A)
    check("T4 静默卸载退出码 0", proc.returncode == 0, f"exit={proc.returncode}")
    check("T4 安装目录已删除", gone, str(INSTALL_A))
    check("T4 注册表项已清理", not reg_read(), str(list(reg_read())[:4]))
    kept_marker = KEPT / "_acc_marker.txt"
    check("T4 data 已迁移到 %APPDATA%", kept_marker.exists(), str(kept_marker))
    check("T4 桌面快捷方式已删除",
          not (desktop_path() / "音序管家.lnk").exists(),
          str(desktop_path() / "音序管家.lnk"))
    check("T4 开始菜单快捷方式已删除",
          not (startmenu_path() / "音序管家.lnk").exists()
          and not (startmenu_path() / "卸载 音序管家.lnk").exists(),
          str(startmenu_path()))

    # ------------------------------------------------- T6 重装后数据自动回迁
    if kept_marker.exists():
        code, rep = run_setup("--silent", str(INSTALL_A), "--no-shortcuts")
        # data_dir() 在程序首次启动时才计算，所以要真跑一次程序
        if (INSTALL_A / APP).exists():
            subprocess.run([str(INSTALL_A / APP), "--selftest"], timeout=300)
        restored = INSTALL_A / "data" / "_acc_marker.txt"
        check("T6 重装后把保留数据自动搬回", restored.exists(), str(restored))

    print("=" * 74)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"通过 {passed}/{len(results)}")
    for name, ok, detail in results:
        if not ok:
            print(f"  失败：{name} — {detail}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
