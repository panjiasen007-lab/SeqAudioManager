"""深色主题 QSS：与列表/日志的语义色配套，Win10/11 下 DPI 无关，纯样式表无图片依赖。"""

ACCENT = "#4a9eff"
BG = "#1b1c1f"
BG_ALT = "#222429"
BORDER = "#33363d"
TEXT = "#dcdfe4"
TEXT_DIM = "#8b909a"

QSS = f"""
QWidget {{
    background: {BG};
    color: {TEXT};
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 12px;
}}
QMainWindow, QDialog {{ background: {BG}; }}

QToolBar {{
    background: {BG_ALT};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 4px 6px;
    spacing: 4px;
}}
QToolBar QToolButton {{
    padding: 5px 10px;
    border-radius: 4px;
    color: {TEXT};
}}
QToolBar QToolButton:hover {{ background: #2c3038; }}
QToolBar QToolButton:pressed {{ background: #363b45; }}
QToolBar QToolButton:disabled {{ color: #5a5f68; }}
QToolBar::separator {{ background: {BORDER}; width: 1px; margin: 4px 6px; }}

QPushButton {{
    background: #2c3038;
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 6px 14px;
    color: {TEXT};
}}
QPushButton:hover {{ background: #363b45; border-color: #454a54; }}
QPushButton:pressed {{ background: #22262d; }}
QPushButton:disabled {{ color: #5a5f68; background: #232529; }}
QPushButton#primary {{
    background: {ACCENT};
    border: none;
    color: #0b1220;
    font-weight: 600;
}}
QPushButton#primary:hover {{ background: #63aeff; }}
QPushButton#primary:disabled {{ background: #2a3a52; color: #6a7c96; }}
QPushButton#danger {{ color: #f85149; }}

QTableView {{
    background: {BG};
    alternate-background-color: #1f2126;
    gridline-color: #2a2d33;
    border: 1px solid {BORDER};
    border-radius: 4px;
    selection-background-color: #2b4a72;
    selection-color: #ffffff;
}}
QTableView::item {{ padding: 3px 4px; }}
QHeaderView::section {{
    background: {BG_ALT};
    color: {TEXT_DIM};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 5px 6px;
    font-weight: 600;
}}
QTableCornerButton::section {{ background: {BG_ALT}; border: none; }}

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: #16171a;
    border: 1px solid {BORDER};
    border-radius: 4px;
    padding: 4px 6px;
    color: {TEXT};
    selection-background-color: #2b4a72;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{
    background: #222429;
    border: 1px solid {BORDER};
    selection-background-color: #2b4a72;
    outline: none;
}}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{
    width: 14px; height: 14px;
    border: 1px solid #4a4f59;
    border-radius: 3px;
    background: #16171a;
}}
QCheckBox::indicator:checked {{
    background: {ACCENT};
    border-color: {ACCENT};
}}
QGroupBox {{
    border: 1px solid {BORDER};
    border-radius: 6px;
    margin-top: 14px;
    padding-top: 8px;
    font-weight: 600;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 10px;
    padding: 0 4px;
    color: {TEXT_DIM};
}}

QProgressBar {{
    background: #16171a;
    border: 1px solid {BORDER};
    border-radius: 3px;
    height: 8px;
    text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 2px; }}

QStatusBar {{ background: {BG_ALT}; border-top: 1px solid {BORDER}; color: {TEXT_DIM}; }}
QSplitter::handle {{ background: {BORDER}; width: 1px; height: 1px; }}
QScrollBar:vertical {{
    background: transparent; width: 10px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #3a3f48; border-radius: 5px; min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: #4a505b; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; }}
QScrollBar::handle:horizontal {{ background: #3a3f48; border-radius: 5px; min-width: 24px; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 4px; }}
QTabBar::tab {{
    background: transparent; padding: 6px 14px; color: {TEXT_DIM};
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}

QMenu {{ background: #24262b; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px; border-radius: 3px; }}
QMenu::item:selected {{ background: #2b4a72; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 8px; }}

QToolTip {{
    background: #24262b;
    color: {TEXT};
    border: 1px solid {BORDER};
    padding: 4px 6px;
}}
"""
