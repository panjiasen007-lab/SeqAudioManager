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

    # 确认没有把 7 个平台变体全带进去（正常只应有 1 个 DLL）
    pa_dir = internal / "_sounddevice_data" / "portaudio-binaries"
    if pa_dir.is_dir():
        dlls = [p.name for p in pa_dir.iterdir() if p.suffix.lower() in (".dll", ".dylib", ".so")]
        if len(dlls) > 1:
            print(f"\n  WARN  portaudio-binaries 里带了 {len(dlls)} 个二进制：{dlls}")
            print("        应只保留 libportaudio64bit.dll，其余平台变体请核对 build.py 的排除规则")

    print()
    if failed:
        print(f"结论：FAIL（{failed} 项缺失/异常）—— 装出来的程序一点播放就会报错，禁止发版")
        return 1
    print("结论：PASS —— 播放组件完整")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
