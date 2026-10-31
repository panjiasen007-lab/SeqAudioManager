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


def _rgba(hex_color: str, alpha: float) -> str:
    """``#rrggbb`` → ``rgba(r,g,b,a)``，alpha 取 0~1。"""
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def _dark(
    name: str,
    accent: str,
    *,
    base: str = "#070a10",
    tint: str = "#131a26",
    accent_hover: str = "#6b93ff",
    cyan: str = "#22d3ee",
) -> Palette:
    """深色主题：星场可见，面板半透明。"""
    return Palette(
        name=name, light=False, base=base,
        panel=_rgba(tint, 0.90), card=_rgba(tint, 0.90), input=_rgba(tint, 0.94),
        hover=_rgba(tint, 0.72), active=_rgba(tint, 0.80),
        table=_rgba("#06080d", 0.96), log=_rgba("#030509", 0.90),
        line="#26303f", line_soft="#1a2130",
        fg="#e8ecf4", fg_dim="#8b95a8", fg_mute="#5a6478",
        accent=accent, accent_hover=accent_hover, accent_dim=_rgba(accent, 0.55),
        cyan=cyan, ok="#34d399", warn="#fbbf24", bad="#f87171", purple="#a78bfa",
    )


THEMES: dict[str, Palette] = {
    "深空": _dark("深空", "#4d7cfe", cyan="#22d3ee"),
    "春": _dark("春", "#34d399", tint="#0e1a14", cyan="#5eead4"),
    "夏": _dark("夏", "#fbbf24", tint="#1a170e", cyan="#fde68a"),
    "秋": _dark("秋", "#fb923c", tint="#1a120c", cyan="#fdba74"),
    "冬": _dark("冬", "#38bdf8", tint="#0d1620", cyan="#bae6fd"),
    "太极": _dark("太极", "#e5e7eb", base="#050505", tint="#111111",
                 accent_hover="#ffffff", cyan="#d1d5db"),
    "黑白极简": Palette(
        name="黑白极简", light=True, base="#f4f5f7",
        panel="#ffffff", card="#ffffff", input="#ffffff",
        hover="#e8eaee", active="#dfe3ea", table="#ffffff", log="#f7f8fa",
        line="#d4d9e0", line_soft="#e6e9ee",
        fg="#16181d", fg_dim="#4b5563", fg_mute="#8b94a3",
        accent="#16181d", accent_hover="#3a3f48", accent_dim="#c4c8cf",
        cyan="#0e7490", ok="#059669", warn="#b45309", bad="#dc2626", purple="#6d28d9",
    ),
}

CURRENT = "深空"

# ---------------------------------------------------------------- 字体（不随主题变）

UI_FONT = (
    '"Noto Sans SC", "Source Han Sans SC", "HarmonyOS Sans SC", "MiSans", '
    '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'
)
MONO_FONT = '"JetBrains Mono", "Sarasa Mono SC", "Cascadia Mono", Consolas, "Courier New", monospace'

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
    dim_border = p.line
    return f"""
/* ============================== 基础 ============================== */
QWidget {{
    background: transparent;
    color: {p.fg};
    font-family: {UI_FONT};
    font-size: 12px;
}}
QMainWindow {{ background: {p.base}; }}
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
    background: {p.accent}; border: 1px solid {p.accent}; color: #ffffff; font-weight: 600;
}}
QToolButton#accent:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QToolButton#accent:disabled {{ background: {p.accent_dim}; border-color: {p.accent_dim}; color: #93a6d4; }}

/* ============================== 按钮 ============================== */
QPushButton {{
    background: {p.card}; border: 1px solid {p.line}; border-radius: 7px;
    padding: 7px 15px; color: {p.fg};
}}
QPushButton:hover {{ background: {p.hover}; border-color: {dim_border}; }}
QPushButton:pressed {{ background: {p.active}; }}
QPushButton:disabled {{ color: {p.fg_mute}; background: transparent; border-color: {p.line_soft}; }}
QPushButton#primary {{
    background: {p.accent}; border: 1px solid {p.accent}; color: #ffffff;
    font-weight: 600; padding: 7px 18px;
}}
QPushButton#primary:hover {{ background: {p.accent_hover}; }}
QPushButton#primary:disabled {{ background: {p.accent_dim}; border-color: {p.accent_dim}; color: #8fa2cf; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {p.line}; color: {p.fg_dim}; }}
QPushButton#ghost:hover {{ border-color: {p.accent}; color: {p.fg}; background: {_rgba(p.accent, 0.10)}; }}
QPushButton#danger {{ color: {p.bad}; border-color: {_rgba(p.bad, 0.45)}; }}
QPushButton#danger:hover {{ border-color: {p.bad}; background: {_rgba(p.bad, 0.12)}; }}
QPushButton#dangerSolid {{
    background: {_rgba(p.bad, 0.92)}; border: 1px solid {_rgba(p.bad, 0.6)};
    color: #ffffff; font-weight: 600;
}}
QPushButton#dangerSolid:hover {{ background: {p.bad}; }}
QPushButton#dangerSolid:disabled {{ background: {_rgba(p.bad, 0.35)}; border-color: {_rgba(p.bad, 0.2)}; color: #c98f8f; }}

/* ============================== 容器 ============================== */
QFrame#card {{ background: {p.card}; border: 1px solid {p.line_soft}; border-radius: 10px; }}
QFrame#statCard {{ background: {p.card}; border: 1px solid {p.line_soft}; border-radius: 9px; }}
QFrame#statCard:hover {{ border-color: {dim_border}; }}
QFrame#brandBar {{ background: {_rgba(p.base, 0.72)}; border: none; border-bottom: 1px solid {p.line_soft}; }}
QFrame#panelBox {{ background: {p.panel}; border: 1px solid {p.line_soft}; border-radius: 12px; }}
QFrame#hLine {{ background: {p.line_soft}; border: none; }}

QLabel#brandTitle {{ color: {p.fg}; font-size: 16px; font-weight: 700; }}
QLabel#brandSub {{ color: {p.fg_mute}; font-size: 10px; }}
QLabel#statLabel {{ color: {p.fg_mute}; font-size: 11px; }}
QLabel#statValue {{ font-family: {MONO_FONT}; font-size: 21px; font-weight: 700; }}
QLabel#sectionTitle {{ color: {p.fg}; font-size: 12px; font-weight: 700; }}
QLabel#hint {{ color: {p.fg_mute}; font-size: 11px; }}
QLabel#mono {{ font-family: {MONO_FONT}; color: {p.fg_dim}; }}

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
    background: {_rgba(p.base, 0.86)}; color: {p.fg_dim}; border: none;
    border-bottom: 1px solid {p.line}; padding: 9px 8px; font-size: 11px; font-weight: 600;
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
QTextBrowser#licenseView {{ font-family: {MONO_FONT}; font-size: 11px; }}

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
    padding: 8px 10px; font-family: {MONO_FONT}; font-size: 11px; color: {p.fg_dim};
}}

/* ============================== Tab ============================== */
QTabWidget::pane {{ border: 1px solid {p.line_soft}; border-radius: 10px; background: {p.panel}; top: -1px; }}
QTabBar::tab {{ background: transparent; padding: 8px 16px; color: {p.fg_mute}; border: none; border-bottom: 2px solid transparent; }}
QTabBar::tab:hover {{ color: {p.fg_dim}; }}
QTabBar::tab:selected {{ color: {p.fg}; border-bottom-color: {p.accent}; }}
"""


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
