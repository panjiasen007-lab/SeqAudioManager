"""网易云音乐（第三方公开接口）元数据 / 歌词 / 封面源。

说明
----
网易云没有官方开放 API，这里走的是社区逆向出的公开查询接口，**仅供个人整理
自有音频时补全信息**。接口随时可能失效、限流，所以它只作为 LRCLIB / iTunes /
MusicBrainz 之后的**兜底源**，任何一次失败都静默跳过，绝不拖累整批任务。

歌词版权归原词曲权利人，本软件仅取回用户请求的歌词用于本地整理，不存储、不分发。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..core.model import MatchCandidate
from .http import HttpClient, NetworkError
from .lrclib import LyricsResult

log = logging.getLogger(__name__)

SEARCH_BASE = "https://music.163.com/api/search/get/web"
LYRIC_BASE = "https://music.163.com/api/song/lyric"
MIN_INTERVAL = 0.5


def _to_candidate(item: dict[str, Any], rank: int) -> Optional[MatchCandidate]:
    name = (item.get("name") or "").strip()
    if not name:
        return None
    album = item.get("album") or {}
    if not isinstance(album, dict):
        album = {}
    artists = item.get("artists") or []
    if not isinstance(artists, list):
        artists = []
    artist = " / ".join(
        (a.get("name") or "") for a in artists if isinstance(a, dict) and a.get("name")
    ).strip()
    if not artist and isinstance(album.get("artist"), dict):
        artist = (album["artist"].get("name") or "").strip()
    duration_ms = item.get("duration") or 0
    cover_url = (album.get("picUrl") or "").strip()
    if cover_url.startswith("http://"):
        cover_url = "https://" + cover_url[7:]

    # publishTime 是毫秒时间戳，转成年份；不是字符串不能直接切片
    publish = album.get("publishTime") or 0
    date = ""
    if isinstance(publish, str):
        date = publish[:4]
    elif isinstance(publish, (int, float)) and publish > 0:
        import datetime

        try:
            date = str(datetime.datetime.fromtimestamp(publish / 1000).year)
        except (OSError, ValueError, OverflowError):
            date = ""
    return MatchCandidate(
        source="网易云",
        title=name,
        artist=artist,
        album=(album.get("name") or "").strip(),
        albumartist=artist,
        date=date,
        track="",
        cover_url=cover_url,
        recording_id=str(item.get("id") or ""),
        duration=float(duration_ms) / 1000.0 if duration_ms else 0.0,
        raw_score=max(0.0, 0.85 - rank * 0.05),
        note=f"{int(duration_ms / 1000)}s" if duration_ms else "",
    )


def search(
    client: HttpClient,
    title: str,
    artist: str = "",
    *,
    limit: int = 8,
) -> list[MatchCandidate]:
    if not title.strip():
        return []
    term = f"{title} {artist}".strip()
    try:
        payload = client.get_json(
            SEARCH_BASE,
            params={"s": term, "type": "1", "limit": str(limit), "offset": "0"},
            min_interval=MIN_INTERVAL,
            attempts=2,
        )
    except NetworkError as exc:
        log.info("网易云检索失败：%s", exc)
        return []

    songs = ((payload or {}).get("result") or {}).get("songs") or []
    candidates: list[MatchCandidate] = []
    for rank, item in enumerate(songs):
        candidate = _to_candidate(item, rank)
        if candidate:
            candidates.append(candidate)
    return candidates


def lyrics(
    client: HttpClient,
    title: str,
    artist: str = "",
    song_id: str = "",
) -> Optional[LyricsResult]:
    """取 LRC 歌词。优先用已解析出的 song_id，否则先搜再取。"""
    if not song_id:
        candidates = search(client, title, artist, limit=3)
        if not candidates:
            return None
        song_id = candidates[0].recording_id
    if not song_id:
        return None
    try:
        payload = client.get_json(
            LYRIC_BASE,
            params={"id": song_id, "lv": "1", "kv": "1", "tv": "-1"},
            min_interval=MIN_INTERVAL,
            attempts=2,
        )
    except NetworkError as exc:
        log.info("网易云歌词获取失败：%s", exc)
        return None
    if not isinstance(payload, dict):
        return None
    lrc = payload.get("lrc") or {}
    synced = (lrc.get("lyric") or "").strip()
    if not synced:
        return None
    return LyricsResult(
        synced=synced,
        plain="",
        source="网易云",
        matched_title=title,
        matched_artist=artist,
        duration=0.0,
    )
