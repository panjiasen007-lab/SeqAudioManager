"""主窗口：品牌条 + 数据卡片 + 曲目表格 + 参数面板 + 运行日志。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QItemSelectionModel, QObject, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QIcon,
    QKeySequence,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import lyrics as lyrics_mod
from ..core import rename as rename_mod
from ..core import report as report_mod
from ..core.config import PresetStore, Settings
from ..core.logging_setup import add_listener, remove_listener
from ..core.model import Tags, Track, TrackStatus
from ..pipeline.processor import BatchOptions
from . import fonts, i18n, theme
from .backdrop import BackdropWidget
from .delegate import TrackDelegate
from .dialogs import (
    BatchTagEditDialog,
    CandidateDialog,
    CommandPaletteDialog,
    RenamePreviewDialog,
    TagEditDialog,
)
from .health import HealthDialog
from .inspector import InspectorPanel
from .settings_panel import SettingsPanel
from .track_model import (
    COLUMNS,
    HIDDEN_BY_DEFAULT,
    STRETCH_COLUMN,
    TrackFilterProxy,
    TrackTableModel,
)
from .widgets import BrandBar, EmptyState, SearchInput, SectionHeader, StatCard
from .worker import (
    AuthenticityWorker,
    CoverWorker,
    MatchPreviewWorker,
    ProcessorWorker,
    ScanWorker,
    SplitCueWorker,
)

log = logging.getLogger(__name__)

LEVEL_COLORS = {
    "ERROR": theme.BAD,
    "CRITICAL": theme.BAD,
    "WARNING": theme.WARN,
    "INFO": theme.FG_DIM,
    "DEBUG": theme.FG_MUTE,
}

#: 「添加文件」对话框过滤器；SACD ISO 是光盘镜像，单列一类便于用户找到
AUDIO_FILE_FILTER = (
    "音频 (*.flac *.ape *.wav *.wv *.tta *.m4a *.dsf *.dff *.iso *.mp3 *.aac *.ogg *.opus);;"
    "SACD 镜像 (*.iso);;所有文件 (*.*)"
)


class LogBridge(QObject):
    """把 logging 回调安全地转成 Qt 信号（回调可能来自工作线程）。"""

    message = Signal(str, str)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        i18n.set_locale(getattr(settings, "language", "zh_CN") or "zh_CN")
        self.settings = settings
        # 字体必须在构建控件之前选好：QSS 里的 font-family 与全局字体都依赖它
        fonts.configure(settings.ui_font, settings.mono_font, settings.font_scale)
        app = QApplication.instance()
        if app is not None:
            app.setFont(fonts.ui_font(13))
        self.presets = PresetStore()
        self.worker: Optional[ProcessorWorker] = None
        self.scan_worker: Optional[ScanWorker] = None
        self.revert_scan_worker: Optional[ScanWorker] = None
        self._revert_targets: list[Track] = []
        self._undo_stack: list[tuple[list[Track], list[Tags]]] = []
        self._match_workers: list[MatchPreviewWorker] = []
        self.auth_worker: Optional[AuthenticityWorker] = None
        self.cover_worker: Optional[CoverWorker] = None
        self.split_worker: Optional[SplitCueWorker] = None
        self._auth_stopped = False
        self._split_stopped = False
        self._stat_cards: dict[str, StatCard] = {}
        self._i18n_buttons: dict[QPushButton, str] = {}
        self._i18n_stat_keys: dict[str, str] = {}

        self.model = TrackTableModel()
        self.proxy = TrackFilterProxy()
        self.proxy.setSourceModel(self.model)

        self._log_bridge = LogBridge()
        self._log_bridge.message.connect(self._append_log)
        self._log_listener = lambda level, msg: self._log_bridge.message.emit(level, msg)

        self.setWindowTitle("音序管家 · 音乐元数据批量处理")
        self.resize(1420, 880)
        self.setMinimumSize(1040, 720)
        self.setAcceptDrops(True)
        self._build_ui()
        self._init_shortcuts()

        self._refresh_stats()
        self.apply_theme(self.settings.theme, persist=False)
        add_listener(self._log_listener)
        self._append_log("INFO", f"日志目录：{self._log_dir_hint()}")
        if settings.offline_mode:
            self._append_log("WARNING", "当前为离线模式：仅做本地标签编辑、水印清理与重命名")

    # ------------------------------------------------------------ 布局

    def _build_ui(self) -> None:
        surface = BackdropWidget()
        surface.setObjectName("appSurface")
        self.surface = surface
        self.setCentralWidget(surface)

        root = QVBoxLayout(surface)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.brand_bar = BrandBar(f"{__version__}")
        root.addWidget(self.brand_bar)

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(14, 10, 14, 8)
        body_layout.setSpacing(10)
        root.addWidget(body, 1)

        body_layout.addWidget(self._build_action_bar())
        body_layout.addLayout(self._build_stat_row())

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_table_area())

        self.settings_panel = SettingsPanel(self.settings)
        self.settings_panel.settingsChanged.connect(self._on_settings_changed)
        self.settings_panel.saved.connect(self._on_settings_saved)
        self.settings_panel.appearancePreviewChanged.connect(self._preview_appearance)

        self.inspector = InspectorPanel()
        self.inspector.editRequested.connect(self.edit_selected)
        self.inspector.spectrumRequested.connect(self.show_spectrum)

        self.right_tabs = QTabWidget()
        self.right_tabs.setDocumentMode(True)
        self.right_tabs.addTab(self.inspector, "详情")
        self.right_tabs.addTab(self.settings_panel, "参数")
        self.right_tabs.setCurrentWidget(self.inspector)

        QTimer.singleShot(0, lambda: self.btn_toggle_panel.setChecked(True))
        splitter.addWidget(self.right_tabs)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([1040, 348])
        body_layout.addWidget(splitter, 1)

        body_layout.addWidget(self._build_log_area())

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        body_layout.addWidget(self.progress)

        status = QStatusBar()
        status.setSizeGripEnabled(False)
        status.setFixedHeight(30)
        self.setStatusBar(status)
        self.status_hint = QLabel("拖入音频文件，或点击「添加文件夹」")
        status.addWidget(self.status_hint, 1)
        self.mode_label = QLabel("")
        self.mode_label.setObjectName("hint")
        status.addPermanentWidget(self.mode_label)

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.btn_add_files = self._button(i18n.tr("add_files"), self.add_files, ghost=True, i18n_key="add_files")
        self.btn_add_folder = self._button(i18n.tr("add_folder"), self.add_folder, ghost=True, i18n_key="add_folder")
        self.btn_remove = self._button(i18n.tr("remove"), self.remove_selected, ghost=True, i18n_key="remove")
        for btn in (self.btn_add_files, self.btn_add_folder, self.btn_remove):
            layout.addWidget(btn)

        layout.addWidget(self._vline())

        # 「清空列表」放回工具栏（一键全自动左侧）
        self.btn_clear = self._button(i18n.tr("clear"), self.clear_all, ghost=True, i18n_key="clear")
        self.btn_clear.setToolTip("清空当前列表（不删除磁盘文件）")
        self.btn_auto = self._button(i18n.tr("auto"), self.run_auto, i18n_key="auto")
        self.btn_auto.setObjectName("primary")
        self.btn_auto.setToolTip("匹配 → 补全 → 清理水印 → 写标签 → 歌词（按当前参数）")
        self.btn_save_all = self._button(i18n.tr("save_all"), self.confirm_save_all, i18n_key="save_all")
        self.btn_save_all.setObjectName("success")
        self.btn_save_all.setToolTip("把列表里所有未写入的标签变更确认并保存到磁盘（会二次确认）")
        self.btn_local = self._button(i18n.tr("local_only"), self.run_local_only, ghost=True, i18n_key="local_only")
        self.btn_local.setToolTip("不联网：清理水印、补全空白字段、写标签、重命名")
        self.btn_stop = self._button(i18n.tr("stop"), self.stop, ghost=True, i18n_key="stop")
        self.btn_stop.setEnabled(False)
        layout.addWidget(self.btn_clear)
        layout.addWidget(self.btn_auto)
        layout.addWidget(self.btn_save_all)
        layout.addWidget(self.btn_local)
        layout.addWidget(self.btn_stop)

        layout.addWidget(self._vline())

        self.btn_auth = self._button(i18n.tr("auth"), self.run_authenticity, ghost=True, i18n_key="auth")
        self.btn_auth.setToolTip("频谱分析判定是否真无损（需解码，速度约 0.3~1 秒/首）")
        self.btn_cleanup = self._button(i18n.tr("cleanup"), self.run_cleanup, ghost=True, i18n_key="cleanup")
        self.btn_cleanup.setObjectName("danger")
        self.btn_cleanup.setToolTip("把判定为假无损/可疑的文件移入回收站或隔离区（Ctrl+Shift+D）")
        layout.addWidget(self.btn_auth)
        layout.addWidget(self.btn_cleanup)

        layout.addStretch(1)

        self.btn_more = self._button(i18n.tr("more"), self._show_more_menu, ghost=True, i18n_key="more")
        self.btn_more.setToolTip("重命名预览、导出报表、隔离区、关于")
        self.btn_more.setMenu(self._build_more_menu())
        layout.addWidget(self.btn_more)

        # 窄屏救星：收起参数面板，表格独占整宽
        self.btn_toggle_panel = self._button(i18n.tr("sidebar"), self.toggle_settings_panel, ghost=True, i18n_key="sidebar")
        self.btn_toggle_panel.setCheckable(True)
        self.btn_toggle_panel.setToolTip("显示 / 隐藏右侧参数面板（Ctrl+P）")
        layout.addWidget(self.btn_toggle_panel)
        return bar

    def _build_more_menu(self) -> QMenu:
        menu = QMenu(self)

        self.action_rename = menu.addAction("重命名预览…", self.rename_preview)
        self.action_report = menu.addAction("导出处理报表…", self.export_report)
        menu.addAction("曲库体检…", self.open_health)
        menu.addAction("批量导出封面…", self.export_covers)
        menu.addAction("生成文件夹封面", self.run_folder_covers)
        menu.addSeparator()
        menu.addAction("去除选中标题的序号", self._strip_selected_numbers)
        menu.addSeparator()
        self.action_quarantine = menu.addAction("隔离区管理…", self.open_quarantine)
        self.action_spectrum = menu.addAction("查看频谱…", self.show_spectrum)
        # ---- 语言子菜单 ----
        self.lang_menu = menu.addMenu(i18n.tr("menu_language"))
        lang_menu = self.lang_menu
        self._lang_actions: dict[str, object] = {}
        for code in i18n.locale_names():
            action = lang_menu.addAction(i18n.locale_names()[code])
            action.setCheckable(True)
            action.setChecked(code == self.settings.language)
            action.toggled.connect(lambda checked, c=code: self.apply_language(c) if checked else None)
            self._lang_actions[code] = action

        # ---- 主题子菜单 ----
        self.theme_menu = menu.addMenu(i18n.tr("menu_theme"))
        self._theme_actions: dict[str, object] = {}
        for name, palette in theme.THEMES.items():
            action = self.theme_menu.addAction(i18n.tr_theme(name))
            action.setIcon(self._theme_swatch(palette))
            action.setCheckable(True)
            action.setChecked(name == self.settings.theme)
            action.toggled.connect(lambda checked, n=name: self.apply_theme(n) if checked else None)
            self._theme_actions[name] = action

        menu.addSeparator()
        menu.addAction("命令面板…", self.show_command_palette)
        menu.addAction("打开日志目录", self._open_log_dir)
        menu.addSeparator()
        menu.addAction("关于音序管家…", self.show_about)
        return menu

    def _show_more_menu(self) -> None:
        """按钮自带的菜单在点击时弹出。"""
        return None

    @staticmethod
    def _theme_swatch(palette) -> QIcon:
        """主题菜单左侧的小色块：底/强调/辅助色的斜向渐变。"""
        pixmap = QPixmap(18, 18)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        gradient = QLinearGradient(0, 0, 18, 18)
        gradient.setColorAt(0.0, QColor(palette.base))
        gradient.setColorAt(0.52, QColor(palette.accent))
        gradient.setColorAt(1.0, QColor(palette.cyan))
        painter.setBrush(gradient)
        painter.setPen(QPen(QColor(palette.line), 1))
        painter.drawRoundedRect(QRectF(0.5, 0.5, 17, 17), 4, 4)
        painter.end()
        return QIcon(pixmap)

    def toggle_settings_panel(self) -> None:
        visible = not self.right_tabs.isVisible()
        self.right_tabs.setVisible(visible)
        self.btn_toggle_panel.setChecked(visible)

    def _button(self, text: str, slot, ghost: bool = False, i18n_key: str = "") -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(34)
        if ghost:
            btn.setObjectName("ghost")
        btn.clicked.connect(slot)
        if i18n_key:
            self._i18n_buttons[btn] = i18n_key
        return btn

    @staticmethod
    def _vline() -> QFrame:
        line = QFrame()
        line.setObjectName("vLine")
        line.setFixedSize(1, 20)
        return line

    def _build_stat_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        specs = [
            ("total", "stat_total", "fg"),
            ("lossless", "stat_lossless", "cyan"),
            ("suspect", "stat_fake", "bad"),
            ("done", "stat_done", "ok"),
            ("review", "stat_review", "warn"),
            ("failed", "stat_failed", "bad"),
        ]
        for key, title_key, color_name in specs:
            card = StatCard(key, i18n.tr(title_key), color_name)
            self._i18n_stat_keys[key] = title_key
            if key == "suspect":
                card.setToolTip("判定为假无损 / 疑似假无损 / 升频 的数量，点击筛选")
            card.clicked.connect(self._on_stat_clicked)
            self._stat_cards[key] = card
            row.addWidget(card)
        return row

    def _build_table_area(self) -> QWidget:
        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(10)

        self.search_edit = SearchInput("搜索歌名、歌手、专辑或文件名…")
        self.search_edit.textChanged.connect(self.proxy.set_text)
        filter_row.addWidget(self.search_edit, 1)

        self.lossless_check = QCheckBox("只看无损")
        self.lossless_check.toggled.connect(self.proxy.set_lossless_only)
        self.problems_check = QCheckBox("只看问题")
        self.problems_check.toggled.connect(self.proxy.set_problems_only)
        filter_row.addWidget(self.lossless_check)
        filter_row.addWidget(self.problems_check)

        self.spec_combo = QComboBox()
        self.spec_combo.setFixedHeight(34)
        self.spec_combo.setMinimumWidth(112)
        self.spec_combo.setToolTip("按采样率 / 位深 / DSD 规格筛选列表")
        for label, key in (
            ("全部规格", "all"),
            ("44.1 kHz", "sr44100"),
            ("48 kHz", "sr48000"),
            ("96 kHz", "sr96000"),
            ("176.4k+", "sr_hi"),
            ("DSD", "dsd"),
            ("16 bit", "bit16"),
            ("24 bit", "bit24"),
            ("32 bit+", "bit32"),
        ):
            self.spec_combo.addItem(label, key)
        self.spec_combo.currentIndexChanged.connect(self._on_spec_filter)
        filter_row.addWidget(self.spec_combo)
        layout.addLayout(filter_row)

        # 判定筛选胶囊：带数量，点一下即可切换
        self._verdict_filter_items = [
            ("filter_all", "all"),
            ("verdict_true", "true"),
            ("verdict_fake", "fake"),
            ("filter_any", "suspect_any"),
            ("verdict_unknown", "unknown"),
        ]
        self._verdict_key = "all"
        self._verdict_counts: dict[str, int] = {}
        self.verdict_chips: dict[str, QPushButton] = {}
        chip_row = QHBoxLayout()
        chip_row.setSpacing(6)
        self._verdict_group = QButtonGroup(self)
        self._verdict_group.setExclusive(True)
        for label_key, key in self._verdict_filter_items:
            chip = QPushButton(i18n.tr(label_key))
            chip.setObjectName("chip")
            chip.setCheckable(True)
            chip.setFixedHeight(28)
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _checked, k=key: self._select_verdict_filter(k))
            self._verdict_group.addButton(chip)
            self.verdict_chips[key] = chip
            chip_row.addWidget(chip)
        self.verdict_chips["all"].setChecked(True)
        chip_row.addStretch(1)
        layout.addLayout(chip_row)

        # 选中操作条：有选中项时才出现，减少工具栏常驻按钮
        self.selection_bar = QFrame()
        self.selection_bar.setObjectName("selectionBar")
        self.selection_bar.setVisible(False)
        sel_layout = QHBoxLayout(self.selection_bar)
        sel_layout.setContentsMargins(12, 6, 10, 6)
        sel_layout.setSpacing(8)
        self.selection_label = QLabel("已选 0 首")
        self.selection_label.setObjectName("strong")
        sel_layout.addWidget(self.selection_label)
        sel_layout.addStretch(1)
        for text, slot in (
            ("鉴定", self.run_authenticity),
            ("编辑标签", self.edit_selected),
            ("清理", self.run_cleanup),
            ("取消选择", lambda: self.table.clearSelection()),
        ):
            btn = QPushButton(text)
            btn.setObjectName("ghost")
            btn.setFixedHeight(26)
            btn.setCursor(Qt.PointingHandCursor)
            btn.clicked.connect(slot)
            sel_layout.addWidget(btn)
        layout.addWidget(self.selection_bar)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setItemDelegate(TrackDelegate(self.table))
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        self.table.horizontalHeader().setHighlightSections(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        saved_widths = (
            list(self.settings.column_widths)
            if self.settings.view_state_saved
            else []
        )
        for index, (_, _, width) in enumerate(COLUMNS):
            initial = saved_widths[index] if index < len(saved_widths) else width
            self.table.setColumnWidth(index, max(24, initial))
        self.table.horizontalHeader().setSectionResizeMode(STRETCH_COLUMN, QHeaderView.Stretch)
        hidden = (
            set(self.settings.hidden_columns)
            if self.settings.view_state_saved
            else set(HIDDEN_BY_DEFAULT)
        )
        for index in range(len(COLUMNS)):
            self.table.setColumnHidden(index, index in hidden)
        if self.settings.view_state_saved and self.settings.sort_column >= 0:
            order = (
                Qt.DescendingOrder
                if self.settings.sort_order == 1
                else Qt.AscendingOrder
            )
            self.table.sortByColumn(self.settings.sort_column, order)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._header_menu)
        self.table.doubleClicked.connect(lambda _: self.edit_selected())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        # 空列表引导层：铺在 viewport 上，随尺寸变化自适应
        self.empty_state = EmptyState(parent=self.table.viewport())
        self.table.viewport().installEventFilter(self)
        self.empty_state.setGeometry(self.table.viewport().rect())

        layout.addWidget(self.table, 1)
        return area

    def eventFilter(self, obj, event):  # noqa: N802
        if obj is self.table.viewport() and event.type() == QEvent.Resize:
            self.empty_state.setGeometry(self.table.viewport().rect())
        return super().eventFilter(obj, event)

    def _build_log_area(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.addWidget(SectionHeader("运行日志", "识别失败的具体原因都在这里"))
        head.addStretch(1)
        self.btn_log_toggle = QPushButton("收起")
        self.btn_log_toggle.setObjectName("smallGhost")
        self.btn_log_toggle.setCursor(Qt.PointingHandCursor)
        self.btn_log_toggle.setFixedHeight(22)
        self.btn_log_toggle.clicked.connect(self._toggle_log)
        head.addWidget(self.btn_log_toggle)
        self.btn_log_clear = QPushButton("清空")
        self.btn_log_clear.setObjectName("smallGhost")
        self.btn_log_clear.setCursor(Qt.PointingHandCursor)
        self.btn_log_clear.setFixedHeight(22)
        self.btn_log_clear.clicked.connect(lambda: self.log_view.clear())
        head.addWidget(self.btn_log_clear)
        layout.addLayout(head)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("logView")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setFixedHeight(118)
        self.log_view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.log_view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.log_view)
        return wrapper

    def _toggle_log(self) -> None:
        visible = not self.log_view.isVisible()
        self.log_view.setVisible(visible)
        self.btn_log_toggle.setText("收起" if visible else "展开")

    def _init_shortcuts(self) -> None:
        QShortcut(QKeySequence.Open, self, self.add_folder)
        QShortcut(QKeySequence.Delete, self, self.remove_selected)
        undo = QShortcut(QKeySequence.Undo, self.table)
        undo.setContext(Qt.WidgetWithChildrenShortcut)
        undo.activated.connect(self.undo_last_tag_edit)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.run_auto)
        QShortcut(QKeySequence("Ctrl+E"), self, self.edit_selected)
        QShortcut(QKeySequence("Ctrl+K"), self, self.show_command_palette)
        QShortcut(QKeySequence("Ctrl+P"), self, self.toggle_settings_panel)
        QShortcut(QKeySequence("Ctrl+S"), self, self.settings_panel.save)
        QShortcut(QKeySequence("Ctrl+Shift+D"), self, self.run_cleanup)
        QShortcut(QKeySequence("F1"), self, self.show_about)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not getattr(self, "_titlebar_done", False):
            from .win_effects import apply_dark_titlebar

            apply_dark_titlebar(int(self.winId()))
            self._titlebar_done = True

    # ------------------------------------------------------------ 导入

    def add_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "选择音频文件",
            self.settings.last_folder or "",
            AUDIO_FILE_FILTER,
        )
        if files:
            self.settings.last_folder = str(Path(files[0]).parent)
            self._start_scan([Path(f) for f in files])

    def add_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "选择音乐文件夹", self.settings.last_folder or ""
        )
        if folder:
            self.settings.last_folder = folder
            self._start_scan([Path(folder)])

    def _start_scan(self, paths: list[Path]) -> None:
        if self.scan_worker is not None and self.scan_worker.isRunning():
            QMessageBox.information(self, "请稍候", "正在扫描上一批文件，请等待完成。")
            return
        self.brand_bar.set_status("扫描中", theme.CYAN)
        self.status_hint.setText("正在扫描…")
        self.scan_worker = ScanWorker(paths, self.settings.recursive_scan)
        self.scan_worker.discovered.connect(
            lambda n: self.status_hint.setText(f"正在扫描… 已发现 {n} 个音频")
        )
        self.scan_worker.finishedScan.connect(self._on_scan_finished)
        self.scan_worker.failed.connect(self._on_scan_failed)
        self.scan_worker.start()

    def _on_scan_failed(self, message: str) -> None:
        """扫描线程异常：日志 + 状态栏 + 明确弹窗，避免用户只看到空列表。"""
        self._append_log("ERROR", f"导入失败：{message}")
        self.brand_bar.set_status("导入失败", theme.BAD)
        self.status_hint.setText(f"导入失败：{message}")
        QMessageBox.warning(
            self,
            "导入失败",
            "文件未能导入：\n"
            f"{message}\n\n"
            "若为 SACD ISO，请确认 assets/tools/sacd_extract.exe 未被安全软件拦截。",
        )

    def _on_scan_finished(self, tracks: list[Track]) -> None:
        if not tracks:
            self._append_log("WARNING", "没有找到可导入的音频文件")
            self.status_hint.setText("没有找到可导入的音频文件")
            self.brand_bar.set_status("就绪", theme.FG_DIM)
            return
        incoming = {str(t.path).lower() for t in tracks}
        # 旧版本把 SACD ISO 记成「不支持/失败」的一行；重新导入时若只按路径去重，
        # 12 个新分轨会被全部当成重复丢掉，列表里只剩旧失败行。
        # 因此先替换「同路径的失败/不支持行」，以及「同路径尚未展开的整轨行」。
        expanded_paths = {
            str(t.path).lower() for t in tracks if t.cue is not None and t.cue_track is not None
        }
        stale = [
            t for t in self.model.tracks()
            if str(t.path).lower() in incoming
            and (
                t.status in (TrackStatus.FAILED, TrackStatus.UNSUPPORTED)
                or (t.cue_track is None and str(t.path).lower() in expanded_paths)
            )
        ]
        if stale:
            self.model.remove_tracks(stale)
            self._append_log("INFO", f"替换 {len(stale)} 条旧的失败/未展开记录")

        existing = {str(t.path).lower() for t in self.model.tracks()}
        fresh = [t for t in tracks if str(t.path).lower() not in existing]
        duplicates = len(tracks) - len(fresh)
        self.model.add_tracks(fresh)
        unsupported = sum(1 for t in fresh if t.status is TrackStatus.UNSUPPORTED)
        self._append_log(
            "INFO",
            f"导入 {len(fresh)} 个文件"
            + (f"，跳过 {duplicates} 个重复项" if duplicates else "")
            + (f"，{unsupported} 个不支持" if unsupported else ""),
        )
        self._refresh_stats()
        self.brand_bar.set_status("就绪", theme.FG_DIM)

        aligned = [t for t in fresh if t.auto_aligned]
        if aligned:
            # 等表格先画出来再弹窗：用户能直接看到哪些行的歌名被自动对齐了
            QTimer.singleShot(
                0, lambda rows=aligned: self._confirm_auto_alignment(rows)
            )

    def _confirm_auto_alignment(self, tracks: list[Track]) -> None:
        """导入后确认「占位符标签按文件名对齐」是否写回文件。

        扫描时对齐只改内存里的 ``working``，文件里的原始标签仍是 ``Track 01``
        这类占位符。保存才落盘；不保存则把 ``working`` 还原，列表恢复未对齐的样子。
        """
        if not tracks:
            return
        if self.worker is not None and self.worker.isRunning():
            # 正在跑批处理时不要插一个写任务，对齐结果先留在列表里
            self._append_log(
                "INFO",
                f"{len(tracks)} 个文件的占位符标签已按文件名对齐"
                "（暂存列表中，可用「全部确认保存」写入）",
            )
            return

        box = QMessageBox(self)
        box.setWindowTitle("文件名与歌名已对齐")
        box.setIcon(QMessageBox.Question)
        box.setText(
            f"导入的 {len(tracks)} 个文件，标签里的歌名（或歌手）是"
            "「Track 01」「Unknown」这类占位内容，已按文件名自动对齐。"
            "当前只改列表显示，文件本身还没改动。"
        )
        preview = "、".join(t.path.name for t in tracks[:3])
        if len(tracks) > 3:
            preview += f" 等 {len(tracks)} 个"
        box.setInformativeText(
            f"示例：{preview}\n\n"
            "「保存到文件」会把对齐后的歌名/歌手写入音频标签（写入前自动备份）；\n"
            "「不保存」会还原为文件原有的占位符标签，列表恢复未对齐的样子。"
        )
        save_btn = box.addButton("保存到文件", QMessageBox.AcceptRole)
        revert_btn = box.addButton("不保存，还原", QMessageBox.RejectRole)
        box.setDefaultButton(save_btn)
        box.setEscapeButton(revert_btn)
        box.exec()

        if box.clickedButton() is save_btn:
            self._append_log("INFO", f"按文件名对齐：写入 {len(tracks)} 个文件的标签")
            self._start_worker(
                BatchOptions(
                    match_online=False,
                    clean_watermark=False,
                    write_tags=True,
                    fetch_lyrics=False,
                    do_rename=False,
                    ensure_covers=False,
                    write_only=True,
                ),
                tracks=tracks,
            )
            return

        for track in tracks:
            track.reset_working()
            track.auto_aligned = False
            track.notes = [
                n for n in track.notes
                if not n.startswith("占位符标签已按文件名补全")
            ]
            self.model.refresh_row(track)
        self._refresh_stats()
        self._append_log("INFO", f"已还原 {len(tracks)} 个文件的原始标签（未写入磁盘）")
        # 再从磁盘重读一遍，确保列表与文件里的原始标签完全一致。
        # 重扫时 auto_align=False，不会再弹对齐确认，不会形成死循环。
        self._rescan_without_alignment(tracks)

    def _rescan_without_alignment(self, tracks: list[Track]) -> None:
        """重读这些文件的磁盘标签，且不触发「占位符按文件名对齐」。"""
        targets = [
            t for t in tracks
            if t.cue_track is None and t.path.exists()
        ]
        if not targets:
            return
        if (
            self.revert_scan_worker is not None
            and self.revert_scan_worker.isRunning()
        ):
            return
        self._revert_targets = targets
        self.brand_bar.set_status("还原中", theme.CYAN)
        self.status_hint.setText("正在重新读取原始标签…")
        self.revert_scan_worker = ScanWorker(
            [t.path for t in targets], recursive=False, auto_align=False
        )
        self.revert_scan_worker.finishedScan.connect(self._on_revert_scan_finished)
        self.revert_scan_worker.failed.connect(
            lambda msg: self._append_log("ERROR", f"重读原始标签失败：{msg}")
        )
        self.revert_scan_worker.start()

    def _on_revert_scan_finished(self, fresh: list[Track]) -> None:
        """把重读结果写回原 Track 对象（保持列表行位置不变）。"""
        by_path: dict[str, Track] = {}
        ambiguous: set[str] = set()
        for track in fresh:
            key = str(track.path).lower()
            if key in by_path:
                # 多轨 CUE 展开后同一文件有多条，不能简单按路径合并
                ambiguous.add(key)
            else:
                by_path[key] = track
        updated = 0
        for old in self._revert_targets:
            key = str(old.path).lower()
            new = by_path.get(key)
            if new is None or key in ambiguous:
                continue
            old.original = new.original
            old.working = new.working
            old.info = new.info
            old.notes = list(new.notes)
            old.status = new.status
            old.cue = new.cue
            old.cue_track = new.cue_track
            old.auto_aligned = False
            self.model.refresh_row(old)
            updated += 1
        self._revert_targets = []
        self._refresh_stats()
        self.brand_bar.set_status("就绪", theme.FG_DIM)
        self.status_hint.setText(f"已恢复 {updated} 个文件的原始标签")
        self._append_log(
            "INFO", f"已重新读取 {updated} 个文件，显示未对齐的原始标签"
        )

    # ------------------------------------------------------------ 拖拽

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # noqa: N802
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._start_scan(paths)
            event.acceptProposedAction()

    # ------------------------------------------------------------ 批处理

    def run_auto(self) -> None:
        if not self._ensure_ready():
            return
        self._start_worker(
            BatchOptions(
                match_online=self.settings.online_enabled and not self.settings.offline_mode,
                clean_watermark=self.settings.watermark_enabled,
                write_tags=True,
                fetch_lyrics=self.settings.use_lrclib or self.settings.use_local_lyrics,
                do_rename=self.settings.rename_enabled,
                ensure_covers=self.settings.ensure_folder_cover,
            )
        )

    def run_local_only(self) -> None:
        if not self._ensure_ready():
            return
        self._start_worker(
            BatchOptions(
                match_online=False,
                clean_watermark=self.settings.watermark_enabled,
                write_tags=True,
                fetch_lyrics=True,
                do_rename=self.settings.rename_enabled,
                ensure_covers=self.settings.ensure_folder_cover,
            )
        )

    def confirm_save_all(self) -> None:
        """全部确认保存：把列表里所有未写入的标签变更确认并落盘（二次确认）。"""
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "已有任务在运行中。")
            return
        tracks = self.model.tracks()
        if not tracks:
            QMessageBox.information(self, "没有曲目", "请先添加音频文件。")
            return

        changed = [t for t in tracks if self._has_pending_changes(t)]
        if not changed:
            QMessageBox.information(self, "无需保存", "列表中没有待写入的标签变更。")
            return

        # 二次确认：写磁盘是重要操作
        reply = QMessageBox.question(
            self,
            "确认保存",
            f"将把 {len(changed)} 首曲目的标签变更写入磁盘（列表共 {len(tracks)} 首）。\n\n"
            "写入会修改音频文件的标签区块（有备份保护），确认继续吗？",
        )
        if reply != QMessageBox.Yes:
            return

        self._start_worker(
            BatchOptions(
                match_online=False,
                clean_watermark=False,
                write_tags=True,
                fetch_lyrics=False,
                do_rename=False,
                ensure_covers=False,
                write_only=True,
            )
        )

    @staticmethod
    def _has_pending_changes(track: Track) -> bool:
        """判断曲目是否有未写入的变更（working 相对 original）。"""
        fields = list(Tags.TEXT_FIELDS) + ["comment", "lyrics"]
        if any(track.working.get(f) != track.original.get(f) for f in fields):
            return True
        return track.working.cover != track.original.cover

    def _ensure_ready(self) -> bool:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "已有任务在运行中。")
            return False
        if not self.model.tracks():
            QMessageBox.information(self, "没有曲目", "请先添加音频文件或文件夹。")
            return False
        return True

    def _start_worker(
        self, options: BatchOptions, tracks: Optional[list[Track]] = None
    ) -> None:
        self.settings_panel.apply_to(self.settings)
        self.settings.save()
        self.settings_panel.mark_saved()

        targets = tracks if tracks is not None else self.model.tracks()
        self._set_running(True)
        self.progress.setMaximum(len(targets))
        self.progress.setValue(0)
        if options.write_only:
            self.mode_label.setText("确认保存")
            self._append_log("INFO", f"开始写入 {len(targets)} 首的标签变更")
        else:
            self.mode_label.setText("联网模式" if options.match_online else "离线模式")
            self._append_log(
                "INFO",
                f"开始处理 {len(targets)} 首（{'联网匹配' if options.match_online else '仅本地'}，"
                f"覆盖策略：{self.settings.conflict()}）",
            )

        self.worker = ProcessorWorker(self.settings, targets, options)
        self.worker.trackUpdated.connect(self.model.refresh_row)
        self.worker.trackUpdated.connect(lambda _: self._refresh_stats())
        self.worker.progressChanged.connect(self._on_progress)
        self.worker.writeProgress.connect(self._on_write_progress)
        self.worker.coverProgress.connect(self._on_cover_progress)
        self.worker.coverSummary.connect(self._on_cover_summary)
        self.worker.phaseChanged.connect(self._on_phase)
        self.worker.finishedRun.connect(self._on_finished)
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        self.progress.setVisible(running)
        self.btn_auto.setEnabled(not running)
        self.btn_save_all.setEnabled(not running)
        self.btn_local.setEnabled(not running)
        self.btn_auth.setEnabled(not running)
        self.btn_clear.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.brand_bar.set_status(
            "处理中" if running else "就绪", theme.PURPLE if running else theme.FG_DIM
        )

    def stop(self) -> None:
        """停止当前所有可取消的后台任务（批处理、鉴定、封面、分轨、扫描）。"""
        stopped: list[str] = []
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            stopped.append("批处理")
        if self.auth_worker is not None and self.auth_worker.isRunning():
            self.auth_worker.cancel()
            self._auth_stopped = True
            stopped.append("无损鉴定")
        if self.cover_worker is not None and self.cover_worker.isRunning():
            self.cover_worker.cancel()
            stopped.append("文件夹封面")
        if self.split_worker is not None and self.split_worker.isRunning():
            self.split_worker.cancel()
            self._split_stopped = True
            stopped.append("分轨导出")
        if self.scan_worker is not None and self.scan_worker.isRunning():
            self.scan_worker.cancel()
            stopped.append("扫描")
        if (
            self.revert_scan_worker is not None
            and self.revert_scan_worker.isRunning()
        ):
            self.revert_scan_worker.cancel()
            stopped.append("重读标签")

        if not stopped:
            self._append_log("INFO", "当前没有正在运行的任务")
            return
        self.btn_stop.setEnabled(False)
        self.status_hint.setText("正在停止…")
        self._append_log(
            "WARNING",
            f"已请求停止：{'、'.join(stopped)}；当前文件结束后退出",
        )

    def _on_progress(self, index: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(index)
        self.status_hint.setText(f"处理中 {index} / {total}")

    def _on_write_progress(self, index: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(index)
        self.status_hint.setText(f"写入标签 {index} / {total}")

    def _on_cover_progress(self, index: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(index)
        self.status_hint.setText(f"生成文件夹封面 {index} / {total}")

    def _on_cover_summary(self, summary) -> None:
        self.status_hint.setText(summary.text() if hasattr(summary, "text") else "")

    def _on_phase(self, name: str) -> None:
        self.brand_bar.set_status(name, theme.CYAN)
        self.progress.setValue(0)

    def _on_finished(self, summary) -> None:
        self._set_running(False)
        self.model.refresh_all()
        self._refresh_stats()
        self._append_log("INFO", summary.text())
        self.status_hint.setText(summary.text())
        self.mode_label.setText("")

    # ------------------------------------------------------------ 行操作

    def _selected_rows(self) -> list[int]:
        return [
            self.proxy.mapToSource(index).row()
            for index in self.table.selectionModel().selectedRows()
        ]

    def _selected_tracks(self) -> list[Track]:
        return [t for t in (self.model.track_at(r) for r in self._selected_rows()) if t]

    def remove_selected(self) -> None:
        rows = self._selected_rows()
        if not rows:
            return
        if QMessageBox.question(
            self,
            "移除选中项",
            f"从列表移除 {len(rows)} 项？（不删除磁盘文件，但会丢失这些项的匹配/编辑状态，需重新导入）",
        ) != QMessageBox.Yes:
            return
        self.model.remove_rows(rows)
        self._refresh_stats()

    def clear_all(self) -> None:
        if not self.model.tracks():
            return
        if QMessageBox.question(self, "确认", "清空当前列表？（不会删除磁盘文件）") == QMessageBox.Yes:
            self.model.clear()
            self._refresh_stats()

    def edit_selected(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            return
        # 多选走批量编辑：每个字段打勾才修改，未勾选保持原值
        if len(tracks) > 1:
            self.batch_edit_selected()
            return
        track = tracks[0]
        dialog = TagEditDialog(track, self)
        if dialog.exec() == TagEditDialog.Accepted:
            self._push_tag_undo(tracks)
            track.working = dialog.result_tags()
            track.status = TrackStatus.PENDING
            track.changed_fields = [
                f
                for f in (
                    "title", "artist", "album", "albumartist", "date", "genre",
                    "track", "disc", "comment", "lyrics",
                )
                if track.working.get(f) != track.original.get(f)
            ]
            if track.working.cover != track.original.cover:
                track.changed_fields.append("cover")
            track.note("已手动编辑，等待写入")
            self.model.refresh_row(track)
            if getattr(self, "inspector", None) is not None:
                self.inspector.refresh()

    def _push_tag_undo(self, tracks: list[Track]) -> None:
        """记录写盘前的标签快照，支持 Ctrl+Z 撤销（最多 30 步）。"""
        self._undo_stack.append((list(tracks), [t.working.copy() for t in tracks]))
        if len(self._undo_stack) > 30:
            self._undo_stack.pop(0)

    def undo_last_tag_edit(self) -> None:
        if not self._undo_stack:
            QMessageBox.information(self, "没有可撤销的操作", "当前没有可撤销的标签编辑。")
            return
        tracks, snapshots = self._undo_stack.pop()
        from ..pipeline.processor import Processor

        for track, snapshot in zip(tracks, snapshots):
            track.working = snapshot
            track.status = TrackStatus.PENDING
            track.notes = [n for n in track.notes if n != "已手动编辑，等待写入"]
            Processor._rebuild_changed_fields(track)
            self.model.refresh_row(track)
        self._refresh_stats()
        if getattr(self, "inspector", None) is not None:
            self.inspector.refresh()
        self._append_log("INFO", f"已撤销 {len(tracks)} 首的标签编辑")
        self.status_hint.setText(f"已撤销 {len(tracks)} 首的标签编辑")

    def batch_edit_selected(self) -> None:
        """批量编辑选中曲目的标签：只有勾选的字段才会被修改（V1.1）。"""
        tracks = self._selected_tracks()
        if not tracks:
            return
        dialog = BatchTagEditDialog(tracks, self)
        if dialog.exec() != BatchTagEditDialog.Accepted:
            return

        values = dialog.result_values()
        if not values:
            QMessageBox.information(self, "未选择字段", "请至少勾选一个要修改的字段。")
            return

        self._push_tag_undo(tracks)
        changed_count = self._apply_batch_values(tracks, values)
        self._refresh_stats()
        if getattr(self, "inspector", None) is not None:
            self.inspector.refresh()
        self._append_log(
            "INFO",
            f"批量编辑 {len(tracks)} 首，实际变更 {changed_count} 首"
            f"（字段：{'、'.join(values)}）",
        )

    def _apply_batch_values(self, tracks: list[Track], values: dict[str, str]) -> int:
        """把批量编辑结果写入曲目的 working 标签，返回实际变更的曲目数。

        只处理 ``values`` 里出现的字段（即用户勾选的），其余字段保持原值。
        """
        changed_count = 0
        for track in tracks:
            touched = False
            for key, value in values.items():
                if track.working.get(key) != value:
                    track.working.set(key, value)
                    touched = True
                if key not in track.changed_fields:
                    track.changed_fields.append(key)
            if touched:
                track.status = TrackStatus.PENDING
                track.note(f"批量编辑：{'、'.join(values)}")
                changed_count += 1
            self.model.refresh_row(track)
        return changed_count

    def show_candidates(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            return
        track = tracks[0]
        self._append_log("INFO", f"正在检索候选：{track.path.name}")
        worker = MatchPreviewWorker(self.settings, track)
        worker.finishedMatch.connect(self._on_candidates)
        worker.failedMatch.connect(
            lambda _t, msg: self._append_log("ERROR", f"检索失败：{msg}")
        )
        self._match_workers.append(worker)
        worker.finished.connect(
            lambda: self._match_workers.remove(worker) if worker in self._match_workers else None
        )
        worker.start()

    def _on_candidates(self, track: Track, result) -> None:
        if not result.candidates:
            QMessageBox.information(self, "无结果", "未找到候选，请检查歌名/歌手或网络连接。")
            return
        dialog = CandidateDialog(track, result.candidates, self)
        if dialog.exec() == CandidateDialog.Accepted and dialog.selected:
            chosen = dialog.selected
            changed = track.working.merge(chosen.to_tags(), self.settings.conflict())
            track.confidence = chosen.confidence
            track.source = chosen.source
            track.status = TrackStatus.MATCHED
            track.changed_fields.extend(c for c in changed if c not in track.changed_fields)
            track.note(f"手动选定：{chosen.label}（{chosen.confidence:.0%}）")
            self.model.refresh_row(track)
            self._append_log("INFO", f"{track.path.name} 已选定候选：{chosen.label}")

    def confirm_selected(self) -> None:
        """一键确认「待人工确认」曲目：检索后直接采纳最高分候选，无需逐个弹窗。"""
        tracks = [
            t for t in self._selected_tracks()
            if t.status is TrackStatus.NEEDS_REVIEW
        ]
        if not tracks:
            QMessageBox.information(
                self, "无需确认",
                "请先选中「待人工确认」状态的曲目（匹配置信度低于阈值的那批）。",
            )
            return
        self._append_log("INFO", f"开始确认 {len(tracks)} 首（采纳最高分候选）")
        for track in tracks:
            worker = MatchPreviewWorker(self.settings, track)
            worker.finishedMatch.connect(self._on_confirm)
            worker.failedMatch.connect(
                lambda t, msg: self._append_log("ERROR", f"确认失败 {t.path.name}：{msg}")
            )
            self._match_workers.append(worker)
            worker.finished.connect(
                lambda w=worker: self._match_workers.remove(w) if w in self._match_workers else None
            )
            worker.start()

    def _on_confirm(self, track: Track, result) -> None:
        if not result.candidates:
            track.note("无可采纳的候选，请手动编辑标签")
            self.model.refresh_row(track)
            self._append_log("WARNING", f"{track.path.name} 未找到可确认的候选")
            return
        chosen = result.candidates[0]
        changed = track.working.merge(chosen.to_tags(), self.settings.conflict())
        track.confidence = chosen.confidence
        track.source = chosen.source
        track.status = TrackStatus.MATCHED
        track.changed_fields.extend(c for c in changed if c not in track.changed_fields)
        track.note(f"已确认：{chosen.label}（{chosen.confidence:.0%}）")
        self.model.refresh_row(track)
        self._refresh_stats()
        self._append_log("INFO", f"{track.path.name} 已确认采纳：{chosen.label}")

    def open_folder(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            return
        import os

        try:
            os.startfile(str(tracks[0].path.parent))  # type: ignore[attr-defined]
        except OSError as exc:
            self._append_log("ERROR", f"打开目录失败：{exc}")

    def copy_path(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            return
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(str(tracks[0].path))
        self.status_hint.setText(f"已复制路径：{tracks[0].path.name}")

    def revert_selected(self) -> None:
        for track in self._selected_tracks():
            track.reset_working()
            track.status = TrackStatus.PENDING
            track.confidence = 0.0
            track.source = ""
            track.notes.clear()
            self.model.refresh_row(track)
        self._refresh_stats()

    def _header_menu(self, pos) -> None:
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        for index, (label, _, _) in enumerate(COLUMNS):
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not self.table.isColumnHidden(index))
            action.toggled.connect(
                lambda checked, i=index: self.table.setColumnHidden(i, not checked)
            )
        menu.exec(self.table.horizontalHeader().mapToGlobal(pos))

    def _show_context_menu(self, pos) -> None:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return
        from PySide6.QtWidgets import QMenu

        # 修复：右键点击的行若不在当前选中集合里，先选中它（并清掉旧多选）。
        # 否则「移入隔离区 / 删除歌曲」等操作拿到的 _selected_tracks() 是
        # 之前选中的行，跟用户右键点的那一行对不上，表现就是「点了没反应」。
        selected = self.table.selectionModel().selectedRows()
        if index not in selected:
            self.table.selectionModel().select(
                index,
                QItemSelectionModel.ClearAndSelect | QItemSelectionModel.Rows,
            )
            self.table.setCurrentIndex(index)

        menu = QMenu(self)
        menu.addAction("编辑标签\tCtrl+E", self.edit_selected)
        menu.addAction("批量编辑标签…", self.batch_edit_selected)
        menu.addAction("查看候选匹配", self.show_candidates)
        menu.addAction("确认匹配（采纳最佳候选）", self.confirm_selected)
        menu.addAction("撤销标签编辑", self.undo_last_tag_edit)
        menu.addAction("撤销本地修改", self.revert_selected)
        menu.addSeparator()
        menu.addAction("查看频谱", self.show_spectrum)
        menu.addAction("分轨导出（CUE / SACD ISO）…", self.run_split_cue)
        menu.addAction("移入隔离区", self._quarantine_selected)
        menu.addAction("删除歌曲…", self.delete_selected)
        menu.addSeparator()
        menu.addAction("打开所在文件夹", self.open_folder)
        menu.addAction("复制完整路径", self.copy_path)
        menu.addSeparator()
        menu.addAction("按标签重命名本目录…", self.rename_folder)
        menu.addAction("仅处理选中项", self._run_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _on_selection_changed(self, *_) -> None:
        tracks = self._selected_tracks()
        self.selection_bar.setVisible(bool(tracks))
        if tracks:
            self.selection_label.setText(f"已选 {len(tracks)} 首")
        self.inspector.set_track(tracks[0] if tracks else None)

    def _select_verdict_filter(self, key: str) -> None:
        chip = self.verdict_chips.get(key)
        if chip is None:
            return
        self._verdict_key = key
        chip.blockSignals(True)
        chip.setChecked(True)
        chip.blockSignals(False)
        self.proxy.set_verdict_group(key)

    def _quarantine_selected(self) -> None:
        """右键直接把选中项送进隔离区（不弹清单，仅对已鉴定的项生效）。"""
        from ..core.quarantine import QuarantineStore

        tracks = [t for t in self._selected_tracks() if t.path.exists()]
        _seen: set[str] = set()
        tracks = [t for t in tracks
                  if not (str(t.path).lower() in _seen or _seen.add(str(t.path).lower()))]
        if not tracks:
            return
        total = sum(t.path.stat().st_size for t in tracks)
        if QMessageBox.question(
            self,
            "移入隔离区",
            f"将 {len(tracks)} 个文件（{total / 1024 / 1024:.0f} MB）移入隔离区？\n"
            "可在「更多 → 隔离区管理」中还原。",
        ) != QMessageBox.Yes:
            return
        store = QuarantineStore()
        created, failed = store.move_in(
            [(t.path, t.authenticity.verdict if t.authenticity else "",
              t.authenticity.detail_text if t.authenticity else "") for t in tracks]
        )
        removed = [t for t in self.model.tracks() if not t.path.exists()]
        self.model.remove_tracks(removed)
        self._refresh_stats()
        self._append_log("WARNING", f"已隔离 {len(created)} 个文件，{len(failed)} 个失败")

    def delete_selected(self) -> None:
        """删除歌曲：把选中项的本地文件移入回收站或彻底删除（二次确认）。

        与「移入隔离区」的区别：隔离区是软件自己的暂存（记录判定依据），
        这里面向「用户就是不想再要这个文件」的场景，直接走系统回收站。
        """
        from ..core.quarantine import (
            QuarantineStore,
            delete_permanently,
            send_to_recycle_bin,
        )

        tracks = [t for t in self._selected_tracks() if t.path.exists()]
        # 分轨条目共享同一文件，按 path 去重避免对同一文件重复删除
        _seen: set[str] = set()
        tracks = [t for t in tracks
                  if not (str(t.path).lower() in _seen or _seen.add(str(t.path).lower()))]
        if not tracks:
            QMessageBox.information(self, "没有文件", "选中项没有可删除的本地文件。")
            return
        total = sum(t.path.stat().st_size for t in tracks)
        paths = [t.path for t in tracks]

        box = QMessageBox(self)
        box.setWindowTitle("删除歌曲")
        box.setIcon(QMessageBox.Warning)
        box.setText(
            f"将处理 {len(tracks)} 个音频文件（{total / 1024 / 1024:.0f} MB）。\n\n"
            "「移入回收站」可还原；「彻底删除」不可恢复，请谨慎。"
        )
        box.addButton("移入回收站", QMessageBox.AcceptRole)
        perm_btn = box.addButton("彻底删除", QMessageBox.DestructiveRole)
        cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()

        if clicked is cancel_btn or clicked is None:
            return

        if clicked is perm_btn:
            # 彻底删除：不可恢复，再问一次
            if QMessageBox.question(
                self,
                "最后确认",
                f"确定要【彻底删除】{len(tracks)} 个文件吗？\n此操作不可恢复！",
            ) != QMessageBox.Yes:
                return
            done, failed = delete_permanently(paths)
            removed = [t for t in self.model.tracks() if not t.path.exists()]
            self._append_log(
                "WARNING",
                f"已彻底删除 {done} 个文件" + (f"，{len(failed)} 个失败" if failed else ""),
            )
            for path, reason in failed:
                self._append_log("ERROR", f"删除失败 {path.name}：{reason}")
        else:
            count, error = send_to_recycle_bin(paths)
            if count == 0:
                # 回收站不可用（非 Windows 或策略禁用），回退隔离区，绝不丢文件
                self._append_log("WARNING", f"回收站不可用（{error}），改用隔离区")
                store = QuarantineStore()
                created, failed = store.move_in([(t.path, "", "") for t in tracks])
                removed = [t for t in self.model.tracks() if not t.path.exists()]
                self._append_log("WARNING", f"已隔离 {len(created)} 个文件（可还原）")
            else:
                removed = [t for t in self.model.tracks() if not t.path.exists()]
                self._append_log("WARNING", f"已移入回收站 {count} 个文件（可在资源管理器还原）")

        if removed:
            self.model.remove_tracks(removed)
            self._refresh_stats()
        self.status_hint.setText(
            f"已删除 {len(removed)} 个文件" if removed else "删除未生效：文件仍存在，请检查权限"
        )

    def _strip_selected_numbers(self) -> None:
        """对选中曲目的标题做一次「去序号」，立即可见。"""
        from ..core.clean import strip_track_number

        tracks = self._selected_tracks() or self.model.tracks()
        count = 0
        for track in tracks:
            title = track.working.title
            if not title:
                continue
            new_title, stripped = strip_track_number(title)
            if stripped:
                track.working.title = new_title
                if "title" not in track.changed_fields:
                    track.changed_fields.append("title")
                track.note("去序号")
                self.model.refresh_row(track)
                count += 1
        self._append_log("INFO", f"已去除 {count} 个标题的序号前缀")
        self.status_hint.setText(f"去序号：{count} 个标题已更新")

    def _run_selected(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "已有任务在运行中。")
            return
        self.settings_panel.apply_to(self.settings)
        self._set_running(True)
        self.progress.setMaximum(len(tracks))
        self.progress.setValue(0)
        self.worker = ProcessorWorker(
            self.settings,
            tracks,
            BatchOptions(
                match_online=self.settings.online_enabled and not self.settings.offline_mode,
                clean_watermark=self.settings.watermark_enabled,
                write_tags=True,
                fetch_lyrics=True,
                do_rename=False,
            ),
        )
        self.worker.trackUpdated.connect(self.model.refresh_row)
        self.worker.trackUpdated.connect(lambda _: self._refresh_stats())
        self.worker.progressChanged.connect(self._on_progress)
        self.worker.writeProgress.connect(self._on_write_progress)
        self.worker.phaseChanged.connect(self._on_phase)
        self.worker.finishedRun.connect(self._on_finished)
        self.worker.start()

    # ------------------------------------------------------------ 真假无损鉴定

    def run_authenticity(self) -> None:
        from ..core.authenticity import ffmpeg_available

        if not ffmpeg_available():
            QMessageBox.warning(
                self,
                "缺少解码组件",
                "真假无损鉴定需要 ffmpeg 解码音频。\n\n"
                "本机未找到 ffmpeg，请把 ffmpeg.exe 放到程序目录，"
                "或安装 imageio-ffmpeg 后重试。",
            )
            return
        if self.auth_worker is not None and self.auth_worker.isRunning():
            QMessageBox.information(self, "请稍候", "正在鉴定中，请等待完成。")
            return

        selected = self._selected_tracks()
        targets = selected or [
            t for t in self.model.tracks()
            if t.status is not TrackStatus.UNSUPPORTED and t.info.lossless
        ]
        # 分轨展开后同一整轨文件可能有多行，按 path 去重，避免对同一文件重复鉴定
        seen: set[str] = set()
        deduped = [t for t in targets if not (str(t.path).lower() in seen or seen.add(str(t.path).lower()))]
        targets = deduped
        if not targets:
            QMessageBox.information(
                self, "没有可鉴定的曲目",
                "无损鉴定只对有损/无损判断意义，请先添加 FLAC / APE / WAV / DSD 等文件。",
            )
            return

        self._set_running(True)
        self._auth_stopped = False
        self.progress.setMaximum(len(targets))
        self.progress.setValue(0)
        self.brand_bar.set_status("鉴定中", theme.CYAN)
        self._append_log(
            "INFO", f"开始无损鉴定 {len(targets)} 首（{'选中项' if selected else '全部无损文件'}）"
        )

        self.auth_worker = AuthenticityWorker(targets, workers=max(2, self.settings.concurrency))
        self.auth_worker.trackDone.connect(self._on_auth_track)
        self.auth_worker.progressChanged.connect(self._on_auth_progress)
        self.auth_worker.finishedAll.connect(self._on_auth_finished)
        self.auth_worker.failed.connect(lambda m: self._append_log("ERROR", f"鉴定失败：{m}"))
        self.auth_worker.start()

    def _on_auth_track(self, track: Track, result) -> None:
        # 同一整轨文件的多个分轨条目共享鉴定结果（按 path 同步）
        key = str(track.path).lower()
        for t in self.model.tracks():
            if str(t.path).lower() == key:
                t.authenticity = result
                self.model.refresh_row(t)
        # 每判完一首就刷新统计卡，让「假无损」数字随扫描实时上涨
        self._refresh_stats()
        if result.reasons:
            self._append_log(
                "WARNING" if result.is_suspicious else "INFO",
                f"{track.path.name} → {result.verdict}（{result.detail_text}）",
            )

    def _on_auth_progress(self, index: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(index)
        self.status_hint.setText(f"鉴定中 {index} / {total}")

    def _on_auth_finished(self, true_count: int, suspect: int, unknown: int) -> None:
        self._set_running(False)
        self.brand_bar.set_status("就绪", theme.FG_DIM)
        self.model.refresh_all()
        # 扫描结束后统一刷新统计卡，把同批推定改判的结果也一并计入
        self._refresh_stats()
        if self._auth_stopped:
            msg = (
                f"鉴定已停止：已完成真无损 {true_count}，可疑 {suspect}，"
                f"无法判定 {unknown}"
            )
            self._append_log("WARNING", msg)
            self._auth_stopped = False
        else:
            msg = f"鉴定完成：真无损 {true_count}，可疑 {suspect}，无法判定 {unknown}"
            self._append_log("WARNING" if suspect else "INFO", msg)
            if suspect:
                self._append_log("INFO", "已自动勾选「只看问题」，可从表格筛出可疑项")
        self.status_hint.setText(msg)

    # ------------------------------------------------------------ 清理假无损

    def run_cleanup(self) -> None:
        """一键清理：把判定为假无损/可疑的文件移出曲库。"""
        from ..core.authenticity import removable_tracks
        from ..core.quarantine import (
            QuarantineStore,
            delete_permanently,
            send_to_recycle_bin,
        )
        from .cleanup import MODE_DELETE, MODE_QUARANTINE, CleanupDialog

        if self.auth_worker is not None and self.auth_worker.isRunning():
            QMessageBox.information(self, "请稍候", "鉴定仍在进行中，请等它跑完再清理。")
            return
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "批处理正在运行，先停止再清理。")
            return

        scope = self._selected_tracks() or self.model.tracks()
        candidates = removable_tracks(
            scope, include_suspect=self.settings.cleanup_include_suspect
        )
        # 分轨条目共享同一整轨文件，删除/隔离只需对每个文件操作一次
        seen: set[str] = set()
        candidates = [t for t in candidates
                      if not (str(t.path).lower() in seen or seen.add(str(t.path).lower()))]
        if not candidates:
            unexamined = [t for t in scope if t.authenticity is None]
            message = "当前列表没有判定为假无损的文件。"
            if unexamined:
                message += f"\n\n其中 {len(unexamined)} 首尚未鉴定，要先跑一遍「真假鉴定」吗？"
                if QMessageBox.question(self, "先鉴定？", message) == QMessageBox.Yes:
                    self.run_authenticity()
                return
            QMessageBox.information(self, "无需清理", message)
            return

        dialog = CleanupDialog(candidates, self)
        if dialog.exec() != CleanupDialog.Accepted:
            return

        paths = [t.path for t in candidates]
        mode = dialog.mode

        removed: list[Track] = []
        if mode == MODE_DELETE:
            done, failed = delete_permanently(paths)
            removed = [t for t in self.model.tracks() if not t.path.exists()]
            self._append_log(
                "WARNING", f"已彻底删除 {done} 个文件" + (f"，{len(failed)} 个失败" if failed else "")
            )
            for path, reason in failed:
                self._append_log("ERROR", f"删除失败 {path.name}：{reason}")
        elif mode == MODE_QUARANTINE:
            store = QuarantineStore()
            created, failed = store.move_in(
                [
                    (t.path, t.authenticity.verdict if t.authenticity else "",
                     t.authenticity.detail_text if t.authenticity else "")
                    for t in candidates
                ]
            )
            removed = [t for t in self.model.tracks() if not t.path.exists()]
            self._append_log(
                "WARNING",
                f"已隔离 {len(created)} 个文件到 {store.root}，可在「隔离区管理」中还原",
            )
            for path, reason in failed:
                self._append_log("ERROR", f"隔离失败 {path.name}：{reason}")
        else:
            count, error = send_to_recycle_bin(paths)
            if count == 0:
                # 回收站不可用（非 Windows 或被策略禁用）时回退到隔离区，
                # 绝不能因为一个 API 失败就退化成"删除失败但文件也没了"
                self._append_log("WARNING", f"回收站不可用（{error}），改用隔离区")
                store = QuarantineStore()
                created, failed = store.move_in(
                    [
                        (t.path, t.authenticity.verdict if t.authenticity else "",
                         t.authenticity.detail_text if t.authenticity else "")
                        for t in candidates
                    ]
                )
                removed = [t for t in self.model.tracks() if not t.path.exists()]
                self._append_log("WARNING", f"已隔离 {len(created)} 个文件（可还原）")
            else:
                removed = [t for t in self.model.tracks() if not t.path.exists()]
                self._append_log("WARNING", f"已移入回收站 {count} 个文件（可在资源管理器还原）")

        if removed:
            self.model.remove_tracks(removed)
            self._refresh_stats()
        self.status_hint.setText(
            f"清理完成：{len(removed)} 个文件已移出列表"
            if removed else "清理未生效：文件仍存在，请检查权限"
        )

    def open_quarantine(self) -> None:
        from .cleanup import QuarantineDialog

        dialog = QuarantineDialog(parent=self)
        dialog.exec()

    # ------------------------------------------------------------ 频谱 / 关于

    def show_spectrum(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            QMessageBox.information(self, "未选中", "请先选中一行，再查看频谱。")
            return
        from .spectrum import open_spectrum

        open_spectrum(tracks[0], self)

    def show_about(self) -> None:
        from .about import AboutDialog

        AboutDialog(self).exec()

    def open_health(self) -> None:
        if not self.model.tracks():
            QMessageBox.information(self, "没有曲目", "请先添加音频文件或文件夹。")
            return
        HealthDialog(self.model.tracks(), self).exec()

    def _show_settings_tab(self, index: int) -> None:
        self.right_tabs.setVisible(True)
        self.right_tabs.setCurrentWidget(self.settings_panel)
        self.settings_panel.tabs.setCurrentIndex(max(0, min(index, self.settings_panel.tabs.count() - 1)))
        self.btn_toggle_panel.setChecked(True)

    def _apply_font_command(self, ui_id: str) -> None:
        panel = self.settings_panel
        index = panel.ui_font_combo.findData(ui_id)
        if index >= 0:
            panel.ui_font_combo.setCurrentIndex(index)
            panel.save()

    def _apply_scale_command(self, scale: float) -> None:
        panel = self.settings_panel
        index = panel.font_scale_combo.findData(scale)
        if index >= 0:
            panel.font_scale_combo.setCurrentIndex(index)
            panel.save()

    def _toggle_setting_widget(self, attr: str) -> None:
        panel_widget = getattr(self.settings_panel, attr, None)
        if panel_widget is not None:
            panel_widget.setChecked(not panel_widget.isChecked())
            self.settings_panel.save()
            return
        window_widget = getattr(self, attr, None)
        if window_widget is not None:
            window_widget.setChecked(not window_widget.isChecked())

    def show_command_palette(self) -> None:
        commands: list[tuple[str, str, str, object]] = []

        def add(group: str, name: str, shortcut: str, callback) -> None:
            commands.append((group, name, shortcut, callback))

        add("操作", "添加文件", "", self.add_files)
        add("操作", "添加文件夹", "Ctrl+O", self.add_folder)
        add("操作", "一键全自动", "Ctrl+Return", self.run_auto)
        add("操作", "仅本地处理", "", self.run_local_only)
        add("操作", "保存全部变更", "", self.confirm_save_all)
        add("操作", "真假无损鉴定", "", self.run_authenticity)
        add("操作", "清理假无损", "Ctrl+Shift+D", self.run_cleanup)
        add("操作", "曲库体检", "", self.open_health)
        add("操作", "分轨导出（CUE / SACD ISO）", "", self.run_split_cue)
        add("操作", "编辑标签", "Ctrl+E", self.edit_selected)
        add("操作", "批量编辑标签", "", self.batch_edit_selected)
        add("操作", "撤销标签编辑", "Ctrl+Z", self.undo_last_tag_edit)
        add("操作", "查看频谱", "", self.show_spectrum)
        add("操作", "重命名预览", "", self.rename_preview)
        add("操作", "导出处理报表", "", self.export_report)
        add("操作", "批量导出封面", "", self.export_covers)
        add("操作", "隔离区管理", "", self.open_quarantine)
        add("操作", "收起 / 展开日志", "", self._toggle_log)
        add("操作", "关于音序管家", "F1", self.show_about)

        for index, (title, _) in enumerate(("匹配", "歌词", "写入", "外观")):
            add("设置", f"打开参数页：{title}", "", lambda i=index: self._show_settings_tab(i))
        add("设置", "侧栏显示 / 隐藏", "Ctrl+P", self.toggle_settings_panel)

        add("筛选", "判定：全部", "", lambda: self._select_verdict_filter("all"))
        add("筛选", "判定：真无损", "", lambda: self._select_verdict_filter("true"))
        add("筛选", "判定：假无损", "", lambda: self._select_verdict_filter("fake"))
        add("筛选", "判定：假无损 + 可疑", "", lambda: self._select_verdict_filter("suspect_any"))
        add("筛选", "判定：无法判定", "", lambda: self._select_verdict_filter("unknown"))
        add("筛选", "只看无损（开关）", "", lambda: self._toggle_setting_widget("lossless_check"))
        add("筛选", "只看问题（开关）", "", lambda: self._toggle_setting_widget("problems_check"))

        for name in theme.THEMES:
            add("外观", f"主题：{i18n.tr_theme(name)}", "", lambda n=name: self.apply_theme(n))
        for choice in fonts.UI_CHOICES:
            add("外观", f"界面字体：{choice.name}", "", lambda f=choice.id: self._apply_font_command(f))
        for label, value in (("90%", 0.9), ("100%", 1.0), ("110%", 1.1), ("125%", 1.25)):
            add("外观", f"字号：{label}", "", lambda v=value: self._apply_scale_command(v))

        add("设置", "递归扫描（开关）", "", lambda: self._toggle_setting_widget("recursive_check"))
        add("设置", "清理时包含「疑似」（开关）", "", lambda: self._toggle_setting_widget("suspect_check"))
        add("设置", "每个文件夹生成封面（开关）", "", lambda: self._toggle_setting_widget("cover_check"))
        add("设置", "自动重命名（开关）", "", lambda: self._toggle_setting_widget("rename_check"))
        add("设置", "兼容模式 ID3v2.3（开关）", "", lambda: self._toggle_setting_widget("compat_check"))
        add("设置", "联网匹配（开关）", "", lambda: self._toggle_setting_widget("online_check"))

        dialog = CommandPaletteDialog(commands, self)
        if dialog.exec() == CommandPaletteDialog.Accepted and dialog.selected_command:
            dialog.selected_command()

    def _open_log_dir(self) -> None:
        import os as _os

        try:
            _os.startfile(self._log_dir_hint())  # type: ignore[attr-defined]
        except OSError as exc:
            self._append_log("ERROR", f"打开日志目录失败：{exc}")

    # ------------------------------------------------------------ 语言

    def apply_language(self, code: str) -> None:
        if code not in i18n.locale_names():
            return
        i18n.set_locale(code)
        self.settings.language = code

        for btn, key in self._i18n_buttons.items():
            btn.setText(i18n.tr(key))
        for key, title_key in self._i18n_stat_keys.items():
            self._stat_cards[key].title_label.setText(i18n.tr(title_key))

        # 判定筛选胶囊：只更新文字，数量和当前选择保持不变
        self._update_verdict_chips()

        # 菜单标题与主题项
        self.lang_menu.setTitle(i18n.tr("menu_language"))
        self.theme_menu.setTitle(i18n.tr("menu_theme"))
        for name, action in self._theme_actions.items():
            action.setText(i18n.tr_theme(name))
        for locale_code, action in self._lang_actions.items():
            blocked = action.blockSignals(True)
            action.setChecked(locale_code == code)
            action.blockSignals(blocked)

        self.model.refresh_all()
        try:
            self.settings.save()
        except OSError:
            pass
        self._append_log("INFO", f"界面语言已切换：{i18n.locale_names()[code]}")

    # ------------------------------------------------------------ 主题

    def _preview_appearance(self, ui_id: str, mono_id: str, scale: float) -> None:
        """字体/字号即时预览：只改当前会话，点「保存参数」后才持久化。"""
        fonts.configure(ui_id, mono_id, scale)
        app = QApplication.instance()
        if app is not None:
            app.setFont(fonts.ui_font(13))
        self.apply_theme(self.settings.theme, persist=False, quiet=True)
        self.status_hint.setText("字体已预览，点击「保存参数」后生效")

    def apply_theme(self, name: str, *, persist: bool = True, quiet: bool = False) -> None:
        if name not in theme.THEMES:
            return

        qss = theme.apply_theme(name)
        QApplication.instance().setStyleSheet(qss)
        self.surface.set_theme(name)
        self.settings.theme = name
        for theme_name, action in self._theme_actions.items():
            blocked = action.blockSignals(True)
            action.setChecked(theme_name == name)
            action.blockSignals(blocked)
        self.model.refresh_all()
        for card in self._stat_cards.values():
            card.restyle()
        if getattr(self, "inspector", None) is not None:
            self.inspector.refresh()
        try:
            from .win_effects import apply_titlebar

            apply_titlebar(
                int(self.winId()),
                dark=not theme.is_light(name),
                caption=theme.BG_BASE,
                border=theme.LINE,
            )
        except Exception:  # noqa: BLE001
            pass
        self.brand_bar.update()
        self.update()
        if persist:
            try:
                self.settings.save()
            except OSError:
                pass
        if not quiet:
            self._append_log("INFO", f"主题已切换：{name}")

    def _update_verdict_chips(self) -> None:
        for label_key, key in self._verdict_filter_items:
            text = i18n.tr(label_key)
            count = self._verdict_counts.get(key, 0)
            if count:
                text = f"{text}  {count}"
            self.verdict_chips[key].setText(text)

    def _on_spec_filter(self) -> None:
        self.proxy.set_spec_filter(str(self.spec_combo.currentData()))

    # ------------------------------------------------------------ 封面导出

    def export_covers(self) -> None:
        from ..core.cover import export_covers

        tracks = [t for t in self.model.tracks()
                  if (t.working.cover or t.original.cover)]
        if not tracks:
            QMessageBox.information(self, "没有封面", "列表中没有任何封面。\n"
                                   "可先联网匹配获取封面，或导入已内嵌封面的文件。")
            return
        folder = QFileDialog.getExistingDirectory(self, "选择导出目录")
        if not folder:
            return
        exported, skipped = export_covers(tracks, Path(folder))
        self._append_log("INFO", f"已导出 {exported} 张封面到 {folder}")
        QMessageBox.information(
            self, "导出完成", f"成功导出 {exported} 张封面，跳过 {skipped} 个无封面文件。"
        )

    # ------------------------------------------------------------ 文件夹封面

    def run_folder_covers(self) -> None:
        """独立操作：为每个文件夹生成一张 cover（专辑封面 → 歌手照片）。"""
        from ..core.model import TrackStatus

        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "批处理正在运行，先等它完成。")
            return
        if self.cover_worker is not None and self.cover_worker.isRunning():
            QMessageBox.information(self, "请稍候", "文件夹封面正在生成中。")
            return

        tracks = [
            t for t in self.model.tracks()
            if t.status is not TrackStatus.UNSUPPORTED and t.path.exists()
        ]
        if not tracks:
            QMessageBox.information(self, "没有曲目", "请先添加音频文件。")
            return

        self._set_running(True)
        self.progress.setMaximum(max(1, len(tracks)))
        self.progress.setValue(0)
        self.brand_bar.set_status("生成封面", theme.CYAN)
        self._append_log("INFO", f"开始为 {len(tracks)} 首所在文件夹生成封面")

        self.cover_worker = CoverWorker(self.settings, tracks)
        self.cover_worker.progressChanged.connect(self._on_cover_progress)
        self.cover_worker.summaryReady.connect(self._on_cover_summary)
        self.cover_worker.logMessage.connect(self._append_log)
        self.cover_worker.finished.connect(self._on_folder_covers_finished)
        self.cover_worker.failed.connect(lambda m: self._append_log("ERROR", f"文件夹封面失败：{m}"))
        self.cover_worker.start()

    def _on_folder_covers_finished(self) -> None:
        self._set_running(False)
        self.brand_bar.set_status("就绪", theme.FG_DIM)
        self.model.refresh_all()
        self._refresh_stats()

    # ------------------------------------------------------------ CUE 分轨导出

    def run_split_cue(self) -> None:
        """分轨导出：把整轨 WAV/APE/FLAC + CUE 切成 FLAC，或把 SACD ISO 导出为 DSF。"""
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "批处理正在运行，先等它完成。")
            return
        if self.split_worker is not None and self.split_worker.isRunning():
            QMessageBox.information(self, "请稍候", "分轨导出正在进行中。")
            return

        # 选中项或全部里带多轨 CUE 的整轨文件
        scope = self._selected_tracks() or self.model.tracks()
        targets = [
            t for t in scope
            if t.cue is not None and len(t.cue.tracks) > 1 and t.path.exists()
        ]
        if not targets:
            QMessageBox.information(
                self, "没有可导出的整轨",
                "请先添加带 CUE 索引的整轨文件（WAV/APE/FLAC + CUE）或 SACD ISO，"
                "或选中它们。",
            )
            return

        # 选输出目录
        first = targets[0]
        default = str(first.path.parent)
        folder = QFileDialog.getExistingDirectory(
            self, "选择分轨导出目录", self.settings.last_folder or default
        )
        if not folder:
            return

        self._set_running(True)
        self._split_stopped = False
        self.progress.setMaximum(max(1, sum(len(t.cue.tracks) for t in targets)))
        self.progress.setValue(0)
        self.brand_bar.set_status("分轨导出", theme.CYAN)
        self._append_log("INFO", f"开始分轨导出 {len(targets)} 个整轨文件")

        self.split_worker = SplitCueWorker(self.settings, targets, Path(folder))
        self.split_worker.progressChanged.connect(self._on_split_progress)
        self.split_worker.summaryReady.connect(self._on_split_summary)
        self.split_worker.logMessage.connect(self._append_log)
        self.split_worker.finished.connect(self._on_split_finished)
        self.split_worker.failed.connect(lambda m: self._append_log("ERROR", f"分轨导出失败：{m}"))
        self.split_worker.start()

    def _on_split_progress(self, index: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(index)
        self.status_hint.setText(f"分轨导出 {index} / {total}")

    def _on_split_summary(self, summary) -> None:
        self.status_hint.setText(summary.text())

    def _on_split_finished(self) -> None:
        if self._split_stopped:
            self._append_log("WARNING", "分轨导出已停止")
            self._split_stopped = False
        self._set_running(False)
        self.brand_bar.set_status("就绪", theme.FG_DIM)

    # ------------------------------------------------------------ 重命名 / 报表

    def rename_preview(self) -> None:
        tracks = [t for t in self.model.tracks() if t.status is not TrackStatus.UNSUPPORTED]
        if not tracks:
            QMessageBox.information(self, "没有曲目", "请先添加音频文件。")
            return
        self._run_rename(tracks, scope="列表全部曲目")

    def rename_folder(self) -> None:
        """按标签统一重命名「选中项所在目录」的全部曲目。

        与「重命名预览」的区别只在范围：文件名与标签的一致性通常是**按专辑**
        对齐的（一张专辑一套命名规则），逐首右键去改既慢又容易漏，所以这里
        以选中项所在目录为单位整批处理。
        """
        selected = self._selected_tracks()
        if not selected:
            return
        folders = {t.path.parent for t in selected}
        tracks = [
            t for t in self.model.tracks()
            if t.path.parent in folders and t.status is not TrackStatus.UNSUPPORTED
        ]
        if not tracks:
            QMessageBox.information(self, "没有曲目", "所选目录下没有可重命名的曲目。")
            return
        name = next(iter(folders)).name if len(folders) == 1 else f"{len(folders)} 个目录"
        self._run_rename(tracks, scope=f"{name}（{len(tracks)} 首）")

    def _run_rename(self, tracks: list[Track], scope: str) -> None:
        """按当前模板生成计划 → 预览 → 执行。"""
        self.settings_panel.apply_to(self.settings)
        bad = rename_mod.unknown_fields(self.settings.rename_template)
        if bad:
            QMessageBox.warning(
                self, "模板字段无效",
                "重命名模板里有无法识别的字段："
                + "、".join(f"{{{b}}}" for b in bad)
                + "\n\n这些字段会被替换成空，文件名会缺段。"
                "请先在参数面板里改正模板（可用字段：歌手 / 歌名 / 专辑 / 曲目 / 年份 等）。",
            )
            return
        plan = rename_mod.build_plan(tracks, self.settings.rename_template)
        if not plan.pending and not plan.blocked:
            QMessageBox.information(
                self, "无需重命名",
                f"{scope}：按当前模板，所有文件命名已符合规则。",
            )
            return
        dialog = RenamePreviewDialog(plan, self)
        if dialog.exec() != RenamePreviewDialog.Accepted:
            return
        success, failed = rename_mod.execute_plan(plan)
        for item in plan.items:
            if item.changed and item.ok:
                old_lrc = lyrics_mod.sidecar_path(item.old_path)
                if old_lrc.exists():
                    try:
                        old_lrc.rename(lyrics_mod.sidecar_path(item.new_path))
                    except OSError:
                        pass
        self.model.refresh_all()
        self._append_log("INFO", f"重命名完成（{scope}）：成功 {success}，失败 {failed}")
        QMessageBox.information(self, "完成", f"成功 {success} 项，失败 {failed} 项。")

    def export_report(self) -> None:
        tracks = self.model.tracks()
        if not tracks:
            QMessageBox.information(self, "没有数据", "列表为空，无可导出内容。")
            return
        default = str(Path(self.settings.last_folder or ".") / report_mod.default_report_name())
        path, _ = QFileDialog.getSaveFileName(self, "导出处理报表", default, "CSV (*.csv)")
        if not path:
            return
        try:
            report_mod.export_csv(tracks, path)
        except OSError as exc:
            QMessageBox.critical(self, "导出失败", str(exc))
            return
        self._append_log("INFO", f"报表已导出：{path}")
        QMessageBox.information(self, "导出完成", f"已保存到：\n{path}")

    # ------------------------------------------------------------ 设置

    def _on_settings_changed(self) -> None:
        # 参数修改只标记「未保存」，不再自动落盘；用户点「保存参数」才生效
        self.status_hint.setText("参数已修改，点击右侧「保存参数」才会生效")

    def _on_settings_saved(self) -> None:
        self.status_hint.setText("参数已保存")

    # ------------------------------------------------------------ 日志与状态

    def _append_log(self, level: str, message: str) -> None:
        color = LEVEL_COLORS.get(level.upper(), theme.FG_DIM)
        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendHtml(
            f'<span style="color:{theme.FG_MUTE}">{stamp}</span>'
            f'<span style="color:{color}">&nbsp;&nbsp;{_escape(message)}</span>'
        )
        bar = self.log_view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _refresh_stats(self) -> None:
        tracks = self.model.tracks()
        stats = report_mod.summarize(tracks)
        lossless = sum(1 for t in tracks if t.info.lossless)
        suspect = sum(1 for t in tracks if t.authenticity is not None
                      and t.authenticity.is_suspicious)
        values = {
            "total": len(tracks),
            "lossless": lossless,
            "suspect": suspect,
            "done": stats.get("完成", 0),
            "review": stats.get("待人工确认", 0),
            "failed": stats.get("失败", 0),
        }
        for key, value in values.items():
            card = self._stat_cards.get(key)
            if card is not None:
                card.set_value(value)

        from ..core.authenticity import group_of

        counts = {key: 0 for _, key in self._verdict_filter_items}
        counts["all"] = len(tracks)
        for track in tracks:
            group = (
                group_of(track.authenticity.verdict)
                if track.authenticity is not None
                else "unknown"
            )
            if group == "true":
                counts["true"] += 1
            elif group == "fake":
                counts["fake"] += 1
                counts["suspect_any"] += 1
            elif group == "suspect":
                counts["suspect_any"] += 1
            else:
                counts["unknown"] += 1
        self._verdict_counts = counts
        self._update_verdict_chips()

        self.empty_state.setVisible(not tracks)
        if not tracks:
            self.status_hint.setText("拖入音频文件，或点击「添加文件夹」")
        else:
            hint = (
                f"共 {len(tracks)} 首 · 无损 {lossless} · 完成 {values['done']}"
                f" · 待确认 {values['review']} · 失败 {values['failed']}"
            )
            if suspect:
                hint += f" · 假无损 {suspect}"
            self.status_hint.setText(hint)

    def _on_stat_clicked(self, key: str) -> None:
        if key == "total":
            self.search_edit.clear()
            self.lossless_check.setChecked(False)
            self.problems_check.setChecked(False)
        elif key == "lossless":
            self.lossless_check.setChecked(not self.lossless_check.isChecked())
        elif key == "suspect":
            self._select_verdict_filter("suspect_any")
        elif key in ("review", "failed"):
            if not self.problems_check.isChecked():
                self.problems_check.setChecked(True)

    def _log_dir_hint(self) -> str:
        try:
            from ..core.paths import log_dir

            return str(log_dir())
        except OSError:
            return "(不可写)"

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.worker is not None and self.worker.isRunning():
            reply = QMessageBox.question(
                self,
                "任务进行中",
                "批处理尚未完成，确定退出吗？\n（已写入的文件不会回滚）",
            )
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.worker.cancel()
            self.worker.wait(3000)
        if self.scan_worker is not None and self.scan_worker.isRunning():
            self.scan_worker.requestInterruption()
            self.scan_worker.wait(2000)
        if (
            self.revert_scan_worker is not None
            and self.revert_scan_worker.isRunning()
        ):
            self.revert_scan_worker.requestInterruption()
            self.revert_scan_worker.wait(2000)
        # 未保存的参数修改：明确让用户选择，绝不静默自动保存 ——
        # 否则「保存才生效」就是一句空话，关窗口一样能改配置
        if self.settings_panel.is_dirty():
            box = QMessageBox(self)
            box.setWindowTitle("有未保存的参数")
            box.setIcon(QMessageBox.Question)
            box.setText("参数面板有未保存的修改，退出前要保存吗？")
            save_btn = box.addButton("保存并退出", QMessageBox.AcceptRole)
            box.addButton("不保存", QMessageBox.DestructiveRole)
            cancel_btn = box.addButton("取消", QMessageBox.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked is cancel_btn:
                event.ignore()
                return
            if clicked is save_btn:
                self.settings_panel.save()
        self._save_view_state()
        remove_listener(self._log_listener)
        event.accept()

    def _save_view_state(self) -> None:
        """退出时保存列宽、隐藏列与排序，下次打开保持原样。"""
        header = self.table.horizontalHeader()
        count = self.table.model().columnCount()
        self.settings.column_widths = [header.sectionSize(i) for i in range(count)]
        self.settings.hidden_columns = [
            i for i in range(count) if self.table.isColumnHidden(i)
        ]
        self.settings.sort_column = header.sortIndicatorSection()
        self.settings.sort_order = 1 if header.sortIndicatorOrder() == Qt.DescendingOrder else 0
        self.settings.view_state_saved = True
        try:
            self.settings.save()
        except OSError:
            pass


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
