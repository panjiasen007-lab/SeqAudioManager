"""视觉主题：可一键切换的调色板系统。

设计原则
--------
1. **所有颜色都从 Palette 取**，QSS 由 ``build_qss()`` 按当前调色板现算 ——
   切换主题就是换一套调色板 + 重刷样式表 + 重绘，不再有"改一个颜色要动十几处"的问题。
2. **星空是深色主题的一部分**：深色主题叠在星场之上（面板半透明）；浅色主题
   （黑白极简）背景改为纯色，星场自动关闭。
3. 强调色只用于主按钮与选中态；状态色只出现在胶囊与数字上。

主题清单
--------
深空（默认，蓝）、春（绿）、夏（暖黄）、秋（橙）、冬（冰蓝）、
黑白极简（浅色）、太极（黑白高对比深色）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------- 调色板


@dataclass(frozen=True)
class Palette:
    name: str
    light: bool
    base: str                 # 窗口底
    panel: str                # 面板（半透明）
    card: str                 # 卡片
    input: str                # 输入框
    hover: str                # 悬浮
    active: str               # 按下
    table: str                # 表格
    log: str                  # 日志
    line: str                 # 主分隔线
    line_soft: str            # 次分隔
    fg: str
    fg_dim: str
    fg_mute: str
    accent: str
    accent_hover: str
    accent_dim: str
    cyan: str
    ok: str
    warn: str
    bad: str
    purple: str
    #: 主强调色上的文字色（亮色按钮用深字，深色按钮用白字）
    on_accent: str = "#ffffff"
    #: 背景笔触：stars / vinyl / blossom / sun / leaves / snow / waves /
    #: dusk / taiji / paper / grid
    pattern: str = "stars"


def _rgba(hex_color: str, alpha: float) -> str:
    """``#rrggbb`` → ``rgba(r,g,b,a)``，alpha 取 0~1。"""
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _solid(color: str) -> str:
    """把 ``rgba(...)`` 还原成不透明 ``#rrggbb``（QSS 渐变需要可混合的实色）。"""
    if color.startswith("rgba("):
        parts = color[5:-1].split(",")
        return "#{:02x}{:02x}{:02x}".format(
            int(float(parts[0])), int(float(parts[1])), int(float(parts[2]))
        )
    return color


def _readable(color: str, *, dark: str = "#111318", light: str = "#ffffff") -> str:
    """按背景色亮度选白字或深字，保证按钮文字对比度 ≥ 4.5。"""
    value = _solid(color).lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    if len(value) < 6:
        return light
    rgb = [int(value[index * 2 : index * 2 + 2], 16) / 255 for index in range(3)]
    rgb = [
        channel / 12.92
        if channel <= 0.03928
        else ((channel + 0.055) / 1.055) ** 2.4
        for channel in rgb
    ]
    luminance = 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]
    white_contrast = 1.05 / (luminance + 0.05)
    return light if white_contrast >= 4.5 else dark


def _mix(color_a: str, color_b: str, weight_b: float) -> str:
    """两色混合，``weight_b`` 是 color_b 的占比（0~1）。"""
    va = color_a.lstrip("#")
    vb = color_b.lstrip("#")
    channels = []
    for index in range(3):
        a = int(va[index * 2 : index * 2 + 2], 16)
        b = int(vb[index * 2 : index * 2 + 2], 16)
        channels.append(round(a * (1 - weight_b) + b * weight_b))
    return "#{:02x}{:02x}{:02x}".format(*channels)


