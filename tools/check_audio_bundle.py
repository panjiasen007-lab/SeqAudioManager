"""校验打包产物里的播放组件是否完整（发版前置检查，任何一项 FAIL 则退出码非 0）。

背景：sounddevice 依赖的 PortAudio DLL 位于包外的 ``_sounddevice_data`` 目录，
PyInstaller 的 ``--collect-data`` 抓不到。漏带的症状是「exe 启动正常、一点播放
就 OSError」，源码环境永远复现不了——所以每次发版前必须机械地检查文件确实在包里。

用法::

    python tools/check_audio_bundle.py                 # 检查 portable dist
    python tools/check_audio_bundle.py <安装目录路径>   # 检查任意安装位置
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIST = ROOT / "build" / "portable" / "dist" / "音序管家" / "_internal"

#: (说明, 相对 _internal 的路径或 glob, 最小字节数)
# 注意：sounddevice.py / _sounddevice_data.__init__ 是纯 Python 模块，PyInstaller
# 打进 PYZ 归档、不落独立文件——它们的存活以 exe `--selftest` 输出的
# `sounddevice_error` 字段为准（缺 DLL 时 import 会直接抛 OSError）。
CHECKS = [
    ("PortAudio x64 DLL", "_sounddevice_data/portaudio-binaries/libportaudio64bit.dll", 100_000),
    ("cffi 后端", "_cffi_backend*.pyd", 100_000),
]

#: 必须存在于 PYZ 归档里的纯 Python 模块（它们不落独立文件，只能查归档）
REQUIRED_MODULES = ("sounddevice", "_sounddevice", "_sounddevice_data", "cffi")


def _read_zlib(reader_cls, path: Path) -> set[str] | None:
    try:
        reader = reader_cls(str(path))
    except Exception as exc:  # noqa: BLE001
        print(f"  WARN  读取 {path.name} 失败：{exc}")
        return None
    toc = getattr(reader, "toc", None) or {}
    return set(toc.keys()) if hasattr(toc, "keys") else None


def _pyz_modules(internal: Path) -> set[str] | None:
    """读 PYZ 归档里的模块名表；读不到返回 None（不算失败）。

    为什么需要这一步：``sounddevice.py`` / ``_sounddevice_data/__init__.py`` 都是
    纯 Python 模块，PyInstaller 会把它们编译进 PYZ 归档，磁盘上没有同名文件。
    只查文件会得出"缺模块"的假警报，而只靠 ``--selftest`` 又要先跑起来 ——
    发版检查最好能"静态"回答这个问题。

    PyInstaller 5 把 PYZ 落成 ``_internal/PYZ-00.pyz``；6.x 的 onedir 直接把它
    嵌在 exe 的 CArchive 里（entry 名 ``PYZ.pyz``）——两种布局都要能读。
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
    except Exception:  # noqa: BLE001 - 没装 PyInstaller 就退化为只查文件
        return None

    for candidate in sorted(internal.glob("PYZ-*.pyz")):
        modules = _read_zlib(ZlibArchiveReader, candidate)
        if modules:
            return modules

    import tempfile

    for exe in sorted(internal.parent.glob("*.exe")):
        try:
            archive = CArchiveReader(str(exe))
            entries = list(getattr(archive, "toc", {}) or {})
        except Exception:  # noqa: BLE001
            continue
        name = next((item for item in entries if "pyz" in str(item).lower()), None)
        if not name:
            continue
        try:
            payload = archive.extract(str(name))
        except Exception as exc:  # noqa: BLE001
            print(f"  WARN  从 {exe.name} 提取 PYZ 失败：{exc}")
            continue
        if not payload:
            continue
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / "PYZ.pyz"
            dump.write_bytes(payload)
            modules = _read_zlib(ZlibArchiveReader, dump)
            if modules:
                return modules
    return None


def _resolve(internal: Path, pattern: str) -> Path | None:
    """路径含通配符时取第一个匹配（如 _cffi_backend.cp313-win_amd64.pyd）。"""
    if any(ch in pattern for ch in "*?["):
        matches = sorted(internal.glob(pattern))
        return matches[0] if matches else None
    return internal / pattern


def main(argv: list[str]) -> int:
    internal = Path(argv[1]) if len(argv) > 1 else DEFAULT_DIST
    if not internal.is_dir():
        # 兼容 onedir 无 _internal 的旧布局
        alt = internal.parent
        if (alt / "sounddevice.py").exists() or (alt / "_sounddevice_data").exists():
            internal = alt
        else:
            print(f"FAIL 目录不存在：{internal}")
            return 2

    print(f"检查目标：{internal}\n")
    failed = 0
    for label, rel, min_size in CHECKS:
        path = _resolve(internal, rel)
        if path is None or not path.exists():
            print(f"  FAIL  {label:<18} 缺失 {rel}")
            failed += 1
            continue
        size = path.stat().st_size
        ok = size >= min_size
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<18} {path.relative_to(internal)}  {size / 1024:.0f} KB")
        if not ok:
            failed += 1

    # 模块层检查（PYZ 归档）
    modules = _pyz_modules(internal)
    if modules is None:
        print("  WARN  模块层检查跳过（读不到 PYZ 归档；请改用 --selftest 补充确认）")
    else:
        for name in REQUIRED_MODULES:
            ok = name in modules
            print(f"  {'PASS' if ok else 'FAIL'}  {'模块 ' + name:<18} PYZ-00.pyz")
            if not ok:
                failed += 1

    # 确认没有把 7 个平台/位数/ASIO 变体全带进去（v1.7 只允许 win64 非 ASIO 那一个）
    pa_dir = internal / "_sounddevice_data" / "portaudio-binaries"
    if pa_dir.is_dir():
        dlls = [
            p.name for p in pa_dir.iterdir() if p.suffix.lower() in (".dll", ".dylib", ".so")
        ]
        extra = [name for name in dlls if name != "libportaudio64bit.dll"]
        if extra:
            # v1.7 T06：ASIO/32bit/arm64 变体从未被验证过，出现即视为失败
            print(f"  FAIL  PortAudio 变体过多：{sorted(dlls)}")
            print("        只允许 libportaudio64bit.dll（v1.7 明确不做 ASIO/DLL 替换）")
            failed += 1
        else:
            print(f"  PASS  {'PortAudio 变体':<18} 仅 {dlls[0] if dlls else '（缺失）'}")

    print()
    if failed:
        print(f"结论：FAIL（{failed} 项缺失/异常）—— 装出来的程序一点播放就会报错，禁止发版")
        return 1
    print("结论：PASS —— 播放组件完整")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
