"""字体：注册随包的开源字体，并提供可选的界面/等宽字体。

为什么必须在运行时注册
--------------------
随包字体（``assets/fonts``）没有装到系统里。QSS 里写 ``font-family: "Noto Sans SC"``
只有在字体已注册后才会命中，否则会静默回退到系统默认字体 —— 界面看起来
"没生效"，其实只是没注册。

授权
----
* Noto Sans SC — SIL Open Font License 1.1
* 霞鹜文楷 LXGW WenKai — SIL Open Font License 1.1
* 得意黑 Smiley Sans — SIL Open Font License 1.1
* Inter — SIL Open Font License 1.1
* JetBrains Mono — SIL Open Font License 1.1

全部允许随软件分发与商用，唯一要求是保留授权声明（见 ``assets/fonts/OFL-*.txt``）。

用户还可以把自己的字体丢进 ``data/fonts/``，启动时一并注册。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtGui import QFont, QFontDatabase

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FontChoice:
    id: str
    name: str
    family: str
    category: str
    license: str = "SIL OFL 1.1"
    cjk: bool = True


#: 20 款随包开源字体：5 款中文字体 + 10 款西文界面字体 + 5 款等宽字体。
#: 西文字体不含中文字形，QSS/字体族会自动回退到 Noto Sans SC。
UI_CHOICES: tuple[FontChoice, ...] = (
    FontChoice("noto", "思源黑体 / Noto Sans SC", "Noto Sans SC", "中文无衬线"),
    FontChoice("wenkai", "霞鹜文楷", "LXGW WenKai", "中文楷体"),
    FontChoice("smiley", "得意黑", "Smiley Sans", "中文展示"),
    FontChoice("mashan", "马善政毛笔楷书", "Ma Shan Zheng", "中文书法"),
    FontChoice("zcool_qk", "站酷庆科黄油体", "ZCOOL QingKe HuangYou", "中文展示"),
    FontChoice("inter", "Inter", "Inter", "UI", cjk=False),
    FontChoice("plex_sans", "IBM Plex Sans", "IBM Plex Sans", "UI", cjk=False),
    FontChoice("source_sans", "Source Sans 3", "Source Sans 3", "UI", cjk=False),
    FontChoice("fira_sans", "Fira Sans", "Fira Sans", "UI", cjk=False),
    FontChoice("public_sans", "Public Sans", "Public Sans", "UI", cjk=False),
    FontChoice("manrope", "Manrope", "Manrope", "UI", cjk=False),
    FontChoice("work_sans", "Work Sans", "Work Sans", "UI", cjk=False),
    FontChoice("rubik", "Rubik", "Rubik", "UI", cjk=False),
    FontChoice("lato", "Lato", "Lato", "UI", cjk=False),
    FontChoice("noto_sans", "Noto Sans", "Noto Sans", "UI", cjk=False),
)

#: 可选的等宽字体（数据列/路径/规格）。
MONO_CHOICES: tuple[FontChoice, ...] = (
    FontChoice("jetbrains", "JetBrains Mono", "JetBrains Mono", "Mono", cjk=False),
    FontChoice("fira_code", "Fira Code", "Fira Code", "Mono ligatures", cjk=False),
    FontChoice("plex_mono", "IBM Plex Mono", "IBM Plex Mono", "Mono", cjk=False),
    FontChoice("source_code_pro", "Source Code Pro", "Source Code Pro", "Mono", cjk=False),
    FontChoice("cascadia", "Cascadia Code", "Cascadia Code", "Mono ligatures", cjk=False),
)

DEFAULT_UI = "noto"
DEFAULT_MONO = "jetbrains"
DEFAULT_SCALE = 1.0
MIN_SCALE = 0.85
MAX_SCALE = 1.35

_UI_FALLBACKS = (
    "Noto Sans SC",
    "Source Han Sans SC",
    "Microsoft YaHei UI",
    "Microsoft YaHei",
    "Segoe UI",
)
_MONO_FALLBACKS = ("Cascadia Mono", "Consolas", "Noto Sans SC")

_FONT_SUFFIXES = {".ttf", ".otf", ".ttc", ".otc"}

_loaded = False
_ui_choice: FontChoice = UI_CHOICES[0]
_mono_choice: FontChoice = MONO_CHOICES[0]
_scale = DEFAULT_SCALE


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
    global _loaded
    if _loaded:
        return {"ui": _ui_choice.family, "mono": _mono_choice.family}

    registered: list[str] = []
    for directory in _font_dirs():
        for font_file in _iter_font_files(directory):
            font_id = QFontDatabase.addApplicationFont(str(font_file))
            if font_id < 0:
                log.warning("字体注册失败：%s", font_file.name)
                continue
            for family in QFontDatabase.applicationFontFamilies(font_id):
                registered.append(family)

    log.info(
        "字体就绪：UI=%s 等宽=%s（注册 %d 个字族）",
        _ui_choice.family,
        _mono_choice.family,
        len(set(registered)),
    )
    _loaded = True
    return {"ui": _ui_choice.family, "mono": _mono_choice.family}


def _choice_or_default(choices: tuple[FontChoice, ...], font_id: str) -> FontChoice:
    for choice in choices:
        if choice.id == font_id:
            return choice
    return choices[0]


def configure(ui_id: str = DEFAULT_UI, mono_id: str = DEFAULT_MONO, scale: float = DEFAULT_SCALE) -> None:
    """选择字体与字号倍率（由设置加载后调用）。"""
    global _ui_choice, _mono_choice, _scale
    _ui_choice = _choice_or_default(UI_CHOICES, ui_id)
    _mono_choice = _choice_or_default(MONO_CHOICES, mono_id)
    try:
        _scale = min(MAX_SCALE, max(MIN_SCALE, float(scale)))
    except (TypeError, ValueError):
        _scale = DEFAULT_SCALE


def current_ui_id() -> str:
    return _ui_choice.id


def current_mono_id() -> str:
    return _mono_choice.id


def current_scale() -> float:
    return _scale


def scale_px(pixel_size: int) -> int:
    return max(8, round(pixel_size * _scale))


def _quote_family(family: str) -> str:
    return f'"{family}"' if " " in family else family


def _stack(primary: str, fallbacks: Iterable[str]) -> str:
    families = [primary]
    for family in fallbacks:
        if family and family != primary:
            families.append(family)
    families.append("sans-serif" if primary != "JetBrains Mono" else "monospace")
    return ", ".join(_quote_family(f) for f in families)


def ui_css() -> str:
    """QSS 用的界面字体族（带中文/系统回退）。"""
    return _stack(_ui_choice.family, _UI_FALLBACKS)


def mono_css() -> str:
    """QSS 用的数据/等宽字体族。"""
    return _stack(_mono_choice.family, _MONO_FALLBACKS)


def ui_family() -> str:
    return _ui_choice.family


def mono_family() -> str:
    return _mono_choice.family


def ui_font(pixel_size: int = 13, bold: bool = False) -> QFont:
    """构造 UI 字体。"""
    font = QFont(_ui_choice.family)
    font.setFamilies([_ui_choice.family, *_UI_FALLBACKS])
    font.setPixelSize(scale_px(pixel_size))
    font.setBold(bold)
    font.setHintingPreference(QFont.PreferFullHinting)
    return font


def mono_font(pixel_size: int = 12, bold: bool = False) -> QFont:
    """构造等宽字体。数据列纵向对齐靠它。"""
    font = QFont(_mono_choice.family)
    font.setFamilies([_mono_choice.family, *_MONO_FALLBACKS])
    font.setStyleHint(QFont.Monospace)
    font.setPixelSize(scale_px(pixel_size))
    font.setBold(bold)
    return font


def describe() -> str:
    """一行摘要，供「关于」对话框显示。"""
    from ..core import paths
    from . import i18n

    return i18n.tr_format(
        "{name}（{license}） · 等宽 {mono} · 字号 {scale} · 字体目录 {path}",
        name=i18n.tr_text(_ui_choice.name),
        license=_ui_choice.license,
        mono=i18n.tr_text(_mono_choice.name),
        scale=f"{_scale:.0%}",
        path=paths.asset_path("fonts") or "-",
    )