def _dark(
    name: str,
    accent: str,
    *,
    base: str,
    tint: str,
    accent_hover: str,
    cyan: str,
    on_accent: str = "#ffffff",
    pattern: str = "stars",
    fg: str = "#e9edf6",
    fg_dim: str = "#8d97aa",
    fg_mute: str = "#5d677b",
    line: str = "#273244",
    line_soft: str = "#1a2231",
    ok: str = "#3ddc97",
    warn: str = "#f6c85f",
    bad: str = "#ff7a8a",
    purple: str = "#b39dff",
) -> Palette:
    """深色主题：背景笔触可见，面板半透明。"""
    return Palette(
        name=name, light=False, base=base,
        panel=_rgba(tint, 0.82), card=_rgba(tint, 0.78), input=_rgba(tint, 0.90),
        hover=_rgba(tint, 0.66), active=_rgba(tint, 0.74),
        table=_rgba(_mix(base, "#000000", 0.28), 0.90),
        log=_rgba(_mix(base, "#000000", 0.46), 0.86),
        line=line, line_soft=line_soft,
        fg=fg, fg_dim=fg_dim, fg_mute=fg_mute,
        accent=accent, accent_hover=accent_hover, accent_dim=_rgba(accent, 0.50),
        cyan=cyan, ok=ok, warn=warn, bad=bad, purple=purple,
        on_accent=on_accent, pattern=pattern,
    )


THEMES: dict[str, Palette] = {
    "深空": _dark(
        "深空", "#6aa8ff", base="#060b18", tint="#0e1a33",
        accent_hover="#9cc5ff", cyan="#45e0cf", on_accent="#061020",
        pattern="stars", fg="#e9effb", fg_dim="#8e9ab4", fg_mute="#5d6882",
        line="#2a3852", line_soft="#1b2436",
    ),
    "黑胶": _dark(
        "黑胶", "#e6a23c", base="#100b08", tint="#24170f",
        accent_hover="#f7c46c", cyan="#d98a5c", on_accent="#1a0f07",
        pattern="vinyl", fg="#f2e9dd", fg_dim="#a8957f", fg_mute="#6e5b49",
        line="#3a2a1c", line_soft="#271b12",
        ok="#8fbf6a", warn="#e6a23c", bad="#e06b5b", purple="#c79b6b",
    ),
    "春": _dark(
        "春", "#5fd39a", base="#07140f", tint="#10271c",
        accent_hover="#8ce8b7", cyan="#7fe0d0", on_accent="#06150f",
        pattern="blossom", fg="#e6f6ee", fg_dim="#8fb5a3", fg_mute="#5d7d6c",
        line="#244536", line_soft="#173026",
        ok="#5fd39a", warn="#f0c987", bad="#ef7f8f", purple="#f2a7c8",
    ),
    "夏": _dark(
        "夏", "#ffb347", base="#181006", tint="#2c1d0b",
        accent_hover="#ffd27a", cyan="#55d6c2", on_accent="#2a1806",
        pattern="sun", fg="#fff3e2", fg_dim="#bda98e", fg_mute="#826d52",
        line="#4a351c", line_soft="#33240f",
        ok="#7fd08a", warn="#ffb347", bad="#ff7a5c", purple="#ff9f68",
    ),
    "秋": _dark(
        "秋", "#e07a3f", base="#160d08", tint="#2b180e",
        accent_hover="#f2a06b", cyan="#d6b36f", on_accent="#1b0d06",
        pattern="leaves", fg="#f7e9df", fg_dim="#b89a86", fg_mute="#7d6352",
        line="#46301f", line_soft="#2e1e13",
        ok="#91b96a", warn="#e0a43f", bad="#df6a5a", purple="#c9855f",
    ),
    "冬": _dark(
        "冬", "#8fd4ff", base="#050e19", tint="#0b2034",
        accent_hover="#bde8ff", cyan="#d4f4ff", on_accent="#061421",
        pattern="snow", fg="#e9f5ff", fg_dim="#8faabe", fg_mute="#5b7285",
        line="#26445c", line_soft="#182d40",
        ok="#77d6bf", warn="#f2cf7a", bad="#ff8595", purple="#b7b3ff",
    ),
    "海岸": _dark(
        "海岸", "#35d6c4", base="#04141a", tint="#0a2a32",
        accent_hover="#6be9da", cyan="#ffd29d", on_accent="#042023",
        pattern="waves", fg="#e6fbf8", fg_dim="#8db8b6", fg_mute="#5b7f7e",
        line="#20494c", line_soft="#123033",
        ok="#35d6c4", warn="#ffc46b", bad="#ff7f7f", purple="#8bd3ff",
    ),
    "暮光": _dark(
        "暮光", "#c084fc", base="#0c0717", tint="#1d1131",
        accent_hover="#d9aaff", cyan="#f0a6d8", on_accent="#160a2a",
        pattern="dusk", fg="#f2e9ff", fg_dim="#a795bd", fg_mute="#6f5f86",
        line="#3a2853", line_soft="#271a3a",
        ok="#8dd7b0", warn="#f0c87a", bad="#ff7f9c", purple="#c084fc",
    ),
    "太极": _dark(
        "太极", "#f2f2f2", base="#050505", tint="#141414",
        accent_hover="#ffffff", cyan="#bdbdbd", on_accent="#0a0a0a",
        pattern="taiji", fg="#f4f4f4", fg_dim="#a8a8a8", fg_mute="#6e6e6e",
        line="#3a3a3a", line_soft="#242424",
        ok="#9ccf9c", warn="#d9c27a", bad="#d98a8a", purple="#b0a8c0",
    ),
    "纸白": Palette(
        name="纸白", light=True, base="#efe7d8",
        panel="#f8f2e6", card="#fffbf1", input="#fffdf7",
        hover="#e9dfcf", active="#e0d4c2", table="#fcf7ec", log="#f4eddf",
        line="#cdbda7", line_soft="#ddd1bf",
        fg="#2b241a", fg_dim="#6a5e50", fg_mute="#7a6e62",
        accent="#9a5b2c", accent_hover="#7c4520", accent_dim="#e6c6a8",
        cyan="#3f7d78", ok="#3f7d4b", warn="#a86a1f", bad="#b03a2e", purple="#7b4fa3",
        on_accent="#fff8ee", pattern="paper",
    ),
    "黑白极简": Palette(
        name="黑白极简", light=True, base="#f1f2f5",
        panel="#ffffff", card="#ffffff", input="#ffffff",
        hover="#e7e9ee", active="#dde0e6", table="#ffffff", log="#f7f8fa",
        line="#cfd3db", line_soft="#e2e5ea",
        fg="#111318", fg_dim="#4c515b", fg_mute="#6e737d",
        accent="#111318", accent_hover="#343945", accent_dim="#c9ccd4",
        cyan="#167d8d", ok="#16784a", warn="#a35f00", bad="#b3261e", purple="#6b4fa0",
        on_accent="#ffffff", pattern="grid",
    ),
}

