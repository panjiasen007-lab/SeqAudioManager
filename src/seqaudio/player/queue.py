"""播放队列与四种播放模式。

为什么队列项是「轻量投影」而不是 ``Track``
------------------------------------------
``Track`` 有二十多个字段（原始标签、工作标签、鉴定结果、备份路径…），一个
5000 首的队列如果直接持有 ``Track`` 引用，队列面板每次刷新都要碰这些字段；
更麻烦的是 ``ProcessorWorker`` 会持续改写 ``Track.working``，播放侧不该跟着
抖动。所以队列只存**播放真正需要的东西**：路径、CUE 轨序、标题三件套、时长、
规格，以及 CUE 的起止边界（这样引擎不必回头再解析 CUE 文件）。

设计文档 3.5 还留了「按 path + cue_track 反查完整 Track」的口子 —— 需要显示
标签细节时由界面层自己反查，队列不参与。
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from .constants import PlayMode

log = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """队列中的一条（``Track`` 的播放侧投影）。"""

    path: Path
    cue_track: Optional[int] = None      # CUE 展开后的子轨索引（整轨为 None）
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: float = 0.0                # 秒
    sample_rate: int = 0
    channels: int = 0
    bit_depth: int = 0
    #: CUE 子轨在**文件内**的起止（毫秒）；0/0 表示从文件头播到尾
    start_ms: int = 0
    duration_ms: int = 0
    #: 是否 DSD 源（信号路径与解码参数要用）
    is_dsd: bool = False

    @property
    def key(self) -> tuple[str, Optional[int]]:
        """去重/定位用的稳定键。"""
        return (str(self.path), self.cue_track)

    @property
    def display(self) -> str:
        """``歌手 - 标题`` 或退化到文件名。"""
        title = self.title or self.path.stem
        return f"{self.artist} - {title}" if self.artist else title

    def to_dict(self) -> dict[str, Any]:
        """可 JSON 序列化（会话文件与信号路径事件都用它）。"""
        return {
            "path": str(self.path),
            "cue_track": self.cue_track,
            "title": self.title,
            "artist": self.artist,
            "album": self.album,
            "duration": float(self.duration),
            "sample_rate": int(self.sample_rate),
            "channels": int(self.channels),
            "bit_depth": int(self.bit_depth),
            "start_ms": int(self.start_ms),
            "duration_ms": int(self.duration_ms),
            "is_dsd": bool(self.is_dsd),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QueueItem":
        """从会话/事件字典还原；缺字段一律按默认值兜底，不抛异常。"""
        payload = data or {}
        path_text = str(payload.get("path", "") or "")
        cue = payload.get("cue_track", None)
        try:
            cue_track = None if cue is None else int(cue)
        except (TypeError, ValueError):
            cue_track = None
        return cls(
            path=Path(path_text),
            cue_track=cue_track,
            title=str(payload.get("title", "") or ""),
            artist=str(payload.get("artist", "") or ""),
            album=str(payload.get("album", "") or ""),
            duration=float(payload.get("duration", 0.0) or 0.0),
            sample_rate=int(payload.get("sample_rate", 0) or 0),
            channels=int(payload.get("channels", 0) or 0),
            bit_depth=int(payload.get("bit_depth", 0) or 0),
            start_ms=int(payload.get("start_ms", 0) or 0),
            duration_ms=int(payload.get("duration_ms", 0) or 0),
            is_dsd=bool(payload.get("is_dsd", False)),
        )

    @classmethod
    def from_track(cls, track) -> "QueueItem":
        """由 ``core.model.Track`` 生成投影（只读，不修改 Track）。"""
        path = Path(track.path)
        info = getattr(track, "info", None)
        start_ms = 0
        duration_ms = 0
        cue_track = getattr(track, "cue_track", None)
        entry = getattr(track, "cue_track_entry", None)
        if entry is not None:
            start_ms = max(0, int(getattr(entry, "start_ms", 0) or 0))
            end_ms = int(getattr(entry, "end_ms", 0) or 0)
            if end_ms > start_ms:
                duration_ms = end_ms - start_ms
        cue_duration = duration_ms / 1000.0 if duration_ms > 0 else 0.0
        info_duration = float(getattr(info, "duration", 0.0) or 0.0)
        return cls(
            path=path,
            cue_track=None if cue_track is None else int(cue_track),
            title=str(getattr(track, "title", "") or ""),
            artist=str(getattr(track, "artist", "") or ""),
            album=str(getattr(track, "album", "") or ""),
            duration=cue_duration or info_duration,
            sample_rate=int(getattr(info, "sample_rate", 0) or 0),
            channels=int(getattr(info, "channels", 0) or 0),
            bit_depth=int(getattr(info, "bit_depth", 0) or 0),
            start_ms=start_ms,
            duration_ms=duration_ms,
            is_dsd=bool(getattr(info, "dsd_rate", None)),
        )


class PlayQueue:
    """列表 + 当前索引 + 播放模式。

    随机模式的「一轮内不重复」用**待播池**实现：``_shuffle_pool`` 里是还没播过
    的下标，抽一个少一个；池空了就重洗一轮（并避开刚刚播完的那一首，否则听感上
    还是「同一首连播两次」）。``_history`` 让「上一首」在随机模式下也有意义。
    """

    def __init__(
        self,
        items: Optional[Iterable[QueueItem]] = None,
        mode: PlayMode = PlayMode.SEQUENTIAL,
    ) -> None:
        self.items: list[QueueItem] = list(items or [])
        self.index: int = 0 if self.items else -1
        self.mode: PlayMode = mode if isinstance(mode, PlayMode) else PlayMode(mode)
        self._shuffle_pool: list[int] = []
        self._history: list[int] = []
        self._reset_shuffle_pool()

    # ------------------------------------------------------------ 构造

    @classmethod
    def from_tracks(
        cls, tracks: Sequence[Any], mode: PlayMode = PlayMode.SEQUENTIAL
    ) -> "PlayQueue":
        """由 ``Track`` 列表构造（自动投影）。"""
        return cls((QueueItem.from_track(track) for track in tracks), mode=mode)

    # ------------------------------------------------------------ 只读

    def __len__(self) -> int:
        return len(self.items)

    def __bool__(self) -> bool:
        return bool(self.items)

    def snapshot(self) -> list[dict[str, Any]]:
        """整个队列的可序列化快照（界面渲染与日志用）。"""
        return [item.to_dict() for item in self.items]

    def current(self) -> Optional[QueueItem]:
        """当前项；空队列或索引越界时 ``None``。"""
        if 0 <= self.index < len(self.items):
            return self.items[self.index]
        return None

    def at(self, index: int) -> Optional[QueueItem]:
        if 0 <= int(index) < len(self.items):
            return self.items[int(index)]
        return None

    def total_sec(self) -> float:
        return float(sum(max(0.0, item.duration) for item in self.items))

    def index_of(self, item_or_key: QueueItem | tuple[str, Optional[int]]) -> int:
        """按 item 或 ``key`` 找下标；找不到返回 ``-1``。"""
        key = item_or_key.key if isinstance(item_or_key, QueueItem) else item_or_key
        for position, item in enumerate(self.items):
            if item.key == key:
                return position
        return -1

    # ------------------------------------------------------------ 修改

    def load(self, tracks_or_items: Iterable[Any], start_index: int = 0) -> None:
        """整体换队列（Track 或 QueueItem 都可）。"""
        converted: list[QueueItem] = []
        for entry in tracks_or_items:
            if isinstance(entry, QueueItem):
                converted.append(entry)
            else:
                converted.append(QueueItem.from_track(entry))
        self.items = converted
        self.index = max(0, min(int(start_index), len(converted) - 1)) if converted else -1
        self._history.clear()
        self._reset_shuffle_pool()

    def append(self, track_or_item: Any) -> int:
        """追加到队尾，返回新下标。"""
        item = (
            track_or_item
            if isinstance(track_or_item, QueueItem)
            else QueueItem.from_track(track_or_item)
        )
        self.items.append(item)
        if len(self.items) == 1 and self.index < 0:
            self.index = 0
        if self.mode is PlayMode.SHUFFLE:
            # 新项插进本轮待播池的随机位置：否则「一轮不重复」会退化成
            # 「新加的歌永远排在最后」，插播场景下听感很怪
            new_index = len(self.items) - 1
            self._shuffle_pool.insert(random.randrange(len(self._shuffle_pool) + 1), new_index)
        return len(self.items) - 1

    def append_last(self, track_or_item: Any) -> int:
        """「稍后播放」= 追加队尾（与 ``append`` 同义，语义化别名）。"""
        return self.append(track_or_item)

    def insert_after_current(self, track_or_item: Any) -> int:
        """「下一首播」= 插到当前项之后，返回新下标。"""
        item = (
            track_or_item
            if isinstance(track_or_item, QueueItem)
            else QueueItem.from_track(track_or_item)
        )
        if not self.items or self.index < 0:
            self.items.append(item)
            self.index = 0
            self._reset_shuffle_pool()
            return 0
        position = self.index + 1
        self.items.insert(position, item)
        # 插入会移位所有后续下标：待播池与历史都要跟着平移
        self._shuffle_pool = [i + 1 if i >= position else i for i in self._shuffle_pool]
        self._history = [i + 1 if i >= position else i for i in self._history]
        return position

    def enqueue_next(self, track_or_item: Any) -> int:
        """``insert_after_current`` 的别名（任务书 T03 的命名）。"""
        return self.insert_after_current(track_or_item)

    def enqueue_last(self, track_or_item: Any) -> int:
        """``append_last`` 的别名（任务书 T03 的命名）。"""
        return self.append_last(track_or_item)

    def remove_at(self, index: int) -> Optional[QueueItem]:
        """移除一项并修正当前索引。"""
        position = int(index)
        if not (0 <= position < len(self.items)):
            return None
        removed = self.items.pop(position)
        if not self.items:
            self.index = -1
            self._reset_shuffle_pool()
            return removed
        if position < self.index:
            self.index -= 1
        elif position == self.index:
            self.index = min(self.index, len(self.items) - 1)
        self._shuffle_pool = [
            i - 1 if i > position else i for i in self._shuffle_pool if i != position
        ]
        self._history = [i - 1 if i > position else i for i in self._history if i != position]
        return removed

    def move(self, source: int, target: int) -> bool:
        """把 ``source`` 移到 ``target``（拖拽排序）。"""
        src, dst = int(source), int(target)
        if not (0 <= src < len(self.items)) or not (0 <= dst < len(self.items)) or src == dst:
            return False
        item = self.items.pop(src)
        self.items.insert(dst, item)
        current = self.current()
        self.index = self.index_of(current.key) if current is not None else -1
        self._reset_shuffle_pool()
        return True

    def clear(self) -> None:
        self.items.clear()
        self.index = -1
        self._history.clear()
        self._shuffle_pool.clear()

    def set_mode(self, mode: PlayMode | str) -> PlayMode:
        """切换模式；进入随机时重洗一轮（从当前项之后开始不重复）。"""
        self.mode = mode if isinstance(mode, PlayMode) else PlayMode(mode)
        self._history.clear()
        self._reset_shuffle_pool()
        log.debug("播放模式 -> %s", self.mode.value)
        return self.mode

    # ------------------------------------------------------------ 导航

    def jump_to(self, index: int) -> Optional[QueueItem]:
        """跳到指定下标（越界返回 ``None``，不改变当前项）。"""
        position = int(index)
        if not (0 <= position < len(self.items)):
            return None
        self._remember_current()
        self.index = position
        if self.mode is PlayMode.SHUFFLE:
            if position in self._shuffle_pool:
                self._shuffle_pool.remove(position)
        return self.current()

    def next(self, auto_advance: bool = False) -> Optional[QueueItem]:
        """取下一项。

        Args:
            auto_advance: ``True`` 表示是「一首播完」触发的自动续播，
                单曲循环时应当重播本首；``False`` 表示用户点了「下一首」，
                此时必须真的换歌（否则按钮像坏了）。

        Returns:
            下一项；到队尾且非循环（或空队列）时 ``None``。
        """
        if not self.items or self.index < 0:
            return None

        if self.mode is PlayMode.REPEAT_ONE and auto_advance:
            return self.current()

        if self.mode is PlayMode.SHUFFLE:
            return self._next_shuffle()

        target = self.index + 1
        if target >= len(self.items):
            if self.mode is PlayMode.REPEAT_ALL:
                target = 0
            else:
                return None
        self._remember_current()
        self.index = target
        return self.current()

    def previous(self) -> Optional[QueueItem]:
        """上一项；队首时顺序/单曲模式返回 ``None``（不循环回绕）。"""
        if not self.items or self.index < 0:
            return None
        if self.mode is PlayMode.SHUFFLE:
            while self._history:
                candidate = self._history.pop()
                if 0 <= candidate < len(self.items):
                    self.index = candidate
                    return self.current()
            return self.current()
        target = self.index - 1
        if target < 0:
            if self.mode is PlayMode.REPEAT_ALL:
                target = len(self.items) - 1
            else:
                return None
        self.index = target
        return self.current()

    # ------------------------------------------------------------ 内部

    def _remember_current(self) -> None:
        if 0 <= self.index < len(self.items):
            self._history.append(self.index)
            if len(self._history) > 512:  # 长时间播放不该无限长
                del self._history[: len(self._history) - 512]

    def _reset_shuffle_pool(self) -> None:
        """重洗待播池：当前项不算「还没播」，其余全部入池并打乱。"""
        self._shuffle_pool = [i for i in range(len(self.items)) if i != self.index]
        random.shuffle(self._shuffle_pool)

    def _next_shuffle(self) -> Optional[QueueItem]:
        if not self.items:
            return None
        if not self._shuffle_pool:
            # 一轮播完：重洗一轮，并避开刚刚播过的那一首
            previous = self.index
            self._remember_current()
            pool = [i for i in range(len(self.items))]
            random.shuffle(pool)
            if len(pool) > 1 and pool[0] == previous:
                pool.append(pool.pop(0))
            self._shuffle_pool = pool
        target = self._shuffle_pool.pop(0)
        self._remember_current()
        self.index = target
        return self.current()

    def __repr__(self) -> str:  # pragma: no cover - 日志/调试
        return f"<PlayQueue {len(self.items)} 项 @ {self.index} {self.mode.value}>"


__all__ = ["QueueItem", "PlayQueue"]
