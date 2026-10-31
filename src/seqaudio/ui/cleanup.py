"""假无损清理与隔离区管理。

安全设计
--------
删除用户的无损母带是这个软件里风险最高的操作 —— 1.7GB 的 DFF 误删一次，
重新找资源可能要好几天。所以这里的原则是：

* 默认选项永远是**可还原**的（回收站 / 隔离区），彻底删除必须额外勾选确认；
* 确认框里把每个文件、大小、判定依据都列出来，不做"共 N 个文件"这种糊涂账；
* 彻底删除按钮的文案带上数量，让用户在按下去之前就知道后果是什么。
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.model import Track
from ..core.quarantine import QuarantineStore, human_size
from ..core.authenticity import VERDICT_COLORS
from . import theme
from .widgets import Card

log = logging.getLogger(__name__)

MODE_RECYCLE = "recycle"
MODE_QUARANTINE = "quarantine"
MODE_DELETE = "delete"


def _make_table(headers: list[str], widths: list[int]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectRows)
    table.setSelectionMode(QAbstractItemView.ExtendedSelection)
    table.setWordWrap(False)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setFocusPolicy(Qt.NoFocus)
    table.verticalHeader().setDefaultSectionSize(28)
    for index, width in enumerate(widths):
        table.setColumnWidth(index, width)
    table.horizontalHeader().setStretchLastSection(True)
    # 列宽总和略小于窗口时也会冒出横向滚动条，直接关掉更干净
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    table.horizontalHeader().setHighlightSections(False)
    return table


class CleanupDialog(QDialog):
    """清理确认：列出文件 + 选择处理方式。"""

    def __init__(self, tracks: list[Track], parent=None):
        super().__init__(parent)
        self.tracks = tracks
        self.mode = MODE_RECYCLE
        self.setWindowTitle("清理假无损")
        self.resize(820, 640)
        self.setMinimumSize(720, 560)

        total_bytes = sum(t.path.stat().st_size for t in tracks if t.path.exists())
        fake = sum(1 for t in tracks if t.authenticity and t.authenticity.verdict == "假无损")
        suspect = len(tracks) - fake

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        title = QLabel(f"待清理 {len(tracks)} 个文件，合计 {human_size(total_bytes)}")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            f"其中确认假无损 {fake} 个，疑似假无损 / 升频 {suspect} 个。"
            "建议先用频谱确认再删除 —— 双击任意一行可查看频谱。"
        )
        subtitle.setObjectName("hint")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self.table = _make_table(["文件名", "判定", "格式", "大小", "判定依据"],
                                 [266, 92, 64, 74, 190])
        self.table.setRowCount(len(tracks))
        for row, track in enumerate(tracks):
            auth = track.authenticity
            items = [
                QTableWidgetItem(track.path.name),
                QTableWidgetItem(auth.verdict if auth else ""),
                QTableWidgetItem(track.info.fmt),
                QTableWidgetItem(human_size(track.path.stat().st_size)
                                 if track.path.exists() else "-"),
                QTableWidgetItem(track.working.title or track.path.stem),
            ]
            items[1].setForeground(_color(VERDICT_COLORS.get(auth.verdict if auth else "", theme.FG_MUTE)))
            for column, item in enumerate(items):
                self.table.setItem(row, column, item)
        self.table.doubleClicked.connect(self._show_spectrum)
        layout.addWidget(self.table, 1)

        # ---- 处理方式 ----
        mode_card = Card("处理方式", "", theme.ACCENT)
        self.radio_recycle = QRadioButton("移入回收站（推荐）—— 可在资源管理器「回收站」中还原")
        self.radio_quarantine = QRadioButton("移入软件隔离区 —— 可在「隔离区」窗口里一键还原")
        self.radio_delete = QRadioButton("彻底删除 —— 不经过回收站，无法恢复")
        self.radio_recycle.setChecked(True)
        for radio in (self.radio_recycle, self.radio_quarantine, self.radio_delete):
            radio.setCursor(Qt.PointingHandCursor)
            mode_card.add_widget(radio)
            radio.toggled.connect(self._on_mode_changed)

        hint = QLabel("同名的 .lrc / .cue 会跟随音频一起处理，避免留下孤儿文件。")
        hint.setObjectName("hint")
        mode_card.add_widget(hint)
        layout.addWidget(mode_card)

        # ---- 危险操作确认 ----
        self.confirm_check = QCheckBox("我确认这些文件确实不需要了，并且理解彻底删除无法恢复")
        self.confirm_check.setCursor(Qt.PointingHandCursor)
        self.confirm_check.setVisible(False)
        self.confirm_check.toggled.connect(self._refresh_buttons)
        layout.addWidget(self.confirm_check)

        self.warning = QLabel("")
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet(f"color:{theme.BAD}; font-weight:600;")
        self.warning.setVisible(False)
        layout.addWidget(self.warning)

        buttons = QDialogButtonBox()
        self.btn_ok = buttons.addButton("开始清理", QDialogButtonBox.AcceptRole)
        self.btn_ok.setCursor(Qt.PointingHandCursor)
        self.btn_ok.setObjectName("primary")
        cancel = buttons.addButton("取消", QDialogButtonBox.RejectRole)
        cancel.setObjectName("ghost")
        cancel.setCursor(Qt.PointingHandCursor)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._refresh_buttons()

    # ------------------------------------------------------------

    def _on_mode_changed(self) -> None:
        if self.radio_delete.isChecked():
            self.mode = MODE_DELETE
        elif self.radio_quarantine.isChecked():
            self.mode = MODE_QUARANTINE
        else:
            self.mode = MODE_RECYCLE
        self.confirm_check.setVisible(self.mode == MODE_DELETE)
        if self.mode != MODE_DELETE:
            self.confirm_check.setChecked(False)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        dangerous = self.mode == MODE_DELETE
        self.btn_ok.setText(f"彻底删除 {len(self.tracks)} 个文件" if dangerous else "开始清理")
        self.btn_ok.setObjectName("dangerSolid" if dangerous else "primary")
        # 切换 objectName 后必须重新应用样式，否则 QSS 不会重算
        self.btn_ok.style().unpolish(self.btn_ok)
        self.btn_ok.style().polish(self.btn_ok)
        self.warning.setVisible(dangerous)
        if dangerous:
            self.warning.setText(
                f"⚠ 这些文件将直接从磁盘移除，不进入回收站，也无法通过回收站还原。"
                f"共 {len(self.tracks)} 个文件。"
            )
        self.btn_ok.setEnabled(not dangerous or self.confirm_check.isChecked())

    def _show_spectrum(self) -> None:
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            return
        from .spectrum import open_spectrum

        open_spectrum(self.tracks[rows[0]], self)


class QuarantineDialog(QDialog):
    """隔离区管理：还原 / 彻底删除。"""

    def __init__(self, store: Optional[QuarantineStore] = None, parent=None):
        super().__init__(parent)
        self.store = store or QuarantineStore()
        self.setWindowTitle("隔离区")
        self.resize(860, 580)
        self.setMinimumSize(720, 480)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(10)

        self.title = QLabel("隔离区")
        self.title.setObjectName("sectionTitle")
        layout.addWidget(self.title)

        hint = QLabel("被隔离的文件保存在软件数据目录下，还原时会回到原路径；"
                      "若原位置已被占用，会另存为「文件名 (已恢复)」。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.table = _make_table(["文件名", "判定", "大小", "隔离时间", "原路径"],
                                 [210, 90, 76, 124, 280])
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_restore = QPushButton("还原选中")
        self.btn_restore.setObjectName("primary")
        self.btn_restore.clicked.connect(self.restore_selected)
        self.btn_purge = QPushButton("彻底删除选中")
        self.btn_purge.setObjectName("danger")
        self.btn_purge.clicked.connect(self.purge_selected)
        self.btn_open = QPushButton("打开隔离区目录")
        self.btn_open.setObjectName("ghost")
        self.btn_open.clicked.connect(self.open_folder)
        for button in (self.btn_restore, self.btn_purge, self.btn_open):
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(32)
        row.addWidget(self.btn_restore)
        row.addWidget(self.btn_purge)
        row.addWidget(self.btn_open)
        row.addStretch(1)
        close = QPushButton("关闭")
        close.setObjectName("ghost")
        close.setCursor(Qt.PointingHandCursor)
        close.setFixedHeight(32)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)

        self._entries = []
        self.reload()

    # ------------------------------------------------------------

    def reload(self) -> None:
        self._entries = self.store.entries()
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            values = [
                os.path.basename(entry.stored),
                entry.verdict or "-",
                entry.size_text,
                entry.moved_at,
                entry.original,
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                if column == 1:
                    item.setForeground(_color(VERDICT_COLORS.get(entry.verdict, theme.FG_MUTE)))
                if column == 4:
                    item.setForeground(_color(theme.FG_MUTE))
                self.table.setItem(row, column, item)

        total = sum(e.size for e in self._entries)
        self.title.setText(
            f"隔离区 · {len(self._entries)} 个文件 · 占用 {human_size(total)}"
            if self._entries else "隔离区 · 空"
        )
        has_entries = bool(self._entries)
        self.btn_restore.setEnabled(has_entries)
        self.btn_purge.setEnabled(has_entries)

    def _selected_entries(self):
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        return [self._entries[r] for r in rows if 0 <= r < len(self._entries)]

    def restore_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            return
        ok = 0
        for entry in entries:
            success, message = self.store.restore(entry)
            if success:
                ok += 1
            else:
                log.warning("还原失败 %s：%s", entry.original, message)
        QMessageBox.information(self, "还原完成", f"成功还原 {ok} / {len(entries)} 个文件。")
        self.reload()

    def purge_selected(self) -> None:
        entries = self._selected_entries()
        if not entries:
            return
        # 隔离区里已经"删过一次"了，但这里仍是不可恢复操作，必须二次确认
        reply = QMessageBox.warning(
            self,
            "彻底删除",
            f"将从隔离区永久删除 {len(entries)} 个文件，无法恢复。\n\n继续？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        ok = sum(1 for entry in entries if self.store.purge(entry)[0])
        QMessageBox.information(self, "完成", f"已永久删除 {ok} 个文件。")
        self.reload()

    def open_folder(self) -> None:
        try:
            os.startfile(str(self.store.root))  # type: ignore[attr-defined]
        except OSError as exc:
            QMessageBox.warning(self, "无法打开", str(exc))


def _color(hex_value: str):
    from PySide6.QtGui import QColor

    return QColor(hex_value)
