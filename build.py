"""打包脚本：一次产出「安装版」与「便携绿色版」。

用法
----
    pip install pyinstaller py7zr
    python build.py                 # 默认：便携版 7z + 安装程序 exe
    python build.py --target portable
    python build.py --target installer
    python build.py --target onefile   # 单个 EXE，便于快速自测
    python build.py --debug            # 保留控制台窗口

产物
----
    dist/音序管家-v1.0.0-x86-x64.7z             便携绿色版（解压即用）
    dist/音序管家-v1.0.0-x86-x64-Setup.exe      安装程序（写入 HKCU 卸载项、建快捷方式）

为什么便携版用 onedir 而不是 onefile
------------------------------------
onefile 每次启动都要把 ~250MB 内容解压到临时目录，首次要 3~5 秒；onedir 直接
加载，秒开。便携版本来就是"解压到 U 盘直接跑"，用目录结构更合适。
安装版则复用同一份 7z 作为载荷 —— 一次压缩，两处使用，省一半时间。

为什么自己写安装器
------------------
这台机器没有 Inno Setup / NSIS，且都不方便引入。既然 PySide6 已经在手上，
直接用 Qt 写一个三页向导，功能一样，还省掉一整套外部工具链的维护成本。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
ASSETS = ROOT / "assets"
ICON = ASSETS / "app.ico"
ENTRY = ROOT / "run.py"
INSTALLER_DIR = ROOT / "installer"

sys.path.insert(0, str(SRC))

try:
    from seqaudio import APP_NAME, __version__ as VERSION  # noqa: E402
except Exception:  # noqa: BLE001 - 允许在未安装依赖时仅打印帮助
    APP_NAME, VERSION = "音序管家", "0.0.0"

#: 用户要求的命名格式：XXXX-v1.0.0-x86-x64
ARCH_TAG = "x86-x64"
PORTABLE_NAME = f"{APP_NAME}-v{VERSION}-{ARCH_TAG}"
ARCHIVE_NAME = f"{PORTABLE_NAME}.7z"
SETUP_NAME = f"{PORTABLE_NAME}-Setup"

DIST = ROOT / "dist"
BUILD = ROOT / "build"

README_TXT = """音序管家（SeqAudio Manager）· 便携绿色版
========================================

解压后双击「音序管家.exe」即可运行，无需安装。
配置、缓存、日志与备份都写在程序目录的 data\\ 下（U 盘可用）。
如果所在目录不可写（例如放在只读介质或 Program Files），
会自动回落到 %APPDATA%\\SeqAudioManager。

授权
----
本程序以 GNU General Public License v3.0 授权，协议全文见 LICENSE。
字体 Noto Sans SC / JetBrains Mono 为 SIL OFL 1.1 授权。
第三方组件与授权见 THIRD-PARTY.md。

