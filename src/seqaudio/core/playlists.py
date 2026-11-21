"""用户自定义播放列表（JSON 存储）。

为什么不用数据库
----------------
一个播放列表就是「名字 + 一串路径」，几百条顶多几十 KB。引入 SQLite 要背上
迁移、锁与打包依赖，收益为零；JSON 还能让用户自己备份/手改。

可靠性
------
* 写入走「临时文件 + ``os.replace``」——Windows 上进程被杀也不会留下半截 JSON；
* 读到坏文件**不抛异常**：把它备份成 ``playlists.corrupt-<时间>.json`` 后按空表
  继续，播放列表坏了不该让整个软件起不来；
* 名称做白名单校验（不含 ``\\/:*?"<>|``、非空、≤60 字），避免被当成路径用。
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from . import paths

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
MAX_NAME_LENGTH = 60
_ILLEGAL_NAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def _now_text() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def clean_name(name: str) -> str:
    """规范化列表名：去首尾空白、压掉非法字符。非法时返回空串。"""
    text = str(name or "").strip()
    text = _ILLEGAL_NAME.sub("", text)
    return text[:MAX_NAME_LENGTH]


def _normalize_tracks(entries: Iterable[Any]) -> list[dict[str, Any]]:
    """把路径/字典/QueueItem 统一成 ``[{"path":..., "cue_track":...}]``。"""
    result: list[dict[str, Any]] = []
    for entry in entries or []:
        if isinstance(entry, dict):
            path_text = str(entry.get("path", "") or "")
            cue = entry.get("cue_track", None)
        elif isinstance(entry, (tuple, list)):
            path_text = str(entry[0]) if entry else ""
            cue = entry[1] if len(entry) > 1 else None
        else:
            path_text = str(getattr(entry, "path", entry) or "")
            cue = getattr(entry, "cue_track", None)
        if not path_text:
            continue
        item = {"path": path_text}
        if cue is not None:
            item["cue_track"] = cue
        result.append(item)
    return result


@dataclass
class Playlist:
    """一个已保存的播放列表。"""

    name: str
    tracks: list[dict[str, Any]] = field(default_factory=list)
    updated_at: str = ""

    @property
    def count(self) -> int:
        return len(self.tracks)

    def paths(self) -> list[str]:
        return [str(item.get("path", "")) for item in self.tracks if item.get("path")]

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "tracks": list(self.tracks),
                "updated_at": self.updated_at or _now_text()}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Optional["Playlist"]:
        name = clean_name(str(data.get("name", "") or ""))
        if not name:
            return None
        return cls(name=name,
                   tracks=_normalize_tracks(data.get("tracks") or []),
                   updated_at=str(data.get("updated_at", "") or ""))


class PlaylistStore:
    """``playlists.json`` 的读写门面（全部方法都不会抛业务异常）。"""

    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path is not None else paths.data_dir() / "playlists.json"

    # ------------------------------------------------------------ 读写

    def _read(self) -> list[Playlist]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError as exc:
            log.warning("读取播放列表失败（按空表处理）：%s", exc)
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError) as exc:
            log.warning("播放列表 JSON 损坏，已备份并按空表处理：%s", exc)
            self._quarantine()
            return []
        if not isinstance(data, dict):
            self._quarantine()
            return []
        version = data.get("version")
        if isinstance(version, int) and version > SCHEMA_VERSION:
            log.warning("播放列表版本 %s 高于本程序（%s），忽略", version, SCHEMA_VERSION)
            return []
        items = data.get("items")
        if not isinstance(items, list):
            return []
        result: list[Playlist] = []
        for entry in items:
            if isinstance(entry, dict):
                playlist = Playlist.from_dict(entry)
                if playlist is not None:
                    result.append(playlist)
        return result

    def _write(self, items: list[Playlist]) -> bool:
        payload = {
            "version": SCHEMA_VERSION,
            "items": [item.to_dict() for item in items],
        }
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except OSError as exc:
            log.warning("保存播放列表失败：%s", exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            return False

    def _quarantine(self) -> None:
        """坏文件改名留档，避免下次启动重复报错，也不静默删用户数据。"""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        try:
            self.path.replace(self.path.with_name(f"playlists.corrupt-{stamp}.json"))
        except OSError:
            pass

    # ------------------------------------------------------------ 查询

    def all(self) -> list[Playlist]:
        return self._read()

    def names(self) -> list[str]:
        return [item.name for item in self._read()]

    def get(self, name: str) -> Optional[Playlist]:
        key = clean_name(name)
        for item in self._read():
            if item.name == key:
                return item
        return None

    def count(self) -> int:
        return len(self._read())

    # ------------------------------------------------------------ 修改

    def save(self, name: str, tracks: Iterable[Any]) -> bool:
        """新建或覆盖同名列表（覆盖后移到最前，符合「最近使用」直觉）。"""
        key = clean_name(name)
        if not key:
            return False
        items = self._read()
        payload = Playlist(name=key, tracks=_normalize_tracks(tracks), updated_at=_now_text())
        items = [item for item in items if item.name != key]
        items.insert(0, payload)
        return self._write(items)

    def delete(self, name: str) -> bool:
        key = clean_name(name)
        items = self._read()
        remaining = [item for item in items if item.name != key]
        if len(remaining) == len(items):
            return False
        return self._write(remaining)

    def rename(self, old: str, new: str) -> bool:
        old_key, new_key = clean_name(old), clean_name(new)
        if not old_key or not new_key:
            return False
        items = self._read()
        if any(item.name == new_key for item in items if item.name != old_key):
            return False
        changed = False
        for item in items:
            if item.name == old_key:
                item.name = new_key
                item.updated_at = _now_text()
                changed = True
        return self._write(items) if changed else False

    def add_tracks(self, name: str, tracks: Iterable[Any]) -> int:
        """往已有列表追加（按路径去重），返回新增条数；列表不存在返回 0。"""
        key = clean_name(name)
        items = self._read()
        target = next((item for item in items if item.name == key), None)
        if target is None:
            return 0
        seen = {str(entry.get("path", "")).lower() for entry in target.tracks}
        added = 0
        for entry in _normalize_tracks(tracks):
            lowered = str(entry["path"]).lower()
            if lowered in seen:
                continue
            seen.add(lowered)
            target.tracks.append(entry)
            added += 1
        if added:
            target.updated_at = _now_text()
            self._write(items)
        return added

    def remove_at(self, name: str, index: int) -> bool:
        key = clean_name(name)
        items = self._read()
        target = next((item for item in items if item.name == key), None)
        if target is None or not (0 <= int(index) < len(target.tracks)):
            return False
        target.tracks.pop(int(index))
        target.updated_at = _now_text()
        return self._write(items)

    def move_at(self, name: str, index: int, delta: int) -> bool:
        """在列表内上移/下移一项（``delta`` 为 -1 / +1）。"""
        key = clean_name(name)
        items = self._read()
        target = next((item for item in items if item.name == key), None)
        if target is None:
            return False
        source = int(index)
        destination = source + int(delta)
        if not (0 <= source < len(target.tracks)) or not (0 <= destination < len(target.tracks)):
            return False
        target.tracks[source], target.tracks[destination] = (
            target.tracks[destination], target.tracks[source]
        )
        target.updated_at = _now_text()
        return self._write(items)
