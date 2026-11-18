"""播放会话：上次播到哪儿、队列是什么、用什么设备。

为什么用 JSON 而不是数据库
--------------------------
会话是「一次性、很小、坏了也不该影响启动」的数据：几十条路径加一个位置。
引入 SQLite 只为一个 2 KB 的文件毫无收益，而 JSON 有肉眼可读、可手工删掉重来
的优点。写入用「临时文件 + ``os.replace``」——Windows 上进程被杀时，
``os.replace`` 是原子的，不会留下半截 JSON 让下次启动读崩。

版本策略
--------
``SCHEMA_VERSION`` 变了说明字段语义变了。读到**未来版本**（比本进程新）时
直接返回 ``None`` 当作没有会话 —— 用旧程序去猜新格式的字段，比丢掉一次
播放位置糟糕得多。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from ..core import paths
from .constants import PlayMode
from .queue import QueueItem

log = logging.getLogger(__name__)

#: 会话文件格式版本
SCHEMA_VERSION = 1


def _utc_now_text() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _normalize_tracks(queue_paths: Iterable[Any]) -> list[dict[str, Any]]:
    """把各种形式的队列输入统一成 ``[{"path":..., "cue_track":...}]``。"""
    result: list[dict[str, Any]] = []
    for entry in queue_paths or []:
        if isinstance(entry, QueueItem):
            result.append({"path": str(entry.path), "cue_track": entry.cue_track})
        elif isinstance(entry, dict):
            result.append(
                {
                    "path": str(entry.get("path", "") or ""),
                    "cue_track": entry.get("cue_track", None),
                }
            )
        elif isinstance(entry, (tuple, list)):
            path_text = str(entry[0]) if entry else ""
            cue = entry[1] if len(entry) > 1 else None
            result.append({"path": path_text, "cue_track": cue})
        else:
            result.append({"path": str(entry), "cue_track": None})
    return result


@dataclass
class PlaybackSession:
    """一次会话的快照（可序列化，不含任何运行时对象）。"""

    version: int = SCHEMA_VERSION
    tracks: list[dict[str, Any]] = field(default_factory=list)
    index: int = -1
    position_ms: int = 0
    mode: str = PlayMode.SEQUENTIAL.value
    output_device: str = ""
    output_backend: str = ""
    volume_db: float = 0.0
    eq_gains: list[float] = field(default_factory=list)
    saved_at: str = ""

    # ------------------------------------------------------------ 序列化

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "saved_at": self.saved_at or _utc_now_text(),
            "tracks": [dict(item) for item in self.tracks],
            "index": int(self.index),
            "position_ms": int(self.position_ms),
            "mode": str(self.mode),
            "output_device": str(self.output_device),
            "output_backend": str(self.output_backend),
            "volume_db": float(self.volume_db),
            "eq_gains": [float(value) for value in self.eq_gains],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["PlaybackSession"]:
        """从字典还原；版本不认识或结构明显不对时返回 ``None``（绝不抛）。"""
        if not isinstance(data, dict):
            return None
        try:
            version = int(data.get("version", 0) or 0)
        except (TypeError, ValueError):
            return None
        if version != SCHEMA_VERSION:
            log.info("忽略版本 %s 的播放会话（本版本支持 %s）", version, SCHEMA_VERSION)
            return None

        raw_tracks = data.get("tracks") or []
        if not isinstance(raw_tracks, list):
            return None
        tracks = _normalize_tracks(raw_tracks)
        try:
            index = int(data.get("index", -1) or -1)
        except (TypeError, ValueError):
            index = -1
        if tracks:
            index = max(0, min(index, len(tracks) - 1))
        else:
            index = -1
        try:
            position_ms = max(0, int(data.get("position_ms", 0) or 0))
        except (TypeError, ValueError):
            position_ms = 0
        mode = str(data.get("mode", PlayMode.SEQUENTIAL.value) or PlayMode.SEQUENTIAL.value)
        try:
            PlayMode(mode)
        except ValueError:
            mode = PlayMode.SEQUENTIAL.value
        try:
            volume_db = float(data.get("volume_db", 0.0) or 0.0)
        except (TypeError, ValueError):
            volume_db = 0.0
        raw_gains = data.get("eq_gains") or []
        gains: list[float] = []
        if isinstance(raw_gains, list):
            for value in raw_gains:
                try:
                    gains.append(float(value))
                except (TypeError, ValueError):
                    gains.append(0.0)

        return cls(
            version=version,
            tracks=tracks,
            index=index,
            position_ms=position_ms,
            mode=mode,
            output_device=str(data.get("output_device", "") or ""),
            output_backend=str(data.get("output_backend", "") or ""),
            volume_db=volume_db,
            eq_gains=gains,
            saved_at=str(data.get("saved_at", "") or ""),
        )

    # ------------------------------------------------------------ 存取

    def save(self, path: Optional[Path] = None) -> bool:
        """原子写会话；失败只记日志并返回 ``False``（不该阻断退出流程）。"""
        target = Path(path) if path is not None else paths.session_path()
        self.saved_at = self.saved_at or _utc_now_text()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
            )
            os.replace(tmp, target)
            return True
        except OSError as exc:
            log.warning("保存播放会话失败（不影响退出）：%s", exc)
            return False

    @classmethod
    def load(cls, path: Optional[Path] = None) -> Optional["PlaybackSession"]:
        """读会话；文件不存在/损坏/版本不认识都返回 ``None``。"""
        target = Path(path) if path is not None else paths.session_path()
        try:
            raw = target.read_text(encoding="utf-8")
        except OSError:
            return None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            log.warning("播放会话文件损坏，已忽略：%s", exc)
            return None
        return cls.from_dict(data)

    @classmethod
    def capture(cls, engine) -> "PlaybackSession":
        """从引擎抓一份当前状态（退出时调用）。

        只用引擎的公开只读接口，避免会话层反向依赖引擎内部字段。
        """
        snapshot = engine.snapshot() if hasattr(engine, "snapshot") else {}
        tracks = [
            {"path": item.get("path", ""), "cue_track": item.get("cue_track")}
            for item in snapshot.get("queue", [])
        ]
        return cls(
            tracks=tracks,
            index=int(snapshot.get("queue_index", -1)),
            position_ms=int(snapshot.get("position_ms", 0)),
            mode=str(snapshot.get("mode", PlayMode.SEQUENTIAL.value)),
            output_device=str(snapshot.get("device", "")),
            output_backend=str(snapshot.get("backend", "")),
            volume_db=float(snapshot.get("volume_db", 0.0)),
            eq_gains=[float(v) for v in snapshot.get("eq_gains", [])],
        )

    # ------------------------------------------------------------ 便利读数

    def items(self) -> list[QueueItem]:
        """把会话里的路径还原成队列项（不含标签，需界面自行 probe 补全）。"""
        return [
            QueueItem(path=Path(str(entry.get("path", ""))), cue_track=entry.get("cue_track"))
            for entry in self.tracks
            if str(entry.get("path", "") or "").strip()
        ]

    def is_empty(self) -> bool:
        return not self.tracks


# ---------------------------------------------------------------- 模块级便捷函数


def save_session(
    queue_paths: Sequence[Any],
    index: int,
    position_ms: int,
    mode: PlayMode | str,
    output_device: str,
    volume_db: float,
    *,
    output_backend: str = "",
    eq_gains: Optional[Sequence[float]] = None,
    path: Optional[Path] = None,
) -> bool:
    """写会话（任务书 T03 的模块级入口，等价于构造 ``PlaybackSession`` 后 save）。"""
    session = PlaybackSession(
        tracks=_normalize_tracks(queue_paths),
        index=int(index),
        position_ms=max(0, int(position_ms)),
        mode=mode.value if isinstance(mode, PlayMode) else str(mode),
        output_device=str(output_device or ""),
        output_backend=str(output_backend or ""),
        volume_db=float(volume_db),
        eq_gains=[float(value) for value in (eq_gains or [])],
    )
    return session.save(path)


def load_session(path: Optional[Path] = None) -> Optional[PlaybackSession]:
    """读会话（``None`` 表示没有可用会话）。"""
    return PlaybackSession.load(path)


def clear_session(path: Optional[Path] = None) -> None:
    """删掉会话文件（用户关闭「恢复会话」时用）。"""
    target = Path(path) if path is not None else paths.session_path()
    try:
        target.unlink(missing_ok=True)
    except OSError as exc:  # pragma: no cover - 权限/占用
        log.warning("删除播放会话失败：%s", exc)


__all__ = [
    "SCHEMA_VERSION",
    "PlaybackSession",
    "save_session",
    "load_session",
    "clear_session",
]