CURRENT = "深空"

# ---------------------------------------------------------------- 字体（不随主题变，由 ui/fonts 动态提供）

# ---------------------------------------------------------------- QSS 生成


def _status_colors(p: Palette) -> dict[str, tuple[str, str]]:
    return {
        "完成": (p.ok, _rgba(p.ok, 0.14)),
        "失败": (p.bad, _rgba(p.bad, 0.14)),
        "待人工确认": (p.warn, _rgba(p.warn, 0.14)),
        "不支持": (p.fg_mute, _rgba(p.fg_mute, 0.16)),
        "已跳过": (p.fg_mute, _rgba(p.fg_mute, 0.16)),
        "已匹配": (p.accent, _rgba(p.accent, 0.16)),
        "处理中": (p.purple, _rgba(p.purple, 0.16)),
        "读取中": (p.purple, _rgba(p.purple, 0.16)),
        "写入中": (p.purple, _rgba(p.purple, 0.16)),
        "待处理": (p.fg_dim, _rgba(p.fg_dim, 0.12)),
    }


def build_qss(p: Palette) -> str:
    from . import fonts

    dim_border = p.line
    ui_font = fonts.ui_css()
    mono_font = fonts.mono_css()
    qss = f"""
/* ============================== 基础 ============================== */
QWidget {{
    background: transparent;
    color: {p.fg};
    font-family: {ui_font};
    font-size: 12px;
}}
QMainWindow {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {_mix(p.base, p.accent, 0.10)},
        stop:0.42 {p.base},
        stop:1 {_mix(p.base, p.accent, 0.04)});
}}
QDialog {{ background: {p.base}; }}

QToolTip {{
    background: {p.panel};
    color: {p.fg};
    border: 1px solid {p.line};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ============================== 工具栏 ============================== */
QToolBar {{ background: transparent; border: none; padding: 0; spacing: 6px; }}
QToolBar::separator {{ background: {p.line}; width: 1px; margin: 8px 10px; }}

QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 7px;
    padding: 7px 13px; color: {p.fg_dim}; font-size: 12px;
}}
QToolButton:hover {{ background: {p.hover}; border-color: {p.line}; color: {p.fg}; }}
QToolButton:pressed {{ background: {p.active}; }}
QToolButton:disabled {{ color: {p.fg_mute}; background: transparent; border-color: transparent; }}
QToolButton#accent {{
    background: {p.accent}; border: 1px solid {p.accent};
    color: {p.on_accent}; font-weight: 600;
}}
QToolButton#accent:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QToolButton#accent:disabled {{
    background: {p.accent_dim}; border-color: {p.accent_dim};
    color: {_mix(p.on_accent, p.base, 0.55)};
}}

/* ============================== 按钮 ============================== */
QPushButton {{
    background: {p.card}; border: 1px solid {p.line}; border-radius: 7px;
    padding: 7px 15px; color: {p.fg};
}}
QPushButton:hover {{ background: {p.hover}; border-color: {dim_border}; }}
QPushButton:pressed {{ background: {p.active}; }}
QPushButton:disabled {{ color: {p.fg_mute}; background: transparent; border-color: {p.line_soft}; }}
QPushButton#primary {{
    background: {p.accent}; border: 1px solid {p.accent}; color: {p.on_accent};
    font-weight: 600; padding: 7px 18px;
}}
QPushButton#primary:hover {{ background: {p.accent_hover}; }}
QPushButton#primary:disabled {{
    background: {p.accent_dim}; border-color: {p.accent_dim};
    color: {_mix(p.on_accent, p.base, 0.55)};
}}
QPushButton#ghost {{ background: transparent; border: 1px solid {p.line}; color: {p.fg_dim}; }}
QPushButton#ghost:hover {{ border-color: {p.accent}; color: {p.fg}; background: {_rgba(p.accent, 0.10)}; }}
QPushButton#danger {{ color: {p.bad}; border-color: {_rgba(p.bad, 0.45)}; }}
QPushButton#danger:hover {{ border-color: {p.bad}; background: {_rgba(p.bad, 0.12)}; }}
QPushButton#dangerSolid {{
    background: {_rgba(p.bad, 0.92)}; border: 1px solid {_rgba(p.bad, 0.6)};
    color: {_readable(p.bad)}; font-weight: 600;
}}
QPushButton#dangerSolid:hover {{ background: {p.bad}; }}
QPushButton#dangerSolid:disabled {{
    background: {_rgba(p.bad, 0.35)}; border-color: {_rgba(p.bad, 0.2)};
    color: {p.fg_mute};
}}
QPushButton#success {{
    background: {_rgba(p.ok, 0.90)}; border: 1px solid {_rgba(p.ok, 0.55)};
    color: {_readable(p.ok)}; font-weight: 600;
}}
QPushButton#success:hover {{ background: {p.ok}; }}
QPushButton#success:disabled {{
    background: {_rgba(p.ok, 0.35)}; border-color: {_rgba(p.ok, 0.2)};
    color: {p.fg_mute};
}}
QPushButton#chip {{
    background: transparent; border: 1px solid {p.line}; border-radius: 14px;
    padding: 3px 12px; color: {p.fg_dim}; font-size: 11px;
}}
QPushButton#chip:hover {{ border-color: {p.accent}; color: {p.fg}; }}
QPushButton#chip:checked {{
    background: {_rgba(p.accent, 0.16)}; border-color: {p.accent};
    color: {p.fg}; font-weight: 600;
}}

/* ============================== 容器 ============================== */
QFrame#card {{ background: {p.card}; border: 1px solid {p.line_soft}; border-radius: 10px; }}
QFrame#statCard {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
        stop:0 {p.card}, stop:1 {_mix(_solid(p.card), p.accent, 0.10)});
    border: 1px solid {p.line_soft}; border-radius: 9px;
}}
QFrame#statCard:hover {{ border-color: {_rgba(p.accent, 0.45)}; }}
QFrame#selectionBar {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {_rgba(p.accent, 0.16)}, stop:1 {_rgba(p.cyan, 0.08)});
    border: 1px solid {_rgba(p.accent, 0.34)}; border-radius: 9px;
}}
QFrame#brandBar {{
    background: {_rgba(p.base, 0.55)};
    border: none; border-bottom: 1px solid {_rgba(p.accent, 0.28)};
}}
QFrame#panelBox {{ background: {p.panel}; border: 1px solid {p.line_soft}; border-radius: 12px; }}
QFrame#hLine {{ background: {p.line_soft}; border: none; }}

QLabel#brandTitle {{ color: {p.fg}; font-size: 16px; font-weight: 700; }}
QLabel#brandSub {{ color: {p.fg_mute}; font-size: 10px; }}
QLabel#statLabel {{ color: {p.fg_mute}; font-size: 11px; }}
QLabel#statValue {{ font-family: {mono_font}; font-size: 21px; font-weight: 700; }}
QLabel#sectionTitle {{ color: {p.fg}; font-size: 12px; font-weight: 700; }}
QLabel#hint {{ color: {p.fg_mute}; font-size: 11px; }}
QLabel#strong {{ color: {p.fg}; font-weight: 600; }}
QLabel#warnHint {{ color: {p.warn}; font-size: 11px; }}
QLabel#mono {{ font-family: {mono_font}; color: {p.fg_dim}; }}
QLabel#fontPreview {{
    padding: 8px 10px; border: 1px solid {p.line_soft}; border-radius: 8px;
    color: {p.fg_dim};
}}
QLabel#monoPreview {{
    padding: 6px 10px; border: 1px solid {p.line_soft}; border-radius: 8px;
    color: {p.fg_mute};
}}
QFrame#vLine {{ background: {p.line_soft}; border: none; }}
QPushButton#smallGhost {{
    background: transparent; border: 1px solid {p.line_soft}; border-radius: 6px;
    color: {p.fg_mute}; padding: 1px 10px; font-size: 11px;
}}
QPushButton#smallGhost:hover {{ color: {p.fg}; border-color: {p.line}; }}

/* ============================== 表格 ============================== */
QTableView, QTableWidget {{
    background: {p.table}; alternate-background-color: {_rgba(p.base, 0.55)};
    border: 1px solid {p.line_soft}; border-radius: 10px;
    selection-background-color: {_rgba(p.accent, 0.20)}; selection-color: {p.fg};
    outline: none; gridline-color: transparent;
}}
QTableView::item {{ padding: 2px 8px; border: none; }}
QTableView::item:selected {{ background: {_rgba(p.accent, 0.20)}; color: {p.fg}; }}
QTableView::item:hover {{ background: {_rgba(p.fg, 0.04)}; }}
QTableView::item:selected:hover {{ background: {_rgba(p.accent, 0.26)}; }}

QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {_rgba(p.base, 0.78)}; color: {p.fg_dim}; border: none;
    border-bottom: 1px solid {_rgba(p.accent, 0.30)};
    padding: 9px 8px; font-size: 11px; font-weight: 600;
}}
QHeaderView::section:hover {{ color: {p.fg}; background: {p.hover}; }}
QTableCornerButton::section {{ background: {_rgba(p.base, 0.86)}; border: none; }}

/* ============================== 输入 ============================== */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {p.input}; border: 1px solid {p.line}; border-radius: 7px;
    padding: 6px 9px; color: {p.fg};
    selection-background-color: {p.accent_dim}; selection-color: {p.fg};
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: {dim_border}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {p.accent};
}}
QLineEdit::placeholder {{ color: {p.fg_mute}; }}
QLineEdit#searchInput {{
    background: {p.input}; border: 1px solid {p.line}; border-radius: 8px;
    padding-left: 30px; padding-right: 8px; color: {p.fg};
}}
QLineEdit#searchInput:focus {{ border-color: {p.accent}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{
    image: none; border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid {p.fg_mute}; margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {p.panel}; border: 1px solid {p.line}; border-radius: 7px; padding: 4px;
    selection-background-color: {p.active}; outline: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{ background: transparent; border: none; width: 16px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none; border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-bottom: 4px solid {p.fg_mute};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none; border-left: 3px solid transparent; border-right: 3px solid transparent;
    border-top: 4px solid {p.fg_mute};
}}

QTextBrowser {{
    background: {p.card}; border: 1px solid {p.line_soft}; border-radius: 8px;
    padding: 6px; color: {p.fg_dim};
}}
QTextBrowser#licenseView {{ font-family: {mono_font}; font-size: 11px; }}

/* ============================== 勾选 / 单选 ============================== */
QCheckBox {{ spacing: 8px; color: {p.fg_dim}; background: transparent; }}
QCheckBox:hover {{ color: {p.fg}; }}
QCheckBox::indicator {{
    width: 15px; height: 15px; border: 1px solid {p.line}; border-radius: 4px; background: {p.input};
}}
QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
QCheckBox::indicator:checked {{ background: {p.accent}; border-color: {p.accent}; image: none; }}

QRadioButton {{ spacing: 9px; color: {p.fg_dim}; background: transparent; padding: 3px 0; }}
QRadioButton:hover {{ color: {p.fg}; }}
QRadioButton::indicator {{
    width: 15px; height: 15px; border: 1px solid {p.line}; border-radius: 8px; background: {p.input};
}}
QRadioButton::indicator:hover {{ border-color: {p.accent}; }}
QRadioButton::indicator:checked {{ border: 1px solid {p.accent}; background: {p.accent}; }}

/* ============================== 分组 ============================== */
QGroupBox {{
    background: {p.panel}; border: 1px solid {p.line_soft}; border-radius: 10px;
    margin-top: 16px; padding: 14px 12px 12px 12px; font-size: 12px; font-weight: 700; color: {p.fg};
}}
QGroupBox::title {{
    subcontrol-origin: margin; subcontrol-position: top left; left: 12px; padding: 0 6px;
    color: {p.fg_dim}; background: transparent;
}}

/* ============================== 进度 ============================== */
QProgressBar {{ background: {p.card}; border: none; border-radius: 2px; height: 4px; text-align: center; color: transparent; }}
QProgressBar::chunk {{
    border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 {p.accent}, stop:1 {p.cyan});
}}

/* ============================== 状态栏 ============================== */
QStatusBar {{ background: {_rgba(p.base, 0.72)}; border-top: 1px solid {p.line_soft}; color: {p.fg_mute}; font-size: 11px; }}
QStatusBar::item {{ border: none; }}

/* ============================== 分割器 ============================== */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:hover {{ background: {p.line_soft}; }}

/* ============================== 滚动条 ============================== */
QScrollBar:vertical {{ background: transparent; width: 9px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {_rgba(p.fg, 0.20)}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:vertical:hover {{ background: {_rgba(p.fg, 0.30)}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ background: transparent; height: 9px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {_rgba(p.fg, 0.20)}; border-radius: 4px; min-width: 28px; }}
QScrollBar::handle:horizontal:hover {{ background: {_rgba(p.fg, 0.30)}; }}

/* ============================== 菜单 ============================== */
QMenu {{ background: {p.panel}; border: 1px solid {p.line}; border-radius: 9px; padding: 6px; }}
QMenu::item {{ padding: 7px 24px 7px 14px; border-radius: 6px; color: {p.fg_dim}; }}
QMenu::item:selected {{ background: {p.active}; color: {p.fg}; }}
QMenu::item:disabled {{ color: {p.fg_mute}; }}
QMenu::separator {{ height: 1px; background: {p.line_soft}; margin: 5px 8px; }}
QMenu::indicator {{ width: 13px; height: 13px; margin-left: 6px; }}

/* ============================== 日志 ============================== */
QPlainTextEdit#logView {{
    background: {p.log}; border: 1px solid {p.line_soft}; border-radius: 10px;
    padding: 8px 10px; font-family: {mono_font}; font-size: 11px; color: {p.fg_dim};
}}

/* ============================== Tab ============================== */
QTabWidget::pane {{
    border: 1px solid {_rgba(p.accent, 0.22)}; border-radius: 10px;
    background: {p.panel}; top: -1px;
}}
QTabBar::tab {{ background: transparent; padding: 8px 16px; color: {p.fg_mute}; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {p.fg_dim}; }}
QTabBar::tab:selected {{ color: {p.fg}; border-bottom-color: {p.accent}; }}
"""
    # 字号倍率：QSS 里所有 font-size:Npx 统一缩放，避免逐条改模板
    return re.sub(
        r"font-size:\s*(\d+)px",
        lambda m: f"font-size: {fonts.scale_px(int(m.group(1)))}px",
        qss,
    )


