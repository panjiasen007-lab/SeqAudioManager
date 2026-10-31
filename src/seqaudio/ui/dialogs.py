"""对话框：手动编辑标签、候选匹配选择、重命名预览、水印清理预览。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..core.model import MatchCandidate, Tags, Track
from ..core.rename import RenamePlan


class TagEditDialog(QDialog):
    """手动修改单曲标签（需求 2.5：识别不准时可手动修改信息与封面）。"""

    FIELDS = [
        ("歌名", "title"),
        ("歌手", "artist"),
        ("专辑", "album"),
        ("专辑艺术家", "albumartist"),
        ("年份", "date"),
        ("流派", "genre"),
        ("曲目号", "track"),
        ("碟号", "disc"),
    ]

    def __init__(self, track: Track, parent=None):
        super().__init__(parent)
        self.track = track
        self.setWindowTitle(f"编辑标签 — {track.path.name}")
        self.resize(560, 620)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for label, key in self.FIELDS:
            edit = QLineEdit(track.working.get(key))
            self.edits[key] = edit
            form.addRow(label, edit)
        layout.addLayout(form)

        self.comment_edit = QTextEdit(track.working.comment)
        self.comment_edit.setFixedHeight(60)
        layout.addWidget(QLabel("注释"))
        layout.addWidget(self.comment_edit)

        self.lyrics_edit = QTextEdit(track.working.lyrics)
        self.lyrics_edit.setFixedHeight(110)
        layout.addWidget(QLabel("歌词"))
        layout.addWidget(self.lyrics_edit)

        # 封面区
        cover_row = QHBoxLayout()
        self.cover_label = QLabel("无封面")
        self.cover_label.setFixedSize(96, 96)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet("border:1px solid #33363d; border-radius:4px;")
        cover_row.addWidget(self.cover_label)

        btn_col = QVBoxLayout()
        pick_btn = QPushButton("选择图片…")
        pick_btn.clicked.connect(self._pick_cover)
        clear_btn = QPushButton("清除封面")
        clear_btn.clicked.connect(self._clear_cover)
        btn_col.addWidget(pick_btn)
        btn_col.addWidget(clear_btn)
        btn_col.addStretch(1)
        cover_row.addLayout(btn_col)
        cover_row.addStretch(1)
        layout.addLayout(cover_row)

        self._pending_cover: Optional[bytes] = track.working.cover
        self._pending_mime = track.working.cover_mime
        self._render_cover()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存到列表")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _render_cover(self) -> None:
        if not self._pending_cover:
            self.cover_label.setText("无封面")
            self.cover_label.setPixmap(QPixmap())
            return
        pix = QPixmap()
        if pix.loadFromData(self._pending_cover):
            self.cover_label.setPixmap(
                pix.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        else:
            self.cover_label.setText("无法预览")

    def _pick_cover(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "选择封面图片", "", "图片 (*.jpg *.jpeg *.png *.webp)"
        )
        if not path:
            return
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError:
            return
        mime = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
        self._pending_cover, self._pending_mime = data, mime
        self._render_cover()

    def _clear_cover(self) -> None:
        self._pending_cover, self._pending_mime = None, ""
        self._render_cover()

    def result_tags(self) -> Tags:
        tags = self.track.working.copy()
        for key, edit in self.edits.items():
            tags.set(key, edit.text().strip())
        tags.comment = self.comment_edit.toPlainText().strip()
        tags.lyrics = self.lyrics_edit.toPlainText().strip()
        tags.cover = self._pending_cover
        tags.cover_mime = self._pending_mime
        return tags


class CandidateDialog(QDialog):
    """候选匹配列表（需求 3.2.5）。"""

    def __init__(self, track: Track, candidates: list[MatchCandidate], parent=None):
        super().__init__(parent)
        self.track = track
        self.candidates = candidates
        self.selected: Optional[MatchCandidate] = None
        self.setWindowTitle(f"选择匹配结果 — {track.path.name}")
        self.resize(680, 420)

        layout = QVBoxLayout(self)
        head = QLabel(
            f"本地信息：{track.working.title or track.path.stem}"
            + (f" — {track.working.artist}" if track.working.artist else "")
        )
        head.setStyleSheet("color:#8b909a;")
        layout.addWidget(head)

        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        for cand in candidates:
            item = QListWidgetItem(
                f"[{cand.confidence:.0%}] {cand.source}  |  {cand.label}"
                + (f"   曲目 {cand.track}" if cand.track else "")
            )
            item.setData(Qt.UserRole, cand)
            self.list.addItem(item)
        if candidates:
            self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _: self.accept())
        layout.addWidget(self.list, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("使用选中项")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item:
            self.selected = item.data(Qt.UserRole)
            self.accept()


class RenamePreviewDialog(QDialog):
    """重命名预览（需求 3.5：先看效果确认后再执行）。"""

    def __init__(self, plan: RenamePlan, parent=None):
        super().__init__(parent)
        self.plan = plan
        self.setWindowTitle(f"重命名预览 — {len(plan.pending)} 项待改名")
        self.resize(820, 520)

        layout = QVBoxLayout(self)
        info = QLabel(
            f"模板：{plan.template}    待改名 {len(plan.pending)} 项，"
            f"跳过 {len(plan.items) - len(plan.pending) - len(plan.blocked)} 项，"
            f"冲突 {len(plan.blocked)} 项"
        )
        info.setStyleSheet("color:#8b909a;")
        layout.addWidget(info)

        table = QTableWidget(len(plan.items), 3)
        table.setHorizontalHeaderLabels(["状态", "原文件名", "新文件名"])
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        from PySide6.QtWidgets import QAbstractItemView

        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        for row, item in enumerate(plan.items):
            if not item.ok:
                status = "冲突"
            elif item.changed:
                status = "改名"
            else:
                status = "不变"
            table.setItem(row, 0, QTableWidgetItem(status))
            table.setItem(row, 1, QTableWidgetItem(item.old_path.name))
            table.setItem(row, 2, QTableWidgetItem(
                item.new_path.name if item.ok else item.reason
            ))
        table.resizeColumnsToContents()
        layout.addWidget(table, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("执行重命名")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
