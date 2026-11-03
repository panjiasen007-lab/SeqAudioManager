"""曲库体检查看器：重复文件、缺失项与异常标签一屏汇总。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QLabel,
    QMessageBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.health import HealthReport, analyze_library, write_health_report
from ..core.model import Track
from ..core.quarantine import human_size
from . import theme


class HealthDialog(QDialog):
    def __init__(self, tracks: list[Track], parent=None):
        super().__init__(parent)
        self.tracks = list(tracks)
        self.report: HealthReport | None = None
        self.setWindowTitle("曲库体检")
        self.resize(940, 660)
        self.setMinimumSize(820, 560)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 12)
        layout.setSpacing(10)

        head = QLabel("曲库体检")
        head.setObjectName("sectionTitle")
        layout.addWidget(head)
        hint = QLabel("查重只读取同尺寸候选文件的首/中/尾各 1MB，不会完整扫描大文件；结果仅供复核，不会自动删除。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.summary = QWidget()
        self.summary_layout = QGridLayout(self.summary)
        self.summary_layout.setContentsMargins(0, 0, 0, 0)
        self.summary_layout.setHorizontalSpacing(8)
        self.summary_layout.setVerticalSpacing(8)
        layout.addWidget(self.summary)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.dup_table = self._make_table(["类型", "文件数", "可释放", "说明", "示例文件"])
        self.issue_table = self._make_table(["类型", "歌名", "歌手", "说明", "文件路径"])
        self.tabs.addTab(self.dup_table, "重复文件")
        self.tabs.addTab(self.issue_table, "问题清单")
        layout.addWidget(self.tabs, 1)

        buttons = QDialogButtonBox()
        self.export_btn = buttons.addButton("导出健康报表…", QDialogButtonBox.ActionRole)
        self.export_btn.setObjectName("ghost")
        self.export_btn.clicked.connect(self._export)
        self.rescan_btn = buttons.addButton("重新扫描", QDialogButtonBox.ActionRole)
        self.rescan_btn.setObjectName("ghost")
        self.rescan_btn.clicked.connect(self.refresh)
        close_btn = buttons.addButton("关闭", QDialogButtonBox.AcceptRole)
        close_btn.setObjectName("primary")
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.refresh()

    @staticmethod
    def _make_table(headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setShowGrid(False)
        table.setAlternatingRowColors(True)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setWordWrap(False)
        table.horizontalHeader().setStretchLastSection(True)
        return table

    def refresh(self) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            self.report = analyze_library(self.tracks)
        finally:
            QApplication.restoreOverrideCursor()
        self._fill_summary()
        self._fill_duplicates()
        self._fill_issues()

    def _fill_summary(self) -> None:
        while self.summary_layout.count():
            item = self.summary_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        report = self.report
        if report is None:
            return
        stats = [
            ("曲目", len(self.tracks), theme.FG),
            ("假无损", report.fake_count, theme.BAD),
            ("低规格", report.low_spec_count, theme.WARN),
            ("未鉴定", report.unanalysed_count, theme.FG_MUTE),
            ("缺封面", report.count("缺少封面"), theme.CYAN),
            ("缺歌词", report.count("缺少歌词"), theme.PURPLE),
            ("文件问题", report.count("读取/写入失败") + report.count("格式不支持"), theme.BAD),
            ("重复文件", report.duplicate_files, theme.WARN),
            ("可释放", human_size(report.redundant_bytes), theme.OK),
            ("总大小", human_size(report.total_bytes), theme.FG_DIM),
        ]
        for index, (label, value, color) in enumerate(stats):
            card = QWidget()
            card.setObjectName("healthCard")
            card.setStyleSheet(
                f"QWidget#healthCard{{background:{theme.BG_CARD};"
                f"border:1px solid {theme.LINE_SOFT};border-radius:8px;}}"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 6, 10, 6)
            card_layout.setSpacing(1)
            text_label = QLabel(label)
            text_label.setStyleSheet(f"color:{theme.FG_MUTE}; font-size:11px;")
            value_label = QLabel(str(value))
            value_label.setStyleSheet(f"color:{color}; font-size:18px; font-weight:700;")
            card_layout.addWidget(text_label)
            card_layout.addWidget(value_label)
            self.summary_layout.addWidget(card, index // 5, index % 5)

    def _fill_duplicates(self) -> None:
        report = self.report
        groups = report.duplicates if report else []
        self.dup_table.setRowCount(len(groups))
        for row, group in enumerate(groups):
            names = "、".join(t.path.name for t in group.tracks[:2])
            if len(group.tracks) > 2:
                names += f" 等 {len(group.tracks)} 个"
            values = [
                group.kind,
                str(len(group.tracks)),
                human_size(group.wasted_bytes) if group.wasted_bytes else "—",
                group.detail,
                names,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 0:
                    item.setForeground(
                        QColor(theme.BAD)
                        if group.kind == "完全相同"
                        else QColor(theme.WARN)
                    )
                self.dup_table.setItem(row, column, item)
        self.dup_table.resizeColumnsToContents()
        self.dup_table.horizontalHeader().setStretchLastSection(True)

    def _fill_issues(self) -> None:
        report = self.report
        issues = report.issues if report else []
        self.issue_table.setRowCount(len(issues))
        for row, issue in enumerate(issues):
            track = issue.track
            values = [
                issue.category,
                track.working.title or track.original.title or track.path.stem,
                track.working.artist or track.original.artist,
                issue.detail,
                str(track.path),
            ]
            for column, value in enumerate(values):
                self.issue_table.setItem(row, column, QTableWidgetItem(value))
        self.issue_table.resizeColumnsToContents()
        header = self.issue_table.horizontalHeader()
        header.setStretchLastSection(True)
        if self.issue_table.columnWidth(3) > 420:
            self.issue_table.setColumnWidth(3, 420)

    def _export(self) -> None:
        if self.report is None:
            return
        target, _ = QFileDialog.getSaveFileName(
            self, "导出健康报表", "曲库体检.csv", "CSV (*.csv)"
        )
        if not target:
            return
        try:
            path = write_health_report(self.report, target)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", str(exc))
            return
        QMessageBox.information(self, "导出完成", f"健康报表已保存到：\n{path}")