提示
----
* 首次运行会弹出使用条款，同意后不再提示。
* 真假无损鉴定需要解码音频，单首约 0.3~1 秒。
* 清理假无损默认移入回收站，可在资源管理器里还原。
"""


# ---------------------------------------------------------------- 工具


def _run(args: list[str], *, cwd: Path = ROOT, env_extra: dict | None = None) -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if env_extra:
        env.update(env_extra)
    print("执行：", " ".join(args))
    return subprocess.run(args, cwd=str(cwd), env=env).returncode


def _dir_size(path: Path) -> float:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file()) / 1024 / 1024


def _copy_tree(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


# ---------------------------------------------------------------- PyInstaller


def _pyinstaller_common(debug: bool) -> list[str]:
    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--console" if debug else "--windowed",
        "--paths", str(SRC),
        "--collect-submodules", "seqaudio",
        # 真假无损鉴定依赖内置 ffmpeg 解码（DSD / APE / WV 都吃）
        "--collect-binaries", "imageio_ffmpeg",
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
        # 整目录带走：图标、字体、LICENSE 都在 assets 下
        args += ["--add-data", f"{ASSETS}{os.pathsep}assets"]
    return args


def build_onedir(debug: bool = False) -> Path:
    """目录版：便携版与安装包载荷的来源。"""
    out = BUILD / "portable"
    result = _pyinstaller_common(debug) + [
        "--onedir",
        "--name", APP_NAME,
        "--distpath", str(out / "dist"),
        "--workpath", str(out / "work"),
        "--specpath", str(out / "work"),
        str(ENTRY),
    ]
    if _run(result) != 0:
        raise SystemExit("PyInstaller 打包失败")

    app_dir = out / "dist" / APP_NAME
    if not app_dir.exists():
        raise SystemExit(f"未找到预期产物：{app_dir}")

    # 便携版该有的说明文件
    _write_text(app_dir / "使用说明.txt", README_TXT)
    for name in ("LICENSE", "THIRD-PARTY.md"):
        source = ASSETS / name if (ASSETS / name).exists() else ROOT / name
        if source.exists():
            shutil.copy2(source, app_dir / name)
    return app_dir


def build_portable(debug: bool = False) -> Path:
    """把目录版压成 .7z 便携包。"""
    app_dir = build_onedir(debug=debug)
    DIST.mkdir(parents=True, exist_ok=True)
    archive = DIST / ARCHIVE_NAME

    import py7zr

    print(f"\n压缩 {_dir_size(app_dir):.0f} MB → {archive.name}（LZMA2，需要几分钟）")
    started = time.time()
    if archive.exists():
        archive.unlink()
    with py7zr.SevenZipFile(
        archive,
        "w",
        filters=[{"id": py7zr.FILTER_LZMA2, "preset": 4}],
    ) as handle:
        handle.writeall(app_dir, arcname=PORTABLE_NAME)
    print(f"压缩完成：{archive}（{archive.stat().st_size / 1024 / 1024:.1f} MB，"
          f"{time.time() - started:.0f}s）")
    return archive


# ---------------------------------------------------------------- 安装程序


def _write_meta() -> Path:
    meta = INSTALLER_DIR / "_meta.py"
    meta.parent.mkdir(parents=True, exist_ok=True)
    meta.write_text(
        "# 由 build.py 生成，请勿手改\n"
        f'APP_NAME = "{APP_NAME}"\n'
        f'VERSION = "{VERSION}"\n'
        f'PAYLOAD = "{ARCHIVE_NAME}"\n',
        encoding="utf-8",
    )
    return meta


def build_installer(debug: bool = False, archive: Path | None = None) -> Path:
    archive = archive or DIST / ARCHIVE_NAME
    if not archive.exists():
        print("未找到便携包，先构建便携版…")
        archive = build_portable(debug=debug)

    _write_meta()
    DIST.mkdir(parents=True, exist_ok=True)
    out = BUILD / "installer"

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean",
        "--onefile",
        "--console" if debug else "--windowed",
        "--name", SETUP_NAME,
        "--distpath", str(DIST),
        "--workpath", str(out / "work"),
        "--specpath", str(out / "work"),
        "--add-data", f"{archive}{os.pathsep}.",
        "--add-data", f"{ASSETS / 'app.png'}{os.pathsep}.",
        "--add-data", f"{ASSETS / 'fonts'}{os.pathsep}fonts",
        # py7zr 的压缩后端是 C 扩展，显式收集避免漏打
        "--collect-submodules", "py7zr",
        "--hidden-import", "pybcj",
        "--hidden-import", "pyppmd",
        "--hidden-import", "inflate64",
        "--hidden-import", "brotli",
        "--hidden-import", "Cryptodome",
        "--exclude-module", "tkinter",
        "--exclude-module", "seqaudio",
        # 安装器只用到 QtCore/QtGui/QtWidgets，其余模块（QML、多媒体、网络…）
        # 一律排除：它们的 DLL 会让安装包白白多出几十 MB
        "--exclude-module", "PySide6.QtNetwork",
        "--exclude-module", "PySide6.QtQml",
        "--exclude-module", "PySide6.QtQuick",
        "--exclude-module", "PySide6.QtQuickWidgets",
        "--exclude-module", "PySide6.QtSvg",
        "--exclude-module", "PySide6.QtMultimedia",
        "--exclude-module", "PySide6.QtSql",
        "--exclude-module", "PySide6.QtTest",
        "--exclude-module", "PySide6.QtOpenGL",
        "--exclude-module", "PySide6.QtDBus",
        "--exclude-module", "PySide6.QtXml",
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
    ]
    if ICON.exists():
        args += ["--icon", str(ICON)]
    args.append(str(INSTALLER_DIR / "installer.py"))

    if _run(args) != 0:
        raise SystemExit("安装程序打包失败")

    target = DIST / f"{SETUP_NAME}.exe"
    if not target.exists():
        raise SystemExit(f"未找到安装程序：{target}")
    print(f"\n安装程序：{target}（{target.stat().st_size / 1024 / 1024:.1f} MB）")
    return target


def build_onefile(debug: bool = False) -> Path:
    """单个 EXE，仍保留给快速自测用。"""
    out = BUILD / "onefile"
    args = _pyinstaller_common(debug) + [
        "--onefile",
        "--name", APP_NAME,
        "--distpath", str(DIST),
        "--workpath", str(out / "work"),
        "--specpath", str(out / "work"),
        str(ENTRY),
    ]
    if _run(args) != 0:
        raise SystemExit("打包失败")
    target = DIST / f"{APP_NAME}.exe"
    print(f"\n单文件：{target}（{target.stat().st_size / 1024 / 1024:.1f} MB）")
    return target


# ---------------------------------------------------------------- 入口


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="音序管家打包脚本")
    parser.add_argument(
        "--target",
        choices=["all", "portable", "installer", "onefile"],
        default="all",
        help="all=便携版+安装版（默认）",
    )
    parser.add_argument("--debug", action="store_true", help="保留控制台窗口")
    parser.add_argument("--no-clean", action="store_true", help="保留构建缓存")
    args = parser.parse_args()

    if not args.no_clean:
        shutil.rmtree(BUILD / "portable" / "work", ignore_errors=True)

    print(f"{APP_NAME} v{VERSION} · 目标：{args.target}\n")

    if args.target == "onefile":
        build_onefile(args.debug)
        return 0

    archive: Path | None = None
    if args.target in ("all", "portable"):
        archive = build_portable(debug=args.debug)
    if args.target in ("all", "installer"):
        build_installer(debug=args.debug, archive=archive)

    print("\n" + "=" * 56)
    for path in sorted(DIST.glob(f"{PORTABLE_NAME}*")):
        size = path.stat().st_size / 1024 / 1024
        print(f"  {path.name:48s} {size:6.1f} MB")
    print("=" * 56)
    return 0


if __name__ == "__main__":
    sys.exit(main())
