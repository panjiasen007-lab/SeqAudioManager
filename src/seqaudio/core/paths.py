"""路径与目录约定：一键绿色运行（优先读写 EXE 同级目录），不可写时回落到 AppData。"""

from __future__ import annotations

import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)


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


def _kept_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    return (Path(base) if base else Path(tempfile.gettempdir())) / "SeqAudioManager"


def _restore_kept_data(portable: Path) -> None:
    """把卸载时移走的数据搬回安装目录。

    卸载对话框承诺「保留数据，以后重装还能接着用」，但 data 是被挪到
    ``%APPDATA%/SeqAudioManager`` 的，而运行时只认安装目录下的 ``data/`` ——
    不搬回来，用户重装后看到的就是一份全新配置，备份也「不见」了。

    只在安装目录的 ``data/`` **原本不存在**时执行（即刚重装完、程序第一次
    启动），且必须存在 ``config.json`` 才认作可迁移的数据，避免把别处的
    残渣（例如绿色版与安装版并存）拖进来。复制而不是移动：万一迁移失败，
    ``%APPDATA%`` 那份还在。
    """
    source = _kept_data_dir()
    config = source / "config.json"
    if not config.exists():
        return
    try:
        for item in source.iterdir():
            destination = portable / item.name
            if item.is_dir():
                shutil.copytree(item, destination, dirs_exist_ok=True)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(item, destination)
        log.info("已把卸载时保留的数据恢复到 %s", portable)
    except OSError as exc:
        log.warning("恢复保留数据失败（继续用新配置）：%s", exc)


def data_dir() -> Path:
    """配置与缓存根目录。绿色优先：程序目录可写就用它，否则用 AppData。"""
    portable = app_dir() / "data"
    existed = portable.exists()
    if _writable(portable):
        if not existed:
            _restore_kept_data(portable)
        return portable
    fallback = _kept_data_dir()
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
