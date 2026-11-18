"""队列页签：轻量列表模型 + 右键操作 + 总时长。

为什么不用 ``TrackTableModel``
------------------------------
主表格的模型绑定的是 ``Track``（可编辑的标签工作副本），而队列里放的是
``QueueItem`` 的字典快照（引擎侧只读投影）。硬塞进主模型会出现两种错：
一是「从队列移除」会删掉主列表的行，二是 ``Track.working`` 被批处理改动时
队列里的显示跟着变。所以这里自建一个只读的 ``QueueListModel``，职责单一。

当前行用 **▶ 符号 + 加粗** 双重标注，不只靠颜色 —— 色觉差异用户与灰度主题
下也能看出正在播哪一首（测试计划 TC-GUI-10 的可访问性要求）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListView,
    QMenu,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import i18n, theme
from .widgets import SectionHeader

log = logging.getLogger(__name__)

#: 当前行前缀（可访问性：符号 + 加粗，不只靠颜色）
CURRENT_MARK = "▶"
#: 空行占位前缀（保持数字对齐）
IDLE_MARK = "  "


def format_seconds(seconds: float) -> str:
    """秒 → ``mm:ss``（超 1 小时给 ``h:mm:ss``）。"""
    total = max(0, int(round(float(seconds or 0.0))))
    if total >= 3600:
        return f"{total // 3600}:{(total % 3600) // 60:02d}:{total % 60:02d}"
    return f"{total // 60:02d}:{total % 60:02d}"


class QueueListModel(QAbstractListModel):
    """队列的只读列表模型（每行一个 ``QueueItem`` 字典）。"""

    def __init__(self, items: Optional[list[dict[str, Any]]] = None, parent=None):
        super().__init__(parent)
        self._items: list[dict[str, Any]] = list(items or [])
        self._current = -1

    # ---- Qt 接口 ----

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._items)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None
        row = index.row()
        item = self._items[row]
        if role == Qt.DisplayRole:
            return self._row_text(row, item)
        if role == Qt.ToolTipRole:
            return self._row_tooltip(row, item)
        if role == Qt.FontRole and row == self._current:
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ForegroundRole:
            if not self._is_available(item):
                return QColor(theme.FG_MUTE)
            if row == self._current:
                return QColor(theme.ACCENT)
            return None
        return None

    # ---- 数据操作 ----

    def set_items(self, items: Optional[list[dict[str, Any]]]) -> None:
        self.beginResetModel()
        self._items = [dict(entry) for entry in (items or []) if isinstance(entry, dict)]
        if self._current >= len(self._items):
            self._current = -1
        self.endResetModel()

    def set_current(self, row: int) -> None:
        """高亮当前行（越界按「无当前项」处理）。"""
        value = int(row)
        if value >= len(self._items):
            value = -1
        if value == self._current:
            return
        previous = self._current
        self._current = value
        for changed in (previous, value):
            if 0 <= changed < len(self._items):
                self.dataChanged.emit(self.index(changed, 0), self.index(changed, 0))

    def current(self) -> int:
        return self._current

    def item_at(self, row: int) -> Optional[dict[str, Any]]:
        if 0 <= int(row) < len(self._items):
            return dict(self._items[int(row)])
        return None

    def items(self) -> list[dict[str, Any]]:
        return [dict(entry) for entry in self._items]

    def total_seconds(self) -> float:
        total = 0.0
        for item in self._items:
            duration = float(item.get("duration") or 0.0)
            if duration <= 0:
                duration = float(item.get("duration_ms") or 0) / 1000.0
            total += max(0.0, duration)
        return total

    # ---- 内部 ----

    def _row_text(self, row: int, item: dict[str, Any]) -> str:
        mark = CURRENT_MARK if row == self._current else IDLE_MARK
        title = str(item.get("title") or "").strip()
        if not title:
            path_text = str(item.get("path") or "")
            stem = path_text.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
            title = stem.rsplit(".", 1)[0] if "." in stem else stem
        artist = str(item.get("artist") or "").strip()
        duration = float(item.get("duration") or 0.0)
        if duration <= 0:
            duration = float(item.get("duration_ms") or 0) / 1000.0
        parts = [f"{mark}{row + 1:02d}", title]
        if artist:
            parts.append(artist)
        text = "  ".join(parts)
        if duration > 0:
            text = f"{text}   {format_seconds(duration)}"
        if item.get("cue_track") is not None:
            text = f"{text}  · #{item.get('cue_track')}"
        if not self._is_available(item):
            text = f"{text}  [{i18n.tr_text('不可用')}]"
        return text

    def _row_tooltip(self, row: int, item: dict[str, Any]) -> str:
        lines = [
            i18n.tr_format("序号：{index}", index=row + 1),
            i18n.tr_format("路径：{path}", path=item.get("path") or ""),
        ]
        if item.get("album"):
            lines.append(i18n.tr_format("专辑：{album}", album=item.get("album")))
        rate = int(item.get("sample_rate") or 0)
        depth = int(item.get("bit_depth") or 0)
        if rate or depth:
            lines.append(
                i18n.tr_format(
                    "规格：{spec}",
                    spec=" / ".join(
                        part for part in (
                            f"{depth} bit" if depth else "",
                            f"{rate / 1000:g} kHz" if rate else "",
                            "DSD" if item.get("is_dsd") else "",
                        ) if part
                    ),
                )
            )
        if not self._is_available(item):
            lines.append(i18n.tr_text("文件已不可用（所在盘符可能未挂载）"))
        return "\n".join(lines)

    @staticmethod
    def _is_available(item: dict[str, Any]) -> bool:
        """路径是否还在（Q6：不可用的项灰显但**不自动删除**）。"""
        path_text = str(item.get("path") or "")
        if not path_text:
            return False
        try:
            from pathlib import Path

            return Path(path_text).exists()
        except OSError:  # pragma: no cover - 权限问题按不可用处理
            return False


class PlayerQueuePanel(QWidget):
    """「队列」页签。

    Signals:
        trackActivated(int): 双击某行 → 跳播该下标。
        removeRequested(int): 右键「从队列移除」。
        clearRequested(): 「清空队列」按钮。
        playNextRequested(int): 右键「下一首播放」（把该行插到当前项之后）。
        locateRequested(int): 右键「在列表中定位」（主窗口负责选中主表格对应行）。
    """

    trackActivated = Signal(int)
    removeRequested = Signal(int)
    clearRequested = Signal()
    playNextRequested = Signal(int)
    locateRequested = Signal(int)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("playerQueue")

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        root.addWidget(SectionHeader(i18n.tr_text("队列"), i18n.tr_text("双击跳播，右键更多操作")))

        self.model = QueueListModel(parent=self)
        self.list_view = QListView()
        self.list_view.setObjectName("playerQueue")
        self.list_view.setModel(self.model)
        self.list_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_view.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list_view.setUniformItemSizes(False)
        self.list_view.setAlternatingRowColors(False)
        self.list_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list_view.customContextMenuRequested.connect(self._show_context_menu)
        self.list_view.doubleClicked.connect(self._on_double_clicked)
        self.list_view.setToolTip(i18n.tr_text("队列：双击行跳播"))
        root.addWidget(self.list_view, 1)

        self.empty_label = QLabel(i18n.tr_text("队列为空"))
        self.empty_label.setObjectName("hint")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setVisible(True)
        root.addWidget(self.empty_label)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.setSpacing(8)
        self.summary_label = QLabel("")
        self.summary_label.setObjectName("hint")
        footer.addWidget(self.summary_label, 1)
        self.btn_clear = QPushButton(i18n.tr_text("清空队列"))
        self.btn_clear.setObjectName("smallGhost")
        self.btn_clear.setCursor(Qt.PointingHandCursor)
        self.btn_clear.clicked.connect(self.clearRequested.emit)
        footer.addWidget(self.btn_clear)
        root.addLayout(footer)

        self._refresh_summary()

    # ------------------------------------------------------------ 接口

    def set_items(self, items: Optional[list[dict[str, Any]]]) -> None:
        """整表刷新（保持当前行不变，除非越界）。"""
        current = self.model.current()
        self.model.set_items(items)
        if current >= self.model.rowCount():
            self.model.set_current(-1)
        self._refresh_summary()

    def set_current(self, index: int) -> None:
        """高亮当前播放行并滚到可见处（超过一屏时仍有反馈）。"""
        self.model.set_current(int(index))
        if 0 <= int(index) < self.model.rowCount():
            self.list_view.scrollTo(
                self.model.index(int(index), 0), QAbstractItemView.EnsureVisible
            )

    def count(self) -> int:
        return self.model.rowCount()

    def items(self) -> list[dict[str, Any]]:
        return self.model.items()

    def total_seconds(self) -> float:
        return self.model.total_seconds()

    def current_index(self) -> int:
        return self.model.current()

    # ------------------------------------------------------------ 内部

    def _refresh_summary(self) -> None:
        count = self.model.rowCount()
        text = i18n.tr_format(
            "{count} 首 / {total}",
            count=count,
            total=format_seconds(self.model.total_seconds()),
        )
        self.summary_label.setText(text)
        self.empty_label.setVisible(count == 0)
        self.btn_clear.setEnabled(count > 0)

    def _on_double_clicked(self, index: QModelIndex) -> None:
        if index.isValid():
            self.trackActivated.emit(int(index.row()))

    def _show_context_menu(self, pos) -> None:
        index = self.list_view.indexAt(pos)
        if not index.isValid():
            return
        row = int(index.row())
        menu = QMenu(self)
        menu.addAction(i18n.tr_text("播放"), lambda: self.trackActivated.emit(row))
        menu.addAction(
            i18n.tr_text("下一首播放"), lambda: self.playNextRequested.emit(row)
        )
        menu.addAction(
            i18n.tr_text("从队列移除"), lambda: self.removeRequested.emit(row)
        )
        menu.addSeparator()
        menu.addAction(
            i18n.tr_text("在列表中定位"), lambda: self.locateRequested.emit(row)
        )
        menu.exec(self.list_view.viewport().mapToGlobal(pos))

    def refresh_texts(self) -> None:
        """语言切换后刷新动态文案（静态控件由 ``localize_tree`` 负责）。"""
        self._refresh_summary()
        self.model.layoutChanged.emit()


__all__ = ["PlayerQueuePanel", "QueueListModel", "format_seconds", "CURRENT_MARK"]
