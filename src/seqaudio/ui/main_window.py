"""主窗口：品牌条 + 数据卡片 + 曲目表格 + 参数面板 + 运行日志。"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
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
    QStackedLayout,
    QStatusBar,
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
from ..core.model import Track, TrackStatus
from ..pipeline.processor import BatchOptions
from . import theme
from .delegate import TrackDelegate
from .dialogs import CandidateDialog, RenamePreviewDialog, TagEditDialog
from .settings_panel import SettingsPanel
from .track_model import (
    COLUMNS,
    HIDDEN_BY_DEFAULT,
    STRETCH_COLUMN,
    TrackFilterProxy,
    TrackTableModel,
)
from .space import SpaceView
from .widgets import BrandBar, EmptyState, SearchInput, SectionHeader, StatCard
from .worker import AuthenticityWorker, MatchPreviewWorker, ProcessorWorker, ScanWorker

log = logging.getLogger(__name__)

LEVEL_COLORS = {
    "ERROR": theme.BAD,
    "CRITICAL": theme.BAD,
    "WARNING": theme.WARN,
    "INFO": theme.FG_DIM,
    "DEBUG": theme.FG_MUTE,
}


class LogBridge(QObject):
    """把 logging 回调安全地转成 Qt 信号（回调可能来自工作线程）。"""

    message = Signal(str, str)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.settings = settings
        self.presets = PresetStore()
        self.worker: Optional[ProcessorWorker] = None
        self.scan_worker: Optional[ScanWorker] = None
        self._match_workers: list[MatchPreviewWorker] = []
        self.auth_worker: Optional[AuthenticityWorker] = None
        self._stat_cards: dict[str, StatCard] = {}

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
        add_listener(self._log_listener)
        self._append_log("INFO", f"日志目录：{self._log_dir_hint()}")
        if settings.offline_mode:
            self._append_log("WARNING", "当前为离线模式：仅做本地标签编辑、水印清理与重命名")

    # ------------------------------------------------------------ 布局

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)

        # 两层堆叠：底层是持续飞行的星空，上层是半透明界面。
        # 用 QStackedLayout(StackAll) 而不是自绘 paintEvent —— 这样星场是个
        # 真正的 widget，有自己的计时器，也不会被中央控件的背景覆盖掉。
        stack = QStackedLayout(central)
        stack.setStackingMode(QStackedLayout.StackingMode.StackAll)
        stack.setContentsMargins(0, 0, 0, 0)

        self.space = SpaceView(star_count=760)
        self.space.sceneChanged.connect(self._on_scene_changed)
        self.space.set_running(self.settings.space_background)
        stack.addWidget(self.space)

        surface = QWidget()
        surface.setObjectName("appSurface")
        stack.addWidget(surface)
        stack.setCurrentWidget(surface)

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
        self.settings_panel.spaceToggled.connect(self.toggle_space_background)
        QTimer.singleShot(0, lambda: self.btn_toggle_panel.setChecked(True))
        splitter.addWidget(self.settings_panel)
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
        self.scene_label = QLabel("")
        self.scene_label.setStyleSheet(f"color:{theme.FG_MUTE}; font-size:11px;")
        self.scene_label.setToolTip("当前星区（背景每 24 秒切换一次）")
        status.addPermanentWidget(self.scene_label)
        self.mode_label = QLabel("")
        self.mode_label.setStyleSheet(f"color:{theme.FG_MUTE};")
        status.addPermanentWidget(self.mode_label)

    def _build_action_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.btn_add_files = self._button("添加文件", self.add_files, ghost=True)
        self.btn_add_folder = self._button("添加文件夹", self.add_folder, ghost=True)
        self.btn_remove = self._button("移除", self.remove_selected, ghost=True)
        # 「清空」放进「更多」菜单：1080px 下 8 个按钮已接近一行的极限，
        # 清空属于低频且可撤销性差的操作，不适合摆在手边
        for btn in (self.btn_add_files, self.btn_add_folder, self.btn_remove):
            layout.addWidget(btn)

        layout.addWidget(self._vline())

        self.btn_auto = self._button("一键全自动", self.run_auto)
        self.btn_auto.setObjectName("primary")
        self.btn_auto.setToolTip("匹配 → 补全 → 清理水印 → 写标签 → 歌词（按当前参数）")
        self.btn_local = self._button("仅本地处理", self.run_local_only, ghost=True)
        self.btn_local.setToolTip("不联网：清理水印、补全空白字段、写标签、重命名")
        self.btn_stop = self._button("停止", self.stop, ghost=True)
        self.btn_stop.setEnabled(False)
        layout.addWidget(self.btn_auto)
        layout.addWidget(self.btn_local)
        layout.addWidget(self.btn_stop)

        layout.addWidget(self._vline())

        self.btn_auth = self._button("真假鉴定", self.run_authenticity, ghost=True)
        self.btn_auth.setToolTip("频谱分析判定是否真无损（需解码，速度约 0.3~1 秒/首）")
        self.btn_cleanup = self._button("清理假无损", self.run_cleanup, ghost=True)
        self.btn_cleanup.setObjectName("danger")
        self.btn_cleanup.setToolTip("把判定为假无损/可疑的文件移入回收站或隔离区（Ctrl+Shift+D）")
        layout.addWidget(self.btn_auth)
        layout.addWidget(self.btn_cleanup)

        layout.addStretch(1)

        self.btn_more = self._button("更多", self._show_more_menu, ghost=True)
        self.btn_more.setToolTip("重命名预览、导出报表、隔离区、关于")
        self.btn_more.setMenu(self._build_more_menu())
        layout.addWidget(self.btn_more)

        # 窄屏救星：收起参数面板，表格独占整宽
        self.btn_toggle_panel = self._button("参数", self.toggle_settings_panel, ghost=True)
        self.btn_toggle_panel.setCheckable(True)
        self.btn_toggle_panel.setToolTip("显示 / 隐藏右侧参数面板（Ctrl+P）")
        layout.addWidget(self.btn_toggle_panel)
        return bar

    def _build_more_menu(self) -> QMenu:
        menu = QMenu(self)

        self.action_rename = menu.addAction("重命名预览…", self.rename_preview)
        self.action_report = menu.addAction("导出处理报表…", self.export_report)
        menu.addAction("清空列表", self.clear_all)
        menu.addSeparator()
        self.action_quarantine = menu.addAction("隔离区管理…", self.open_quarantine)
        self.action_spectrum = menu.addAction("查看频谱…", self.show_spectrum)
        menu.addSeparator()
        self.action_space = menu.addAction("动态星空背景")
        self.action_space.setCheckable(True)
        self.action_space.setChecked(self.settings.space_background)
        self.action_space.toggled.connect(self.toggle_space_background)
        menu.addAction("打开日志目录", self._open_log_dir)
        menu.addSeparator()
        menu.addAction("关于音序管家…", self.show_about)
        return menu

    def _show_more_menu(self) -> None:
        """按钮自带的菜单在点击时弹出；这里只负责把勾选状态同步成最新值。"""
        self.action_space.setChecked(self.settings.space_background)

    def toggle_settings_panel(self) -> None:
        visible = not self.settings_panel.isVisible()
        self.settings_panel.setVisible(visible)
        self.btn_toggle_panel.setChecked(visible)

    def _button(self, text: str, slot, ghost: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setFixedHeight(34)
        if ghost:
            btn.setObjectName("ghost")
        btn.clicked.connect(slot)
        return btn

    @staticmethod
    def _vline() -> QFrame:
        line = QFrame()
        line.setFixedSize(1, 20)
        line.setStyleSheet(f"background:{theme.LINE}; border:none;")
        return line

    def _build_stat_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        specs = [
            ("total", "曲目总数", theme.FG),
            ("lossless", "无损音源", theme.CYAN),
            ("suspect", "假无损", theme.BAD),
            ("done", "已完成", theme.OK),
            ("review", "待确认", theme.WARN),
            ("failed", "失败", theme.BAD),
        ]
        for key, title, color in specs:
            card = StatCard(key, title, color)
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

        self.verdict_combo = QComboBox()
        self.verdict_combo.setFixedHeight(34)
        self.verdict_combo.setMinimumWidth(118)
        for label, key in (
            ("全部判定", "all"),
            ("真无损", "true"),
            ("假无损", "fake"),
            ("可疑（升频/低码）", "suspect"),
            ("假无损 + 可疑", "suspect_any"),
            ("未鉴定", "unknown"),
        ):
            self.verdict_combo.addItem(label, key)
        self.verdict_combo.currentIndexChanged.connect(self._on_verdict_filter)
        filter_row.addWidget(self.verdict_combo)
        layout.addLayout(filter_row)

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
        for index, (_, _, width) in enumerate(COLUMNS):
            self.table.setColumnWidth(index, width)
        self.table.horizontalHeader().setSectionResizeMode(STRETCH_COLUMN, QHeaderView.Stretch)
        for index in HIDDEN_BY_DEFAULT:
            self.table.setColumnHidden(index, True)
        self.table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.horizontalHeader().customContextMenuRequested.connect(self._header_menu)
        self.table.doubleClicked.connect(lambda _: self.edit_selected())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)

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
        self.btn_log_clear = QPushButton("清空")
        self.btn_log_clear.setCursor(Qt.PointingHandCursor)
        self.btn_log_clear.setFixedHeight(22)
        self.btn_log_clear.setStyleSheet(
            f"QPushButton{{background:transparent;border:1px solid {theme.LINE_SOFT};"
            f"border-radius:6px;color:{theme.FG_MUTE};padding:1px 10px;font-size:11px;}}"
            f"QPushButton:hover{{color:{theme.FG};border-color:{theme.LINE};}}"
        )
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

    def _init_shortcuts(self) -> None:
        QShortcut(QKeySequence.Open, self, self.add_folder)
        QShortcut(QKeySequence.Delete, self, self.remove_selected)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.run_auto)
        QShortcut(QKeySequence("Ctrl+E"), self, self.edit_selected)
        QShortcut(QKeySequence("Ctrl+P"), self, self.toggle_settings_panel)
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
            "音频 (*.flac *.ape *.wav *.wv *.tta *.m4a *.dsf *.dff *.mp3 *.aac *.ogg *.opus);;所有文件 (*.*)",
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
        self.scan_worker.failed.connect(lambda msg: self._append_log("ERROR", f"扫描失败：{msg}"))
        self.scan_worker.start()

    def _on_scan_finished(self, tracks: list[Track]) -> None:
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
            )
        )

    def _ensure_ready(self) -> bool:
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "已有任务在运行中。")
            return False
        if not self.model.tracks():
            QMessageBox.information(self, "没有曲目", "请先添加音频文件或文件夹。")
            return False
        return True

    def _start_worker(self, options: BatchOptions) -> None:
        self.settings_panel.apply_to(self.settings)
        self.settings.save()

        tracks = self.model.tracks()
        self._set_running(True)
        self.progress.setMaximum(len(tracks))
        self.progress.setValue(0)
        self.mode_label.setText("联网模式" if options.match_online else "离线模式")
        self._append_log(
            "INFO",
            f"开始处理 {len(tracks)} 首（{'联网匹配' if options.match_online else '仅本地'}，"
            f"覆盖策略：{self.settings.conflict()}）",
        )

        self.worker = ProcessorWorker(self.settings, tracks, options)
        self.worker.trackUpdated.connect(self.model.refresh_row)
        self.worker.trackUpdated.connect(lambda _: self._refresh_stats())
        self.worker.progressChanged.connect(self._on_progress)
        self.worker.writeProgress.connect(self._on_write_progress)
        self.worker.phaseChanged.connect(self._on_phase)
        self.worker.finishedRun.connect(self._on_finished)
        self.worker.start()

    def _set_running(self, running: bool) -> None:
        self.progress.setVisible(running)
        self.btn_auto.setEnabled(not running)
        self.btn_local.setEnabled(not running)
        self.btn_auth.setEnabled(not running)
        self.btn_stop.setEnabled(running)
        self.brand_bar.set_status(
            "处理中" if running else "就绪", theme.PURPLE if running else theme.FG_DIM
        )

    def stop(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.btn_stop.setEnabled(False)
            self._append_log("WARNING", "已请求停止，正在结束当前文件…")

    def _on_progress(self, index: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(index)
        self.status_hint.setText(f"处理中 {index} / {total}")

    def _on_write_progress(self, index: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(index)
        self.status_hint.setText(f"写入标签 {index} / {total}")

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
        track = tracks[0]
        dialog = TagEditDialog(track, self)
        if dialog.exec() == TagEditDialog.Accepted:
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

    def open_folder(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            return
        import os

        try:
            os.startfile(str(tracks[0].path.parent))  # type: ignore[attr-defined]
        except OSError as exc:
            self._append_log("ERROR", f"打开目录失败：{exc}")

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

        menu = QMenu(self)
        menu.addAction("编辑标签\tCtrl+E", self.edit_selected)
        menu.addAction("查看候选匹配", self.show_candidates)
        menu.addAction("撤销本地修改", self.revert_selected)
        menu.addSeparator()
        menu.addAction("查看频谱", self.show_spectrum)
        menu.addAction("移入隔离区", self._quarantine_selected)
        menu.addSeparator()
        menu.addAction("打开所在文件夹", self.open_folder)
        menu.addAction("仅处理选中项", self._run_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _select_verdict_filter(self, key: str) -> None:
        for index in range(self.verdict_combo.count()):
            if self.verdict_combo.itemData(index) == key:
                self.verdict_combo.setCurrentIndex(index)
                return

    def _quarantine_selected(self) -> None:
        """右键直接把选中项送进隔离区（不弹清单，仅对已鉴定的项生效）。"""
        from ..core.quarantine import QuarantineStore

        tracks = [t for t in self._selected_tracks() if t.path.exists()]
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
        removed = [t for t in tracks if not t.path.exists()]
        self.model.remove_tracks(removed)
        self._refresh_stats()
        self._append_log("WARNING", f"已隔离 {len(created)} 个文件，{len(failed)} 个失败")

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
        if not targets:
            QMessageBox.information(
                self, "没有可鉴定的曲目",
                "无损鉴定只对有损/无损判断意义，请先添加 FLAC / APE / WAV / DSD 等文件。",
            )
            return

        self._set_running(True)
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
        track.authenticity = result
        self.model.refresh_row(track)
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
        msg = f"鉴定完成：真无损 {true_count}，可疑 {suspect}，无法判定 {unknown}"
        self._append_log("WARNING" if suspect else "INFO", msg)
        self.status_hint.setText(msg)
        if suspect:
            self._append_log("INFO", "已自动勾选「只看问题」，可从表格筛出可疑项")

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
            removed = [t for t in candidates if not t.path.exists()]
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
            removed = [t for t in candidates if not t.path.exists()]
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
                removed = [t for t in candidates if not t.path.exists()]
                self._append_log("WARNING", f"已隔离 {len(created)} 个文件（可还原）")
            else:
                removed = [t for t in candidates if not t.path.exists()]
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

    def _open_log_dir(self) -> None:
        import os as _os

        try:
            _os.startfile(self._log_dir_hint())  # type: ignore[attr-defined]
        except OSError as exc:
            self._append_log("ERROR", f"打开日志目录失败：{exc}")

    # ------------------------------------------------------------ 星空背景

    def toggle_space_background(self, enabled: bool) -> None:
        self.space.set_running(enabled)
        self.settings.space_background = enabled
        self.scene_label.setVisible(enabled)
        # 菜单项与参数面板是同一开关的两个入口，要互相同步且不能互相触发
        for widget in (getattr(self, "action_space", None),
                       getattr(self.settings_panel, "space_check", None)):
            if widget is None:
                continue
            blocked = widget.blockSignals(True)
            widget.setChecked(enabled)
            widget.blockSignals(blocked)
        try:
            self.settings.save()
        except OSError:
            pass
        self._append_log("INFO", f"星空背景已{'启用' if enabled else '关闭（省电模式）'}")

    def _on_scene_changed(self, name: str) -> None:
        self.scene_label.setText(f"星区 · {name}")

    def _on_verdict_filter(self) -> None:
        self.proxy.set_verdict_group(str(self.verdict_combo.currentData()))

    # ------------------------------------------------------------ 重命名 / 报表

    def rename_preview(self) -> None:
        tracks = [t for t in self.model.tracks() if t.status is not TrackStatus.UNSUPPORTED]
        if not tracks:
            QMessageBox.information(self, "没有曲目", "请先添加音频文件。")
            return
        self.settings_panel.apply_to(self.settings)
        plan = rename_mod.build_plan(tracks, self.settings.rename_template)
        if not plan.pending and not plan.blocked:
            QMessageBox.information(self, "无需重命名", "按当前模板，所有文件命名已符合规则。")
            return
        dialog = RenamePreviewDialog(plan, self)
        if dialog.exec() == RenamePreviewDialog.Accepted:
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
            self._append_log("INFO", f"重命名完成：成功 {success}，失败 {failed}")
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
        self.settings_panel.apply_to(self.settings)
        QTimer.singleShot(400, self._save_settings_debounced)

    def _save_settings_debounced(self) -> None:
        try:
            self.settings.save()
        except OSError as exc:
            self._append_log("ERROR", f"设置保存失败：{exc}")

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
        try:
            self.settings_panel.apply_to(self.settings)
            self.settings.save()
        except OSError:
            pass
        remove_listener(self._log_listener)
        event.accept()


def _escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
