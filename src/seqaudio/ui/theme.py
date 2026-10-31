"""视觉主题：深色分层 + 冷调强调色 + 精确留白。

设计原则
--------
1. **分层而非平铺**：窗口底 / 面板 / 卡片 / 悬浮，四层色阶各自拉开，
   靠 1px 细边框而非重描边划分区域，界面才有"深度"。
2. **强调色只用于一处**：主操作按钮与选中态统一用 #4d7cfe，
   状态色（成功/警告/失败）只出现在状态胶囊与数字上，避免色彩打架。
3. **数字用等宽**：码率、采样率、置信度、时长这类数据列用等宽字体，
   纵向对齐后才有"仪表盘"的感觉。
4. 不使用任何图片资源，全部 QSS + QPainter，保证 DPI 缩放不失真。
"""

from __future__ import annotations

# ---------------------------------------------------------------- 色板

BG_BASE = "#0a0c10"      # 窗口底
BG_PANEL = "#0f1218"     # 面板/工具栏
BG_CARD = "#151922"      # 卡片/输入框
BG_HOVER = "#1c2130"     # 悬浮
BG_ACTIVE = "#232a3a"    # 按下

LINE = "#232a38"         # 主分隔线
LINE_SOFT = "#1a1f2b"    # 次级分隔

FG = "#e8ecf4"
FG_DIM = "#8b95a8"
FG_MUTE = "#5a6478"

ACCENT = "#4d7cfe"
ACCENT_HOVER = "#6b93ff"
ACCENT_DIM = "#2f4a94"

CYAN = "#22d3ee"
OK = "#34d399"
WARN = "#fbbf24"
BAD = "#f87171"
PURPLE = "#a78bfa"

#: 表格状态色（与 delegate 共用）
STATUS_COLORS = {
    "完成": (OK, "rgba(52,211,153,0.14)"),
    "失败": (BAD, "rgba(248,113,113,0.14)"),
    "待人工确认": (WARN, "rgba(251,191,36,0.14)"),
    "不支持": (FG_MUTE, "rgba(90,100,120,0.16)"),
    "已跳过": (FG_MUTE, "rgba(90,100,120,0.16)"),
    "已匹配": (ACCENT, "rgba(77,124,254,0.16)"),
    "处理中": (PURPLE, "rgba(167,139,250,0.16)"),
    "读取中": (PURPLE, "rgba(167,139,250,0.16)"),
    "写入中": (PURPLE, "rgba(167,139,250,0.16)"),
    "待处理": (FG_DIM, "rgba(139,149,168,0.12)"),
}

UI_FONT = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI Variable Display", "Segoe UI", sans-serif'
MONO_FONT = '"Cascadia Mono", "JetBrains Mono", Consolas, "Courier New", monospace'