# ---------------------------------------------------------------- 运行时应用


def is_light(name: str) -> bool:
    palette = THEMES.get(name)
    return bool(palette and palette.light)


def apply_theme(name: str) -> str:
    """切换主题：更新全局色值并返回新的 QSS。"""
    global CURRENT, BG_BASE, BG_PANEL, BG_CARD, BG_HOVER, BG_ACTIVE
    global LINE, LINE_SOFT, FG, FG_DIM, FG_MUTE
    global ACCENT, ACCENT_HOVER, ACCENT_DIM, CYAN, OK, WARN, BAD, PURPLE
    global SURFACE_PANEL, SURFACE_CARD, SURFACE_INPUT, SURFACE_HOVER, SURFACE_ACTIVE
    global SURFACE_TABLE, SURFACE_LOG, SURFACE_SOLID, STATUS_COLORS, QSS

    palette = THEMES.get(name)
    if palette is None:
        return build_qss(THEMES[CURRENT])

    CURRENT = name
    BG_BASE = palette.base
    BG_PANEL = SURFACE_PANEL = palette.panel
    BG_CARD = SURFACE_CARD = palette.card
    SURFACE_INPUT = palette.input
    BG_HOVER = SURFACE_HOVER = palette.hover
    BG_ACTIVE = SURFACE_ACTIVE = palette.active
    SURFACE_TABLE = palette.table
    SURFACE_LOG = palette.log
    SURFACE_SOLID = palette.panel
    LINE = palette.line
    LINE_SOFT = palette.line_soft
    FG = palette.fg
    FG_DIM = palette.fg_dim
    FG_MUTE = palette.fg_mute
    ACCENT = palette.accent
    ACCENT_HOVER = palette.accent_hover
    ACCENT_DIM = palette.accent_dim
    CYAN = palette.cyan
    OK = palette.ok
    WARN = palette.warn
    BAD = palette.bad
    PURPLE = palette.purple
    STATUS_COLORS = _status_colors(palette)
    QSS = build_qss(palette)
    return QSS


# ---------------------------------------------------------------- 初始状态（默认深空）

_dark = THEMES["深空"]
BG_BASE = _dark.base
BG_PANEL = SURFACE_PANEL = _dark.panel
BG_CARD = SURFACE_CARD = _dark.card
SURFACE_INPUT = _dark.input
BG_HOVER = SURFACE_HOVER = _dark.hover
BG_ACTIVE = SURFACE_ACTIVE = _dark.active
SURFACE_TABLE = _dark.table
SURFACE_LOG = _dark.log
SURFACE_SOLID = _dark.panel
LINE = _dark.line
LINE_SOFT = _dark.line_soft
FG = _dark.fg
FG_DIM = _dark.fg_dim
FG_MUTE = _dark.fg_mute
ACCENT = _dark.accent
ACCENT_HOVER = _dark.accent_hover
ACCENT_DIM = _dark.accent_dim
CYAN = _dark.cyan
OK = _dark.ok
WARN = _dark.warn
BAD = _dark.bad
PURPLE = _dark.purple
STATUS_COLORS = _status_colors(_dark)
QSS = build_qss(_dark)
