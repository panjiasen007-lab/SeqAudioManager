"""字体：注册随包的开源字体，并按可用性挑出最佳 UI 字体。

为什么必须在运行时注册
--------------------
Noto Sans SC 与 JetBrains Mono 都是随包分发的（``assets/fonts``），没有装到系统里。
QSS 里写 ``font-family: "Noto Sans SC"`` 只有在字体已注册后才会命中，否则会静默
回退到系统默认字体 —— 界面看起来"没生效"，其实只是没注册。

授权
----
* Noto Sans SC — SIL Open Font License 1.1
* JetBrains Mono — SIL Open Font License 1.1

两者都允许随软件分发与商用，不需要额外授权，唯一要求是保留授权声明
（见 ``assets/fonts/OFL.txt``）。这也是选它们而不是系统自带字体（微软雅黑等
不可随软件分发）的原因。

用户还可以把自己的字体丢进 ``data/fonts/``，启动时一并注册 —— 有些人对
中文字形有偏好（思源宋体 / 霞鹜文楷），留个口子比强迫接受一种字体好。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

from PySide6.QtGui import QFont, QFontDatabase

log = logging.getLogger(__name__)

#: UI 首选字体（按优先级）。全部为开源授权，前两个随包分发。
UI_CANDIDATES: tuple[str, ...] = (
    "Noto Sans SC",
    "Source Han Sans SC",
    "HarmonyOS Sans SC",
    "MiSans",
    "Alibaba PuHuiTi 3.0",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
)

#: 等宽首选（数据列用）。JetBrains Mono 随包。
MONO_CANDIDATES: tuple[str, ...] = (
    "JetBrains Mono",
    "Sarasa Mono SC",
    "Cascadia Mono",
    "Consolas",
    "Courier New",
)

_FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}

_loaded = False
_ui_family = ""
_mono_family = ""


def _font_dirs() -> list[Path]:
    from ..core import paths

    dirs: list[Path] = []
    bundled = paths.asset_path("fonts")
    if bundled is not None and bundled.is_dir():
        dirs.append(bundled)
    # 绿色版可能把资源放在 EXE 同级的 assets/ 下
    local = paths.app_dir() / "assets" / "fonts"
    if local.is_dir() and local not in dirs:
        dirs.append(local)
    user = paths.data_dir() / "fonts"
    if user.is_dir():
        dirs.append(user)
    return dirs


def _iter_font_files(directory: Path) -> Iterable[Path]:
    try:
        for entry in sorted(directory.iterdir()):
            if entry.suffix.lower() in _FONT_SUFFIXES and entry.is_file():
                yield entry
    except OSError as exc:
        log.debug("读取字体目录失败 %s：%s", directory, exc)


def load_application_fonts() -> dict[str, str]:
    """注册所有随包/用户字体，返回 {"ui": 家族名, "mono": 家族名}。"""
    global _loaded, _ui_family, _mono_family
    if _loaded:
        return {"ui": _ui_family, "mono": _mono_family}

    registered: list[str] = []
    for directory in _font_dirs():
        for font_file in _iter_font_files(directory):
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            if font_id < 0:
                log.warning("字体注册失败：%s", font_file.name)
                continue
            for family in QFontDatabase.applicationFontFamilies(font_id):
                registered.append(family)

    available = set(QFontDatabase.families())
    _ui_family = next((f for f in UI_CANDIDATES if f in available), "")
    _mono_family = next((f for f in MONO_CANDIDATES if f in available), "")

    log.info(
        "字体就绪：UI=%s 等宽=%s（注册 %d 个字族）",
        _ui_family or "系统默认",
        _mono_family or "系统默认",
        len(set(registered)),
    )
    _loaded = True
    return {"ui": _ui_family, "mono": _mono_family}


def ui_family() -> str:
    return _ui_family


def mono_family() -> str:
    return _mono_family


def ui_font(pixel_size: int = 13, bold: bool = False) -> QFont:
    """构造 UI 字体。家族为空时交给 Qt 自己挑（等同系统默认）。"""
    font = QFont(_ui_family) if _ui_family else QFont()
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    font.setHintingPreference(QFont.PreferFullHinting)
    return font


def mono_font(pixel_size: int = 12, bold: bool = False) -> QFont:
    """构造等宽字体。数据列纵向对齐靠它。"""
    font = QFont(_mono_family) if _mono_family else QFont()
    if not _mono_family:
        font.setStyleHint(QFont.Monospace)
    font.setPixelSize(pixel_size)
    font.setBold(bold)
    return font


def describe() -> str:
    """一行摘要，供「关于」对话框显示。"""
    from ..core import paths

    ui = _ui_family or "系统默认"
    mono = _mono_family or "系统默认"
    license_note = "SIL OFL 1.1" if _ui_family == "Noto Sans SC" else "系统字体"
    return f"{ui}（{license_note}） · 等宽 {mono} · 字体目录 {paths.asset_path('fonts') or '-'}"
