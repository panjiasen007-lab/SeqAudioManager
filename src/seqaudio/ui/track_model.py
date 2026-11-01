"""曲目表格模型：把 Track 列表映射成 Qt 表格，支持排序与状态着色。"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QSortFilterProxyModel
from PySide6.QtGui import QColor, QBrush, QFont

from ..core.model import Track, TrackStatus

#: (表头, 取值函数, 列宽)
COLUMNS: list[tuple[str, Callable[[Track], Any], int]] = [
    ("状态", lambda t: t.status.value, 96),
    ("歌名", lambda t: t.working.title or t.original.title, 130),
    ("歌手", lambda t: t.working.artist or t.original.artist, 84),
    ("专辑", lambda t: t.working.album or t.original.album, 82),
    ("年份", lambda t: (t.working.date or t.original.date)[:4], 46),
    ("格式", lambda t: t.info.fmt, 50),
    ("规格", lambda t: t.info.spec_text, 114),
    ("时长", lambda t: t.duration_text, 46),
    ("无损", lambda t: t.info.kind_text, 42),
    ("置信度", lambda t: f"{t.confidence:.0%}" if t.confidence else "", 54),
    ("鉴定", lambda t: (t.authenticity.verdict if t.authenticity else ""), 80),
    ("来源", lambda t: t.source, 80),
    ("文件名", lambda t: (
        f"{t.path.name} · #{t.cue_track_entry.number}" if t.cue_track_entry else t.path.name
    ), 150),
    ("说明", lambda t: " | ".join(t.notes), 180),
]

#: 默认折叠的次要列：年份 / 时长 / 无损 / 来源 / 文件名 / 说明。
#: 1080px 窗口（1366 笔记本）下铺满 14 列必然横向滚动；这几列的信息
#: 在 tooltip 里都有，需要时可从表头右键随时展开。
HIDDEN_BY_DEFAULT = {4, 7, 8, 11, 12, 13}

#: 歌名随窗口伸缩，其余列固定宽度
STRETCH_COLUMN = 1

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
        self._row_of: dict[int, int] = self._build_index()

    def _build_index(self) -> dict[int, int]:
        """id(track) -> 行号。上千首时避免每次刷新都做 O(n) 的 list.index 扫描。"""
        return {id(t): i for i, t in enumerate(self._tracks)}

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
        if track.authenticity is not None:
            from .i18n import tr_verdict

            auth = track.authenticity
            lines.append(f"无损鉴定：{tr_verdict(auth.verdict)}（{auth.detail_text}）")
            lines.extend(f"  · {r}" for r in auth.reasons)
        if track.notes:
            lines.append("说明：")
            lines.extend(f"  · {n}" for n in track.notes)
        return "\n".join(lines)

    # ---- 数据操作 ----
    def set_tracks(self, tracks: list[Track]) -> None:
        self.beginResetModel()
        self._tracks = tracks
        self._row_of = self._build_index()
        self.endResetModel()

    def add_tracks(self, tracks: list[Track]) -> int:
        if not tracks:
            return 0
        self.beginInsertRows(QModelIndex(), len(self._tracks), len(self._tracks) + len(tracks) - 1)
        self._tracks.extend(tracks)
        self._row_of = self._build_index()
        self.endInsertRows()
        return len(tracks)

    def track_at(self, row: int) -> Optional[Track]:
        if 0 <= row < len(self._tracks):
            return self._tracks[row]
        return None

    def tracks(self) -> list[Track]:
        return list(self._tracks)

    def refresh_row(self, track: Track) -> None:
        row = self._row_of.get(id(track))
        if row is None:
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
                removed += 1
        if removed:
            self._row_of = self._build_index()
        return removed

    def remove_tracks(self, tracks: list[Track]) -> int:
        """按对象删除（清理/隔离后调用）。用 id 索引定位，避免逐行扫描。"""
        rows = [self._row_of[id(t)] for t in tracks if id(t) in self._row_of]
        return self.remove_rows(rows)

    def clear(self) -> None:
        self.set_tracks([])


class TrackFilterProxy(QSortFilterProxyModel):
    """按文本与「只看无损 / 只看失败」筛选。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._lossless_only = False
        self._problems_only = False
        self._verdict_group = "all"
        self.setSortRole(Qt.DisplayRole)

    def set_verdict_group(self, group: str) -> None:
        """按鉴定结论筛选：all / true / fake / suspect / suspect_any / unknown。"""
        self._verdict_group = group or "all"
        self.invalidateFilter()

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
        if self._verdict_group != "all":
            from ..core.authenticity import group_of

            verdict = track.authenticity.verdict if track.authenticity else ""
            group = group_of(verdict)
            if self._verdict_group == "suspect_any":
                if group not in ("fake", "suspect"):
                    return False
            elif group != self._verdict_group:
                return False
        if self._problems_only:
            problematic = track.status in (
                TrackStatus.FAILED,
                TrackStatus.NEEDS_REVIEW,
                TrackStatus.UNSUPPORTED,
            ) or (track.authenticity is not None and track.authenticity.is_suspicious)
            if not problematic:
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
