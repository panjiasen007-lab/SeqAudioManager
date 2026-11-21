"""播放列表管理对话框：保存当前列表、载入、重命名、删除、排序。

设计取舍
--------
不开三四个菜单项，而是一个两栏对话框：
左栏「已保存的列表」，右栏「选中列表的内容」，底部是载入动作。
用户点一次就能看清「我有哪些列表、里面是什么」，比逐级菜单少两次点击。
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.playlists import PlaylistStore, clean_name
from . import i18n, theme
from .widgets import Card


class PlaylistDialog(QDialog):
    """``loadRequested(paths, append)``：把选中列表交给主窗口载入。"""

    loadRequested = Signal(list, bool)

    def __init__(
        self,
        store: PlaylistStore,
        current_paths: Iterable[str] = (),
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.store = store
        self._current_paths = [str(p) for p in current_paths if str(p)]
        self.setWindowTitle(i18n.tr_text("播放列表"))
        self.resize(760, 480)
        self._build()
        self.reload()

    # ------------------------------------------------------------ 构建

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 12)
        root.setSpacing(10)

        columns = QHBoxLayout()
        columns.setSpacing(10)

        # ---- 左：已保存列表 ----
        left_card = Card(i18n.tr_text("已保存的列表"), "", theme.CYAN)
        self.saved_list = QListWidget()
        self.saved_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.saved_list.currentItemChanged.connect(lambda *_: self._render_tracks())
        left_card.add_widget(self.saved_list)
        left_buttons = QHBoxLayout()
        left_buttons.setSpacing(6)
        self.btn_new = self._ghost(i18n.tr_text("保存当前列表…"), self._save_current_as)
        self.btn_overwrite = self._ghost(i18n.tr_text("覆盖为当前列表"), self._overwrite)
        left_buttons.addWidget(self.btn_new)
        left_buttons.addWidget(self.btn_overwrite)
        left_card.add_layout(left_buttons)
        columns.addWidget(left_card, 1)

        # ---- 右：列表内容 ----
        right_card = Card(i18n.tr_text("列表内容"), "", theme.PURPLE)
        self.track_list = QListWidget()
        self.track_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.track_list.itemDoubleClicked.connect(lambda *_: self._load(append=False))
        right_card.add_widget(self.track_list)
        right_buttons = QHBoxLayout()
        right_buttons.setSpacing(6)
        self.btn_move_up = self._ghost(i18n.tr_text("上移"), lambda: self._move(-1))
        self.btn_move_down = self._ghost(i18n.tr_text("下移"), lambda: self._move(1))
        self.btn_remove = self._ghost(i18n.tr_text("从列表移除"), self._remove_track)
        for button in (self.btn_move_up, self.btn_move_down, self.btn_remove):
            right_buttons.addWidget(button)
        right_buttons.addStretch(1)
        right_card.add_layout(right_buttons)
        columns.addWidget(right_card, 1)
        root.addLayout(columns, 1)

        # ---- 底部 ----
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        root.addWidget(self.hint)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.btn_rename = self._ghost(i18n.tr_text("重命名…"), self._rename)
        self.btn_delete = self._ghost(i18n.tr_text("删除"), self._delete)
        self.btn_delete.setObjectName("danger")
        footer.addWidget(self.btn_rename)
        footer.addWidget(self.btn_delete)
        footer.addStretch(1)
        self.btn_append = self._ghost(i18n.tr_text("追加到当前列表"), lambda: self._load(append=True))
        self.btn_load = QPushButton(i18n.tr_text("载入到当前列表"))
        self.btn_load.setObjectName("primary")
        self.btn_load.setCursor(Qt.PointingHandCursor)
        self.btn_load.clicked.connect(lambda: self._load(append=False))
        self.btn_close = self._ghost(i18n.tr_text("关闭"), self.reject)
        footer.addWidget(self.btn_append)
        footer.addWidget(self.btn_load)
        footer.addWidget(self.btn_close)
        root.addLayout(footer)

    def _ghost(self, text: str, slot) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("ghost")
        button.setCursor(Qt.PointingHandCursor)
        button.setMinimumHeight(30)
        button.clicked.connect(slot)
        return button

    # ------------------------------------------------------------ 数据

    def reload(self) -> None:
        """重新读盘并保持选中项尽量不变。"""
        selected = self.selected_name()
        self.saved_list.clear()
        for playlist in self.store.all():
            item = QListWidgetItem(f"{playlist.name}  ·  {playlist.count}")
            item.setData(Qt.UserRole, playlist.name)
            item.setToolTip(f"{playlist.name}（{playlist.count} 首）")
            self.saved_list.addItem(item)
            if playlist.name == selected:
                self.saved_list.setCurrentItem(item)
        if self.saved_list.currentItem() is None and self.saved_list.count():
            self.saved_list.setCurrentRow(0)
        self._render_tracks()
        self._refresh_actions()

    def selected_name(self) -> str:
        item = self.saved_list.currentItem()
        return str(item.data(Qt.UserRole)) if item is not None else ""

    def _render_tracks(self) -> None:
        self.track_list.clear()
        name = self.selected_name()
        playlist = self.store.get(name) if name else None
        if playlist is None:
            self.hint.setText(
                i18n.tr_format("共 {saved} 个列表；当前列表 {current} 首",
                               saved=self.store.count(), current=len(self._current_paths))
            )
            self._refresh_actions()
            return
        for entry in playlist.tracks:
            path = str(entry.get("path", ""))
            item = QListWidgetItem(Path(path).name or path)
            item.setToolTip(path)
            self.track_list.addItem(item)
        self.hint.setText(
            i18n.tr_format("共 {saved} 个列表；「{name}」{count} 首；当前列表 {current} 首",
                           saved=self.store.count(), name=playlist.name,
                           count=playlist.count, current=len(self._current_paths))
        )
        self._refresh_actions()

    def _refresh_actions(self) -> None:
        has_saved = bool(self.selected_name())
        has_track = self.track_list.currentRow() >= 0 and self.track_list.count() > 0
        for button in (self.btn_overwrite, self.btn_rename, self.btn_delete,
                       self.btn_load, self.btn_append):
            button.setEnabled(has_saved)
        for button in (self.btn_move_up, self.btn_move_down, self.btn_remove):
            button.setEnabled(has_saved and has_track)

    # ------------------------------------------------------------ 动作

    def _ask_name(self, title: str, default: str = "") -> str:
        text, accepted = QInputDialog.getText(self, title, i18n.tr_text("列表名称："), text=default)
        return clean_name(text) if accepted else ""

    def _save_current_as(self) -> None:
        if not self._current_paths:
            QMessageBox.information(self, i18n.tr_text("播放列表"),
                                    i18n.tr_text("当前列表是空的，先添加歌曲再保存。"))
            return
        name = self._ask_name(i18n.tr_text("保存当前列表"))
        if not name:
            return
        if self.store.get(name) is not None:
            reply = QMessageBox.question(
                self, i18n.tr_text("播放列表"),
                i18n.tr_format("「{name}」已存在，覆盖它吗？", name=name),
            )
            if reply != QMessageBox.Yes:
                return
        if self.store.save(name, self._current_paths):
            self.reload()
            self._select(name)

    def _overwrite(self) -> None:
        name = self.selected_name()
        if not name or not self._current_paths:
            return
        if self.store.save(name, self._current_paths):
            self.reload()
            self._select(name)

    def _select(self, name: str) -> None:
        for row in range(self.saved_list.count()):
            item = self.saved_list.item(row)
            if str(item.data(Qt.UserRole)) == name:
                self.saved_list.setCurrentRow(row)
                return

    def _rename(self) -> None:
        old = self.selected_name()
        if not old:
            return
        new = self._ask_name(i18n.tr_text("重命名列表"), old)
        if not new or new == old:
            return
        if not self.store.rename(old, new):
            QMessageBox.warning(self, i18n.tr_text("播放列表"),
                                i18n.tr_text("重命名失败：名称已存在或非法。"))
            return
        self.reload()
        self._select(new)

    def _delete(self) -> None:
        name = self.selected_name()
        if not name:
            return
        reply = QMessageBox.question(
            self, i18n.tr_text("播放列表"),
            i18n.tr_format("删除列表「{name}」？只删列表，不删磁盘文件。", name=name),
        )
        if reply != QMessageBox.Yes:
            return
        self.store.delete(name)
        self.reload()

    def _move(self, delta: int) -> None:
        name = self.selected_name()
        row = self.track_list.currentRow()
        if not name or row < 0:
            return
        if self.store.move_at(name, row, delta):
            self._render_tracks()
            self.track_list.setCurrentRow(max(0, min(row + delta, self.track_list.count() - 1)))

    def _remove_track(self) -> None:
        name = self.selected_name()
        row = self.track_list.currentRow()
        if not name or row < 0:
            return
        if self.store.remove_at(name, row):
            self._render_tracks()
            if self.track_list.count():
                self.track_list.setCurrentRow(min(row, self.track_list.count() - 1))
            self.reload()

    def _load(self, *, append: bool) -> None:
        name = self.selected_name()
        playlist = self.store.get(name) if name else None
        if playlist is None or not playlist.tracks:
            return
        self.loadRequested.emit(playlist.paths(), bool(append))
        self.accept()
