"""Nuitka 单文件打包脚本（需求 4.1：绿色免安装单 EXE）。

用法
----
    pip install nuitka zstandard ordered-set
    python build.py                # 生成 dist/音序管家.exe
    python build.py --debug        # 保留控制台，便于看异常
    python build.py --onedir       # 生成目录版（启动更快，便于排查）

为什么用 Nuitka 而不是 PyInstaller
----------------------------------
* 编译成 C 再链接，启动快 30%~50%，没有 PyInstaller 的临时解包目录；
* 对杀软误报明显更少（PyInstaller 的 bootloader 是被误杀重灾区）；
* Qt 插件（platforms / imageformats / styles）由官方插件自动收敛，不易漏。

产物说明
--------
单 EXE 解包到临时目录运行，配置与备份默认写在 EXE 同级 ``data/``，
同级不可写时自动回落到 ``%APPDATA%/SeqAudioManager``（见 core/paths.py）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENTRY = ROOT / "src" / "seqaudio" / "__main__.py"
ICON = ROOT / "assets" / "app.ico"
OUTPUT_NAME = "音序管家"


def _check_deps() -> None:
    missing = []
    try:
        import nuitka  # noqa: F401
    except ImportError:
        missing.append("nuitka")
    try:
        import zstandard  # noqa: F401
    except ImportError:
        missing.append("zstandard")
    if missing:
        print("缺少打包依赖：" + ", ".join(missing))
        print(f"请先执行：pip install {' '.join(missing)} ordered-set")
        sys.exit(1)


def build(debug: bool = False, onedir: bool = False, clean: bool = True) -> int:
    _check_deps()
    out_dir = ROOT / "dist"
    if clean and out_dir.exists():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    args = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        "--include-package=seqaudio",
        "--include-data-dir=assets=assets",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=pydoc",
        "--remove-output",
        f"--output-dir={out_dir}",
        f"--output-filename={OUTPUT_NAME}.exe",
        "--company-name=SeqAudio",
        "--product-name=音序管家",
        "--file-version=0.1.0.0",
        "--product-version=0.1.0",
        "--file-description=音乐元数据批量处理工具",
    ]

    if not onedir:
        args.append("--onefile")
        args.append("--windows-temp-dir-override={CACHE_DIR}/seqaudio")

    args.append("--windows-console-mode=force" if debug else "--windows-console-mode=disable")

    if ICON.exists():
        args.append(f"--windows-icon-from-ico={ICON}")
    else:
        print("提示：未找到 assets/app.ico，将使用默认图标（可先运行 tools/make_icon.py 生成）")

    args.append(str(ENTRY))

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    print("执行：", " ".join(args))
    result = subprocess.run(args, cwd=str(ROOT), env=env)
    if result.returncode != 0:
        print("打包失败，返回码：", result.returncode)
        return result.returncode

    target = out_dir / f"{OUTPUT_NAME}.exe" if not onedir else out_dir / f"{OUTPUT_NAME}.dist"
    if target.exists():
        size_mb = (
            target.stat().st_size / 1024 / 1024 if target.is_file() else _dir_size(target)
        )
        print(f"\n打包完成：{target}（约 {size_mb:.1f} MB）")
    else:
        print("打包命令已结束，但未找到预期产物，请检查 dist 目录。")
    return 0


def _dir_size(path: Path) -> float:
    total = sum(p.stat().st_size for p in path.rglob("*") if p.is_file())
    return total / 1024 / 1024


def main() -> int:
    parser = argparse.ArgumentParser(description="音序管家打包脚本")
    parser.add_argument("--debug", action="store_true", help="保留控制台窗口")
    parser.add_argument("--onedir", action="store_true", help="生成目录版而非单文件")
    parser.add_argument("--no-clean", action="store_true", help="保留上次构建缓存（增量编译更快）")
    args = parser.parse_args()
    return build(debug=args.debug, onedir=args.onedir, clean=not args.no_clean)


if __name__ == "__main__":
    sys.exit(main())
