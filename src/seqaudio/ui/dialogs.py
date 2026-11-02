"""对话框：手动编辑标签、候选匹配选择、重命名预览、水印清理预览。"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
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
from . import theme
from .widgets import Card


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
        self.resize(620, 700)
        self.setMinimumSize(560, 620)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        # ---- 曲目信息 ----
        info_card = Card("曲目信息", track.info.fmt)
        self.edits: dict[str, QLineEdit] = {}

        # 文件名（只读）+ 按文件名补全。
        # 文件名不是标签，改它不等于改标签，所以不做成可编辑字段；但抓轨工具
        # 常把真实歌名只留在文件名里（title 写成 Track 01），这里给一个一键
        # 反向补全的入口，比让用户对着两列手动抄要可靠。
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        filename_label = QLabel(track.path.name)
        filename_label.setStyleSheet(f"color:{theme.FG_DIM};")
        filename_label.setToolTip(str(track.path))
        filename_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.fill_hint = QLabel("")
        self.fill_hint.setStyleSheet(f"color:{theme.FG_MUTE}; font-size:11px;")
        fill_btn = QPushButton("按文件名补全")
        fill_btn.setObjectName("ghost")
        fill_btn.setCursor(Qt.PointingHandCursor)
        fill_btn.setToolTip(
            "把文件名里的「轨道号 / 歌手 / 歌名」填进上面的字段（只填空值或占位符，不覆盖已有内容）"
        )
        fill_btn.clicked.connect(self._fill_from_filename)
        name_row.addWidget(self._dim_label("文件名"))
        name_row.addWidget(filename_label, 1)
        name_row.addWidget(fill_btn)
        info_card.add_layout(name_row)
        info_card.add_widget(self.fill_hint)

        rows: list[tuple[str, QWidget]] = []
        left_form: list[tuple[str, QWidget]] = []
        right_form: list[tuple[str, QWidget]] = []
        for i, (label, key) in enumerate(self.FIELDS):
            edit = QLineEdit(track.working.get(key))
            self.edits[key] = edit
            (left_form if i % 2 == 0 else right_form).append((label, edit))

        two_col = QHBoxLayout()
        two_col.setSpacing(14)
        two_col.addLayout(self._mini_form(left_form), 1)
        two_col.addLayout(self._mini_form(right_form), 1)
        info_card.add_layout(two_col)

        self.comment_edit = QTextEdit(track.working.comment)
        self.comment_edit.setFixedHeight(56)
        info_card.add_widget(self._dim_label("注释"))
        info_card.add_widget(self.comment_edit)
        layout.addWidget(info_card)

        # ---- 歌词 ----
        lyrics_card = Card("歌词", "支持 LRC 时间轴", theme.PURPLE)
        self.lyrics_edit = QTextEdit(track.working.lyrics)
        self.lyrics_edit.setFixedHeight(120)
        lyrics_card.add_widget(self.lyrics_edit)
        layout.addWidget(lyrics_card, 1)

        # ---- 封面 ----
        cover_card = Card("封面", "", theme.CYAN)
        cover_row = QHBoxLayout()
        cover_row.setSpacing(14)
        self.cover_label = QLabel("无封面")
        self.cover_label.setFixedSize(104, 104)
        self.cover_label.setAlignment(Qt.AlignCenter)
        self.cover_label.setStyleSheet(
            f"border:1px dashed {theme.LINE}; border-radius:8px; "
            f"color:{theme.FG_MUTE}; background:{theme.BG_CARD};"
        )
        cover_row.addWidget(self.cover_label)

        btn_col = QVBoxLayout()
        btn_col.setSpacing(6)
        pick_btn = QPushButton("选择图片…")
        pick_btn.setObjectName("ghost")
        pick_btn.setCursor(Qt.PointingHandCursor)
        pick_btn.clicked.connect(self._pick_cover)
        clear_btn = QPushButton("清除封面")
        clear_btn.setObjectName("ghost")
        clear_btn.setCursor(Qt.PointingHandCursor)
        clear_btn.clicked.connect(self._clear_cover)
        btn_col.addWidget(pick_btn)
        btn_col.addWidget(clear_btn)
        btn_col.addStretch(1)
        cover_row.addLayout(btn_col)
        cover_row.addStretch(1)
        cover_card.add_layout(cover_row)
        layout.addWidget(cover_card)

        self._pending_cover: Optional[bytes] = track.working.cover
        self._pending_mime = track.working.cover_mime
        self._render_cover()

        # ---- 底部操作 ----
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText("保存到列表")
        ok_btn.setObjectName("primary")
        ok_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        cancel_btn.setText("取消")
        cancel_btn.setObjectName("ghost")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _mini_form(rows: list[tuple[str, QWidget]]) -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(7)
        form.setHorizontalSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for label, widget in rows:
            form.addRow(label, widget)
        return form

    @staticmethod
    def _dim_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hint")
        return label

    def _fill_from_filename(self) -> None:
        """用文件名解析结果补标签：只填空值 / 占位符，不覆盖用户已有内容。"""
        from ..core.probe import (
            guess_from_filename,
            is_placeholder_artist,
            is_placeholder_title,
        )

        guessed = guess_from_filename(self.track.path)
        filled: list[str] = []
        for key, label in (("track", "曲目号"), ("artist", "歌手"), ("title", "歌名")):
            edit = self.edits.get(key)
            if edit is None:
                continue
            value = (getattr(guessed, key, "") or "").strip()
            if not value:
                continue
            current = edit.text().strip()
            placeholder = (
                (key == "title" and is_placeholder_title(current))
                or (key == "artist" and is_placeholder_artist(current))
            )
            if (not current or placeholder) and value != current:
                edit.setText(value)
                filled.append(label)
        if filled:
            self.fill_hint.setText("已按文件名补全：" + "、".join(filled)
                                   + "（仍需点「保存到列表」才生效）")
        else:
            self.fill_hint.setText("文件名里没有可用的新信息，字段保持原样")

    def _render_cover(self) -> None:
        if not self._pending_cover:
            self.cover_label.setText("无封面")
            self.cover_label.setStyleSheet(
                f"border:1px dashed {theme.LINE}; border-radius:8px; "
                f"color:{theme.FG_MUTE}; background:{theme.BG_CARD};"
            )
            self.cover_label.setPixmap(QPixmap())
            return
        pix = QPixmap()
        if pix.loadFromData(self._pending_cover):
            self.cover_label.setStyleSheet(
                f"border:1px solid {theme.LINE}; border-radius:8px;"
            )
            self.cover_label.setPixmap(
                pix.scaled(102, 102, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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


class BatchTagEditDialog(QDialog):
    """批量编辑多首曲目的标签（V1.1）。

    每个字段前有复选框：**只有勾选的字段才会被批量修改**，未勾选的字段保持
    各曲目原值不变。勾选但留空表示清空该字段 —— 这样可以批量清空注释、流派等。
    """

    FIELDS = [
        ("歌名", "title"),
        ("歌手", "artist"),
        ("专辑", "album"),
        ("专辑艺术家", "albumartist"),
        ("年份", "date"),
        ("流派", "genre"),
        ("曲目号", "track"),
        ("碟号", "disc"),
        ("注释", "comment"),
    ]

    def __init__(self, tracks: list[Track], parent=None):
        super().__init__(parent)
        self.tracks = tracks
        self.setWindowTitle(f"批量编辑标签 — {len(tracks)} 首曲目")
        self.resize(660, 620)
        self.setMinimumSize(600, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        head = QLabel(f"将对选中的 {len(tracks)} 首曲目批量修改标签。")
        head.setObjectName("hint")
        layout.addWidget(head)

        toggle_row = QHBoxLayout()
        toggle_row.setSpacing(8)
        all_btn = QPushButton("全选字段")
        all_btn.setObjectName("ghost")
        all_btn.setCursor(Qt.PointingHandCursor)
        all_btn.clicked.connect(lambda: self._set_all(True))
        none_btn = QPushButton("全不选")
        none_btn.setObjectName("ghost")
        none_btn.setCursor(Qt.PointingHandCursor)
        none_btn.clicked.connect(lambda: self._set_all(False))
        toggle_row.addWidget(all_btn)
        toggle_row.addWidget(none_btn)
        toggle_row.addStretch(1)
        layout.addLayout(toggle_row)

        card = Card("字段", "打勾才修改", theme.CYAN)
        self.checks: dict[str, QCheckBox] = {}
        self.edits: dict[str, QLineEdit] = {}

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(7)
        form.setHorizontalSpacing(10)
        form.setLabelAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        for label, key in self.FIELDS:
            check = QCheckBox(label)
            check.setFixedWidth(96)
            edit = QLineEdit()
            edit.setPlaceholderText(self._placeholder(key))
            edit.setEnabled(False)
            check.toggled.connect(lambda checked, e=edit: e.setEnabled(checked))
            self.checks[key] = check
            self.edits[key] = edit
            form.addRow(check, edit)
        card.add_layout(form)
        layout.addWidget(card)

        note = QLabel(
            "勾选字段并填写新值；勾选但留空 = 清空该字段。\n"
            "未勾选的字段保持各曲目原值不变。"
        )
        note.setObjectName("hint")
        note.setWordWrap(True)
        layout.addWidget(note)
        layout.addStretch(1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        ok_btn = buttons.button(QDialogButtonBox.Ok)
        ok_btn.setText("应用到列表")
        ok_btn.setObjectName("primary")
        ok_btn.setCursor(Qt.PointingHandCursor)
        cancel_btn = buttons.button(QDialogButtonBox.Cancel)
        cancel_btn.setText("取消")
        cancel_btn.setObjectName("ghost")
        cancel_btn.setCursor(Qt.PointingHandCursor)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_all(self, checked: bool) -> None:
        for check in self.checks.values():
            check.setChecked(checked)

    def _placeholder(self, key: str) -> str:
        """占位提示：所有曲目该字段一致时显示原值，不一致显示 <多个值>。"""
        values = {(t.working.get(key) or "").strip() for t in self.tracks}
        values.discard("")
        if not values:
            return "（当前为空）"
        if len(values) == 1:
            return next(iter(values))[:48]
        return "<多个值>"

    def result_values(self) -> dict[str, str]:
        """返回勾选字段的目标值；未勾选的字段不出现在结果里。"""
        values: dict[str, str] = {}
        for key, check in self.checks.items():
            if check.isChecked():
                values[key] = self.edits[key].text().strip()
        return values


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