QSS = f"""
/* ============================== 基础 ============================== */
QWidget {{
    background: {BG_BASE};
    color: {FG};
    font-family: {UI_FONT};
    font-size: 12px;
}}
QMainWindow, QDialog {{ background: {BG_BASE}; }}

QToolTip {{
    background: {BG_HOVER};
    color: {FG};
    border: 1px solid {LINE};
    border-radius: 6px;
    padding: 6px 8px;
}}

/* ============================== 工具栏 ============================== */
QToolBar {{
    background: transparent;
    border: none;
    padding: 0;
    spacing: 6px;
}}
QToolBar::separator {{
    background: {LINE};
    width: 1px;
    margin: 8px 10px;
}}

QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 7px;
    padding: 7px 13px;
    color: {FG_DIM};
    font-size: 12px;
}}
QToolButton:hover {{
    background: {BG_HOVER};
    border-color: {LINE};
    color: {FG};
}}
QToolButton:pressed {{ background: {BG_ACTIVE}; }}
QToolButton:disabled {{ color: #3c4453; background: transparent; border-color: transparent; }}
QToolButton#accent {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #ffffff;
    font-weight: 600;
}}
QToolButton#accent:hover {{ background: {ACCENT_HOVER}; border-color: {ACCENT_HOVER}; }}
QToolButton#accent:disabled {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM}; color: #93a6d4; }}

/* ============================== 按钮 ============================== */
QPushButton {{
    background: {BG_CARD};
    border: 1px solid {LINE};
    border-radius: 7px;
    padding: 7px 15px;
    color: {FG};
}}
QPushButton:hover {{ background: {BG_HOVER}; border-color: #2f394c; }}
QPushButton:pressed {{ background: {BG_ACTIVE}; }}
QPushButton:disabled {{ color: {FG_MUTE}; background: #11141b; border-color: {LINE_SOFT}; }}
QPushButton#primary {{
    background: {ACCENT};
    border: 1px solid {ACCENT};
    color: #ffffff;
    font-weight: 600;
    padding: 7px 18px;
}}
QPushButton#primary:hover {{ background: {ACCENT_HOVER}; }}
QPushButton#primary:disabled {{ background: {ACCENT_DIM}; border-color: {ACCENT_DIM}; color: #8fa2cf; }}
QPushButton#ghost {{
    background: transparent;
    border: 1px solid {LINE};
    color: {FG_DIM};
}}
QPushButton#ghost:hover {{ border-color: {ACCENT}; color: {FG}; }}
QPushButton#danger {{ color: {BAD}; }}
QPushButton#danger:hover {{ border-color: {BAD}; background: rgba(248,113,113,0.10); }}

/* ============================== 卡片 ============================== */
QFrame#card {{
    background: {BG_PANEL};
    border: 1px solid {LINE_SOFT};
    border-radius: 10px;
}}
QFrame#statCard {{
    background: {BG_PANEL};
    border: 1px solid {LINE_SOFT};
    border-radius: 9px;
}}
QFrame#statCard:hover {{ border-color: #2b3446; }}
QFrame#brandBar {{
    background: transparent;
    border: none;
    border-bottom: 1px solid {LINE_SOFT};
}}
QLabel#brandTitle {{
    color: {FG};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#brandSub {{ color: #47505f; font-size: 10px; }}
QLabel#statLabel {{ color: #66707f; font-size: 11px; }}
QLabel#statValue {{
    font-family: {MONO_FONT};
    font-size: 20px;
    font-weight: 700;
}}
QLabel#sectionTitle {{
    color: {FG};
    font-size: 12px;
    font-weight: 700;
}}
QLabel#hint {{ color: {FG_MUTE}; font-size: 11px; }}

/* ============================== 表格 ============================== */
QTableView {{
    background: {BG_BASE};
    alternate-background-color: #0d1016;
    border: 1px solid {LINE_SOFT};
    border-radius: 10px;
    selection-background-color: rgba(77,124,254,0.18);
    selection-color: {FG};
    outline: none;
    gridline-color: transparent;
}}
QTableView::item {{ padding: 2px 8px; border: none; }}
QTableView::item:selected {{ background: rgba(77,124,254,0.18); color: {FG}; }}
QTableView::item:hover {{ background: rgba(255,255,255,0.028); }}
QTableView::item:selected:hover {{ background: rgba(77,124,254,0.22); }}

QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {BG_PANEL};
    color: {FG_DIM};
    border: none;
    border-bottom: 1px solid {LINE};
    padding: 9px 8px;
    font-size: 11px;
    font-weight: 600;
}}
QHeaderView::section:hover {{ color: {FG_DIM}; background: {BG_HOVER}; }}
QTableCornerButton::section {{ background: {BG_PANEL}; border: none; }}

/* ============================== 输入 ============================== */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {BG_CARD};
    border: 1px solid {LINE};
    border-radius: 7px;
    padding: 6px 9px;
    color: {FG};
    selection-background-color: {ACCENT_DIM};
    selection-color: #ffffff;
}}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{ border-color: #2f394c; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus {{
    border-color: {ACCENT};
    background: #12161e;
}}
QLineEdit::placeholder {{ color: {FG_MUTE}; }}
QComboBox::drop-down {{ border: none; width: 20px; }}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid {FG_MUTE};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {BG_CARD};
    border: 1px solid {LINE};
    border-radius: 7px;
    padding: 4px;
    selection-background-color: {BG_ACTIVE};
    outline: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    background: transparent;
    border: none;
    width: 16px;
}}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-bottom: 4px solid {FG_MUTE};
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: none;
    border-left: 3px solid transparent;
    border-right: 3px solid transparent;
    border-top: 4px solid {FG_MUTE};
}}

QTextBrowser {{
    background: {BG_CARD};
    border: 1px solid {LINE_SOFT};
    border-radius: 8px;
    padding: 6px;
    color: {FG_DIM};
}}

/* ============================== 勾选 ============================== */
QCheckBox {{ spacing: 8px; color: {FG_DIM}; }}
QCheckBox:hover {{ color: {FG}; }}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border: 1px solid #3a4356;
    border-radius: 4px;
    background: {BG_CARD};
}}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}

/* ============================== 分组 ============================== */
QGroupBox {{
    background: {BG_PANEL};
    border: 1px solid {LINE_SOFT};
    border-radius: 10px;
    margin-top: 16px;
    padding: 14px 12px 12px 12px;
    font-size: 12px;
    font-weight: 700;
    color: {FG};
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 6px;
    color: {FG_DIM};
    background: {BG_BASE};
}}

/* ============================== 进度 ============================== */
QProgressBar {{
    background: {BG_CARD};
    border: none;
    border-radius: 2px;
    height: 4px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{
    border-radius: 2px;
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
        stop:0 {ACCENT}, stop:1 {CYAN});
}}

/* ============================== 状态栏 ============================== */
QStatusBar {{
    background: transparent;
    border-top: 1px solid {LINE_SOFT};
    color: {FG_MUTE};
    font-size: 11px;
}}
QStatusBar::item {{ border: none; }}

/* ============================== 分割器 ============================== */
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 10px; }}
QSplitter::handle:hover {{ background: {LINE_SOFT}; }}

/* ============================== 滚动条 ============================== */
QScrollBar:vertical {{
    background: transparent;
    width: 9px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: #2a3242;
    border-radius: 4px;
    min-height: 28px;
}}
QScrollBar::handle:vertical:hover {{ background: #3a4457; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{
    background: transparent;
    height: 9px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: #2a3242;
    border-radius: 4px;
    min-width: 28px;
}}
QScrollBar::handle:horizontal:hover {{ background: #3a4457; }}

/* ============================== 菜单 ============================== */
QMenu {{
    background: {BG_CARD};
    border: 1px solid {LINE};
    border-radius: 9px;
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 24px 7px 14px;
    border-radius: 6px;
    color: {FG_DIM};
}}
QMenu::item:selected {{ background: {BG_ACTIVE}; color: {FG}; }}
QMenu::separator {{ height: 1px; background: {LINE_SOFT}; margin: 5px 8px; }}
QMenu::indicator {{ width: 13px; height: 13px; margin-left: 6px; }}

/* ============================== 日志 ============================== */
QPlainTextEdit#logView {{
    background: #07090d;
    border: 1px solid {LINE_SOFT};
    border-radius: 10px;
    padding: 8px 10px;
    font-family: {MONO_FONT};
    font-size: 11px;
    color: {FG_DIM};
}}

/* ============================== Tab ============================== */
QTabWidget::pane {{
    border: 1px solid {LINE_SOFT};
    border-radius: 10px;
    background: {BG_PANEL};
}}
QTabBar::tab {{
    background: transparent;
    padding: 8px 16px;
    color: {FG_MUTE};
    border: none;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {FG}; border-bottom-color: {ACCENT}; }}
"""
