"""曲目表格模型：把 Track 列表映射成 Qt 表格，支持排序与状态着色。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSortFilterProxyModel
from PySide6.QtGui import QColor, QBrush, QFont

from ..core.model import Track, TrackStatus

#: (表头, 取值函数, 列宽)
COLUMNS: list[tuple[str, Callable[[Track], Any], int]] = [
    ("状态", lambda t: t.status.value, 82),
    ("歌名", lambda t: t.working.title or t.original.title, 180),
    ("歌手", lambda t: t.working.artist or t.original.artist, 104),
    ("专辑", lambda t: t.working.album or t.original.album, 124),
    ("年份", lambda t: (t.working.date or t.original.date)[:4], 50),
    ("格式", lambda t: t.info.fmt, 54),
    ("规格", lambda t: t.info.spec_text, 88),
    ("时长", lambda t: t.info.duration_text, 50),
    ("无损", lambda t: t.info.kind_text, 46),
    ("置信度", lambda t: f"{t.confidence:.0%}" if t.confidence else "", 58),
    ("来源", lambda t: t.source, 88),
    ("文件名", lambda t: t.path.name, 170),
    ("说明", lambda t: " | ".join(t.notes), 240),
]

#: 文件名默认隐藏（路径与完整文件名在 tooltip 里），窄屏下可一键切出
HIDDEN_BY_DEFAULT = {11}

#: 说明列随窗口伸缩，其余固定
STRETCH_COLUMN = 12

STATUS_COLORS = {
    TrackStatus.DONE: QColor("#3fb950"),
    TrackStatus.FAILED: QColor("#f85149"),
    TrackStatus.NEEDS_REVIEW: QColor("#d29922"),
    TrackStatus.UNSUPPORTED: QColor("#8b949e"),
    TrackStatus.SKIPPED: QColor("#8b949e"),
    TrackStatus.MATCHED: QColor("#58a6ff"),
    TrackStatus.WRITING: QColor("#a371f7"),
    TrackStatus.READING: QColor("#a371f7"),
}


class TrackTableModel(QAbstractTableModel):
    def __init__(self, tracks: Optional[list[Track]] = None, parent=None):
        super().__init__(parent)
        self._tracks: list[Track] = tracks or []

    # ---- Qt 接口 ----
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._tracks)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return COLUMNS[section][0]
        return section + 1

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        track = self._tracks[index.row()]
        getter = COLUMNS[index.column()][1]

        if role == Qt.DisplayRole:
            try:
                value = getter(track)
            except Exception:  # noqa: BLE001
                return ""
            return "" if value is None else str(value)

        if role == Qt.ToolTipRole:
            return self._tooltip(track)

        if role == Qt.ForegroundRole:
            if index.column() == 0:
                color = STATUS_COLORS.get(track.status)
                if color:
                    return QBrush(color)
            if index.column() == 9 and track.confidence:
                if track.confidence >= 0.8:
                    return QBrush(QColor("#3fb950"))
                if track.confidence >= 0.62:
                    return QBrush(QColor("#d29922"))
                return QBrush(QColor("#f85149"))
            if index.column() == 12 and track.notes:
                if track.status is TrackStatus.FAILED:
                    return QBrush(QColor("#f85149"))
            return None

        if role == Qt.FontRole and index.column() == 1:
            font = QFont()
            font.setBold(bool(track.working.title))
            return font

        if role == Qt.TextAlignmentRole and index.column() in (5, 6, 7, 8, 9):
            return int(Qt.AlignCenter)

        return None

    def _tooltip(self, track: Track) -> str:
        lines = [
            f"路径：{track.path}",
            f"状态：{track.status.value}",
            f"技术：{track.info.fmt} / {track.info.spec_text} / {track.info.bitrate_text}"
            f" / {track.info.kind_text}",
        ]
        if track.cue:
            lines.append(f"CUE：{track.cue.cue_path.name}（{len(track.cue.tracks)} 轨）")
        if track.backup_path:
            lines.append(f"备份：{track.backup_path}")
        if track.lyrics_src:
            lines.append(f"歌词来源：{track.lyrics_src}")
        if track.changed_fields:
            lines.append(f"变更字段：{', '.join(track.changed_fields)}")
        if track.notes:
            lines.append("说明：")
            lines.extend(f"  · {n}" for n in track.notes)
        return "\n".join(lines)

    # ---- 数据操作 ----
    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self._tracks = tracks
        self.endResetModel()

    def add_tracks(self, tracks: list[Track]) -> int:
        if not tracks:
            return 0
        self.beginInsertRows(QModelIndex(), len(self._tracks), len(self._tracks) + len(tracks) - 1)
        self._tracks.extend(tracks)
        self.endInsertRows()
        return len(tracks)

    def track_at(self, row: int) -> Optional[Track]:
        if 0 <= row < len(self._tracks):
            return self._tracks[row]
        return None

    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def refresh_row(self, track: Track) -> None:
        try:
            row = self._tracks.index(track)
        except ValueError:
            return
        self.dataChanged.emit(self.index(row, 0), self.index(row, len(COLUMNS) - 1))

    def refresh_all(self) -> None:
        if not self._tracks:
            return
        self.dataChanged.emit(
            self.index(0, 0), self.index(len(self._tracks) - 1, len(COLUMNS) - 1)
        )

    def remove_rows(self, rows: list[int]) -> int:
        removed = 0
        for row in sorted(set(rows), reverse=True):
            if 0 <= row < len(self._tracks):
                self.beginRemoveRows(QModelIndex(), row, row)
                self._tracks.pop(row)
                self.endRemoveRows()
                removed += 1
        return removed

    def clear(self) -> None:
        self.set_tracks([])


class TrackFilterProxy(QSortFilterProxyModel):
    """按文本与「只看无损 / 只看失败」筛选。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._lossless_only = False
        self._problems_only = False
        self.setSortRole(Qt.DisplayRole)

    def set_text(self, text: str) -> None:
        self._text = (text or "").strip().lower()
        self.invalidateFilter()

    def set_lossless_only(self, enabled: bool) -> None:
        self._lossless_only = enabled
        self.invalidateFilter()

    def set_problems_only(self, enabled: bool) -> None:
        self._problems_only = enabled
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row: int, source_parent: QModelIndex) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        track = model.track_at(source_row)
        if track is None:
            return False
        if self._lossless_only and not track.info.lossless:
            return False
        if self._problems_only and track.status not in (
            TrackStatus.FAILED,
            TrackStatus.NEEDS_REVIEW,
            TrackStatus.UNSUPPORTED,
        ):
            return False
        if self._text:
            haystack = " ".join(
                [
                    track.path.name,
                    track.working.title,
                    track.working.artist,
                    track.working.album,
                    track.info.fmt,
                ]
            ).lower()
            if self._text not in haystack:
                return False
        return True
