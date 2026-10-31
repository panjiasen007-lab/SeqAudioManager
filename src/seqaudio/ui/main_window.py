"""主窗口：曲目列表 + 参数面板 + 运行日志 + 一键全自动。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSplitter,
    QTableView,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import lyrics as lyrics_mod
from ..core import rename as rename_mod
from ..core import report as report_mod
from ..core.config import PresetStore, Settings
from ..core.logging_setup import add_listener, remove_listener
from ..core.model import Track, TrackStatus
from ..pipeline.processor import BatchOptions
from .dialogs import CandidateDialog, RenamePreviewDialog, TagEditDialog
from .settings_panel import SettingsPanel
from .track_model import (
    COLUMNS,
    HIDDEN_BY_DEFAULT,
    STRETCH_COLUMN,
    TrackFilterProxy,
    TrackTableModel,
)
from .worker import MatchPreviewWorker, ProcessorWorker, ScanWorker

log = logging.getLogger(__name__)


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
        self._current_scan_target: list[Path] = []

        self.model = TrackTableModel()
        self.proxy = TrackFilterProxy()
        self.proxy.setSourceModel(self.model)

        self._log_bridge = LogBridge()
        self._log_bridge.message.connect(self._append_log)
        self._log_listener = lambda level, msg: self._log_bridge.message.emit(level, msg)

        self.setWindowTitle("音序管家 · 音乐元数据批量处理")
        self.resize(1360, 820)
        self.setAcceptDrops(True)
        self._build_ui()
        self._build_actions()

        add_listener(self._log_listener)
        self._append_log("INFO", f"日志文件：{self._log_file_hint()}")
        if settings.offline_mode:
            self._append_log("WARNING", "当前为离线模式：仅做本地标签编辑、水印清理与重命名")

    # ------------------------------------------------------------ 界面搭建

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 6, 8, 6)
        outer.setSpacing(6)

        outer.addWidget(self._build_toolbar())

        splitter = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        filter_row = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索歌名 / 歌手 / 专辑 / 文件名…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.proxy.set_text)
        filter_row.addWidget(self.search_edit, 1)

        self.lossless_check = QCheckBox("只看无损")
        self.lossless_check.toggled.connect(self.proxy.set_lossless_only)
        self.problems_check = QCheckBox("只看问题")
        self.problems_check.toggled.connect(self.proxy.set_problems_only)
        filter_row.addWidget(self.lossless_check)
        filter_row.addWidget(self.problems_check)
        left_layout.addLayout(filter_row)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.ExtendedSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
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
        left_layout.addWidget(self.table, 1)

        splitter.addWidget(left)

        self.settings_panel = SettingsPanel(self.settings)
        self.settings_panel.settingsChanged.connect(self._on_settings_changed)
        splitter.addWidget(self.settings_panel)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([980, 360])
        outer.addWidget(splitter, 1)

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setFixedHeight(140)
        self.log_view.setStyleSheet(
            "QPlainTextEdit{font-family:Consolas,'Cascadia Mono',monospace;font-size:11px;}"
        )
        outer.addWidget(self.log_view)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        outer.addWidget(self.progress)

        status = self.statusBar()
        self.stats_label = QLabel("就绪")
        status.addWidget(self.stats_label, 1)
        self.mode_label = QLabel("")
        status.addPermanentWidget(self.mode_label)
        self._refresh_stats()

    def _build_toolbar(self) -> QToolBar:
        bar = QToolBar("主工具栏")
        bar.setMovable(False)
        bar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(bar)
        self.toolbar = bar

        self.act_add_files = QAction("添加文件", self)
        self.act_add_files.triggered.connect(self.add_files)
        self.act_add_folder = QAction("添加文件夹", self)
        self.act_add_folder.triggered.connect(self.add_folder)
        self.act_remove = QAction("移除选中", self)
        self.act_remove.triggered.connect(self.remove_selected)
        self.act_clear = QAction("清空", self)
        self.act_clear.triggered.connect(self.clear_all)

        bar.addAction(self.act_add_files)
        bar.addAction(self.act_add_folder)
        bar.addAction(self.act_remove)
        bar.addAction(self.act_clear)
        bar.addSeparator()

        self.act_auto = QAction("一键全自动", self)
        self.act_auto.setToolTip("匹配 → 补全 → 清词水印 → 写标签 → 歌词（按当前参数）")
        self.act_auto.triggered.connect(self.run_auto)

        self.act_local = QAction("仅本地处理", self)
        self.act_local.setToolTip("不联网：清理水印、补全空白字段、写标签、重命名")
        self.act_local.triggered.connect(self.run_local_only)

        self.act_stop = QAction("停止", self)
        self.act_stop.setEnabled(False)
        self.act_stop.triggered.connect(self.stop)

        bar.addAction(self.act_auto)
        bar.addAction(self.act_local)
        bar.addAction(self.act_stop)
        bar.addSeparator()

        self.act_rename = QAction("重命名预览", self)
        self.act_rename.triggered.connect(self.rename_preview)
        self.act_report = QAction("导出报表", self)
        self.act_report.triggered.connect(self.export_report)
        bar.addAction(self.act_rename)
        bar.addAction(self.act_report)
        return bar

    def _build_actions(self) -> None:
        QShortcut(QKeySequence.Open, self, self.add_folder)
        QShortcut(QKeySequence.Delete, self, self.remove_selected)
        QShortcut(QKeySequence("Ctrl+Return"), self, self.run_auto)
        QShortcut(QKeySequence("Ctrl+E"), self, self.edit_selected)

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
        self._current_scan_target = paths
        self.stats_label.setText("正在扫描…")
        self.scan_worker = ScanWorker(paths, self.settings.recursive_scan)
        self.scan_worker.discovered.connect(
            lambda n: self.stats_label.setText(f"正在扫描… 已发现 {n} 个音频")
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
        options = BatchOptions(
            match_online=self.settings.online_enabled and not self.settings.offline_mode,
            clean_watermark=self.settings.watermark_enabled,
            write_tags=True,
            fetch_lyrics=self.settings.use_lrclib or self.settings.use_local_lyrics,
            do_rename=self.settings.rename_enabled,
        )
        self._start_worker(options)

    def run_local_only(self) -> None:
        if not self._ensure_ready():
            return
        options = BatchOptions(
            match_online=False,
            clean_watermark=self.settings.watermark_enabled,
            write_tags=True,
            fetch_lyrics=True,
            do_rename=self.settings.rename_enabled,
        )
        self._start_worker(options)

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
        self.progress.setVisible(True)
        self.progress.setMaximum(len(tracks))
        self.progress.setValue(0)
        self.act_auto.setEnabled(False)
        self.act_local.setEnabled(False)
        self.act_stop.setEnabled(True)
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
        self.worker.finishedRun.connect(self._on_finished)
        self.worker.start()

    def stop(self) -> None:
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.act_stop.setEnabled(False)
            self._append_log("WARNING", "已请求停止，正在结束当前文件…")

    def _on_progress(self, index: int, total: int) -> None:
        self.progress.setMaximum(max(1, total))
        self.progress.setValue(index)
        self.stats_label.setText(f"处理中 {index}/{total}")

    def _on_finished(self, summary) -> None:
        self.progress.setVisible(False)
        self.act_auto.setEnabled(True)
        self.act_local.setEnabled(True)
        self.act_stop.setEnabled(False)
        self.model.refresh_all()
        self._refresh_stats()
        self._append_log("INFO", summary.text())
        self.mode_label.setText("")

    # ------------------------------------------------------------ 行操作

    def _selected_rows(self) -> list[int]:
        rows: list[int] = []
        for index in self.table.selectionModel().selectedRows():
            rows.append(self.proxy.mapToSource(index).row())
        return rows

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
                f for f in ("title", "artist", "album", "albumartist", "date", "genre",
                            "track", "disc", "comment", "lyrics")
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
        worker.finished.connect(lambda: self._match_workers.remove(worker) if worker in self._match_workers else None)
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

        folder = tracks[0].path.parent
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
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
        menu.addAction("打开所在文件夹", self.open_folder)
        menu.addAction("仅处理选中项", self._run_selected)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _header_menu(self, pos) -> None:
        """表头右键：勾选显示/隐藏任意列（窄屏下按需裁列）。"""
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

    def _run_selected(self) -> None:
        tracks = self._selected_tracks()
        if not tracks:
            return
        options = BatchOptions(
            match_online=self.settings.online_enabled and not self.settings.offline_mode,
            clean_watermark=self.settings.watermark_enabled,
            write_tags=True,
            fetch_lyrics=True,
            do_rename=False,
        )
        if self.worker is not None and self.worker.isRunning():
            QMessageBox.information(self, "请稍候", "已有任务在运行中。")
            return
        self.settings_panel.apply_to(self.settings)
        self.progress.setVisible(True)
        self.progress.setMaximum(len(tracks))
        self.progress.setValue(0)
        self.act_stop.setEnabled(True)
        self.worker = ProcessorWorker(self.settings, tracks, options)
        self.worker.trackUpdated.connect(self.model.refresh_row)
        self.worker.progressChanged.connect(self._on_progress)
        self.worker.finishedRun.connect(self._on_finished)
        self.worker.start()

    # ------------------------------------------------------------ 重命名 / 报表

    def rename_preview(self) -> None:
        tracks = [t for t in self.model.tracks() if t.status is not TrackStatus.UNSUPPORTED]
        if not tracks:
            QMessageBox.information(self, "没有曲目", "请先添加音频文件。")
            return
        self.settings_panel.apply_to(self.settings)
        plan = rename_mod.build_plan(tracks, self.settings.rename_template)
        if not plan.pending and not plan.blocked:
            QMessageBox.information(self, "无需重命名", "按当前模板所有文件命名已符合规则。")
            return
        dialog = RenamePreviewDialog(plan, self)
        if dialog.exec() == RenamePreviewDialog.Accepted:
            success, failed = rename_mod.execute_plan(plan)
            # 同步歌词文件跟着改名
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
        color = {
            "ERROR": "#f85149",
            "CRITICAL": "#f85149",
            "WARNING": "#d29922",
            "INFO": "#8b909a",
            "DEBUG": "#6e7681",
        }.get(level.upper(), "#dcdfe4")
        self.log_view.appendHtml(
            f'<span style="color:{color}">{_escape(message)}</span>'
        )

    def _refresh_stats(self) -> None:
        tracks = self.model.tracks()
        if not tracks:
            self.stats_label.setText("就绪 · 拖入音频文件或点击「添加文件夹」")
            return
        stats = report_mod.summarize(tracks)
        lossless = sum(1 for t in tracks if t.info.lossless)
        done = stats.get("完成", 0)
        failed = stats.get("失败", 0)
        review = stats.get("待人工确认", 0)
        unsupported = stats.get("不支持", 0)
        self.stats_label.setText(
            f"共 {len(tracks)} 首 · 无损 {lossless} · 完成 {done} · 待确认 {review}"
            f" · 失败 {failed} · 不支持 {unsupported}"
        )

    def _log_file_hint(self) -> str:
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
    return (
        (text or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
