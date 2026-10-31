"""打包脚本：PyInstaller（默认，零编译依赖）或 Nuitka（更快更小，需 C 编译器）。

用法
----
    pip install pyinstaller
    python build.py                    # 默认 PyInstaller，产出 dist/音序管家.exe
    python build.py --toolkit nuitka   # 需 MSVC Build Tools（见下）
    python build.py --debug            # 保留控制台窗口，便于看启动异常
    python build.py --onedir           # 目录版：启动最快，便于排查缺 DLL

为什么默认 PyInstaller
----------------------
PyInstaller 不需要 C 编译器，装完就能出包，适合交付和快速迭代；
代价是启动要解包（首次 2~4 秒），且个别杀软会对 bootloader 误报。

什么时候换 Nuitka
-----------------
Nuitka 把 Python 编译成 C 再链接，启动快、体积小、误报少，但要求：
  * Python ≤ 3.12（**3.13 起 Nuitka 已禁用 --mingw64**，必须用 MSVC）
  * 安装 Visual Studio Build Tools（含 MSVC v143 + Windows SDK）
  * 环境允许查询注册表（Nuitka 靠 reg.exe 探测 MSVC；被安全策略禁用会卡死）

产物说明
--------
配置与备份默认写在 EXE 同级 ``data/``，同级不可写时回落到
``%APPDATA%/SeqAudioManager``（见 core/paths.py）。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ICON = ROOT / "assets" / "app.ico"
ENTRY = ROOT / "run.py"
OUTPUT_NAME = "音序管家"


# ---------------------------------------------------------------- PyInstaller


def build_pyinstaller(debug: bool = False, onedir: bool = False, clean: bool = True) -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("缺少 PyInstaller，请先执行：pip install pyinstaller")
        return 1

    dist = ROOT / "dist"
    work = ROOT / "build" / "pyinstaller"
    if clean:
        for path in (dist, work):
            shutil.rmtree(path, ignore_errors=True)
    dist.mkdir(parents=True, exist_ok=True)

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir" if onedir else "--onefile",
        "--console" if debug else "--windowed",
        "--name", OUTPUT_NAME,
        "--distpath", str(dist),
        "--workpath", str(work),
        "--specpath", str(work),
        "--paths", str(SRC),
        "--collect-submodules", "seqaudio",
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
        args += ["--add-data", f"{ICON.parent}{os.pathsep}assets"]
    else:
        print("提示：未找到 assets/app.ico，将使用默认图标（可先运行 tools/make_icon.py）")

    args.append(str(ENTRY))

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    print("执行：", " ".join(args))
    result = subprocess.run(args, cwd=str(ROOT), env=env)
    if result.returncode != 0:
        print("打包失败，返回码：", result.returncode)
        return result.returncode

    target = dist / f"{OUTPUT_NAME}.exe" if not onedir else dist / OUTPUT_NAME
    _report(target)
    return 0


# ---------------------------------------------------------------- Nuitka


def build_nuitka(debug: bool = False, onedir: bool = False, clean: bool = True) -> int:
    for module, hint in (
        ("nuitka", "nuitka"),
        ("zstandard", "zstandard"),
        ("ordered_set", "ordered-set"),
    ):
        try:
            __import__(module)
        except ImportError:
            print(f"缺少 {hint}，请先执行：pip install nuitka zstandard ordered-set")
            return 1

    if sys.version_info >= (3, 13):
        print(
            "警告：Python 3.13+ 上 Nuitka 不支持 --mingw64，必须已安装 MSVC Build Tools，\n"
            "      且环境需允许 Nuitka 通过 reg.exe 探测 MSVC，否则会卡在编译器检测。"
        )

    dist = ROOT / "dist"
    if clean:
        shutil.rmtree(dist, ignore_errors=True)
    dist.mkdir(parents=True, exist_ok=True)

    args = [
        sys.executable, "-m", "nuitka",
        "--standalone",
        "--assume-yes-for-downloads",
        "--enable-plugin=pyside6",
        # src 布局：用 -m 包模式编译，并把 src 加进搜索路径
        "--python-flag=-m",
        "--include-package=seqaudio",
        "--nofollow-import-to=tkinter",
        "--nofollow-import-to=unittest",
        "--nofollow-import-to=pydoc",
        "--remove-output",
        f"--output-dir={dist}",
        f"--output-filename={OUTPUT_NAME}",
        "--company-name=SeqAudio",
        "--product-name=音序管家",
        "--file-version=0.1.0.0",
        "--product-version=0.1.0",
        "--file-description=音乐元数据批量处理工具",
    ]
    if ICON.exists():
        args.append(f"--include-data-dir={ICON.parent}=assets")
        args.append(f"--windows-icon-from-ico={ICON}")
    if not onedir:
        args.append("--onefile")
        args.append("--onefile-tempdir-spec={CACHE_DIR}/SeqAudioManager/{VERSION}")
    args.append("--windows-console-mode=force" if debug else "--windows-console-mode=disable")
    args.append("seqaudio")

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    print("执行：", " ".join(args))
    result = subprocess.run(args, cwd=str(SRC), env=env)
    if result.returncode != 0:
        print("打包失败，返回码：", result.returncode)
        return result.returncode

    target = dist / f"{OUTPUT_NAME}.exe" if not onedir else dist / f"{OUTPUT_NAME}.dist"
    _report(target)
    return 0


# ---------------------------------------------------------------- 公共


def _report(target: Path) -> None:
    if not target.exists():
        print("打包命令结束，但未找到预期产物，请检查 dist 目录。")
        return
    size_mb = target.stat().st_size / 1024 / 1024 if target.is_file() else _dir_size(target)
    print(f"\n打包完成：{target}（约 {size_mb:.1f} MB）")


def _dir_size(path: Path) -> float:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024 / 1024


def main() -> int:
    parser = argparse.ArgumentParser(description="音序管家打包脚本")
    parser.add_argument("--toolkit", choices=["pyinstaller", "nuitka"], default="pyinstaller")
    parser.add_argument("--debug", action="store_true", help="保留控制台窗口")
    parser.add_argument("--onedir", action="store_true", help="生成目录版而非单文件")
    parser.add_argument("--no-clean", action="store_true", help="保留上次构建缓存")
    args = parser.parse_args()

    builder = build_nuitka if args.toolkit == "nuitka" else build_pyinstaller
    return builder(debug=args.debug, onedir=args.onedir, clean=not args.no_clean)


if __name__ == "__main__":
    sys.exit(main())
