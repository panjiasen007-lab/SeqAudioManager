"""路径与目录约定：一键绿色运行（优先读写 EXE 同级目录），不可写时回落到 AppData。"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def app_dir() -> Path:
    """程序所在目录（打包后为 EXE 所在目录，开发期为仓库根目录）。"""
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[3]


def _writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        probe = directory / f".write_test_{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def asset_path(name: str) -> Path | None:
    """定位随包资源（图标等）。

    三种运行形态的路径完全不同，之前 app.py 与 widgets.py 各自拼路径，
    都少了一层目录导致图标一直加载失败：
      * 源码运行：<仓库根>/assets/
      * PyInstaller 打包：<_MEIPASS>/assets/
      * Nuitka/绿色版：<EXE 目录>/assets/
    """
    candidates: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "assets" / name)
    # core/paths.py -> core -> seqaudio -> src -> 仓库根
    candidates.append(Path(__file__).resolve().parents[3] / "assets" / name)
    candidates.append(app_dir() / "assets" / name)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def data_dir() -> Path:
    """配置与缓存根目录。绿色优先：程序目录可写就用它，否则用 AppData。"""
    portable = app_dir() / "data"
    if _writable(portable):
        return portable
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    fallback = (Path(base) if base else Path(tempfile.gettempdir())) / "SeqAudioManager"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def config_path() -> Path:
    return data_dir() / "config.json"


def presets_path() -> Path:
    return data_dir() / "presets.json"


def cache_dir() -> Path:
    d = data_dir() / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_dir() -> Path:
    d = data_dir() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def default_backup_dir() -> Path:
    return data_dir() / "backup"


def is_long_path_safe(path: Path) -> bool:
    """Windows 260 字符限制粗检（长路径需系统启用 LongPathsEnabled）。"""
    if os.name != "nt":
        return True
    return len(str(path)) < 255
