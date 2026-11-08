"""QQ 音乐元数据 / 歌词 / 封面源（社区公开接口，兜底源）。

说明
----
QQ 音乐没有官方开放 API，这里走的是社区逆向出的公开查询接口，**仅供个人整理
自有音频时补全信息**。接口随时可能失效、限流，所以它只作为 MusicBrainz / iTunes /
网易云之后的**兜底源**，任何一次失败都静默跳过，绝不拖累整批任务。

接口
----
* 搜索：``c.y.qq.com/soso/fcgi-bin/client_search_cp``（``new_json=1``）；
* 歌词：``c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg``（``nobase64=1``）；
* 封面：``y.gtimg.cn/music/photo_new/T002R300x300M000<albummid>.jpg``。

两个接口都要求 ``Referer: https://y.qq.com/``，否则返回 403 或空。

歌词版权归原词曲权利人，本软件仅取回用户请求的歌词用于本地整理，不存储、不分发。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..core.lyrics import is_synced
from ..core.model import MatchCandidate
from .http import HttpClient, NetworkError
from .lrclib import LyricsResult

log = logging.getLogger(__name__)

SEARCH_URL = "https://c.y.qq.com/soso/fcgi-bin/client_search_cp"
LYRIC_URL = "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg"
COVER_URL = "https://y.gtimg.cn/music/photo_new/T002R300x300M000{}.jpg"
REFERER = "https://y.qq.com/"
MIN_INTERVAL = 0.5


def _headers() -> dict[str, str]:
    return {"Referer": REFERER}


def _to_candidate(item: dict[str, Any], rank: int) -> Optional[MatchCandidate]:
    # new_json=1 返回 title，旧格式返回 songname，两者都兼容
    name = (item.get("title") or item.get("songname") or item.get("name") or "").strip()
    if not name:
        return None

    singers = item.get("singer") or []
    if not isinstance(singers, list):
        singers = []
    artist = " / ".join(
        (s.get("name") or "") for s in singers if isinstance(s, dict) and s.get("name")
    ).strip()

    album = item.get("album") or {}
    if not isinstance(album, dict):
        album = {}
    album_mid = (album.get("mid") or "").strip()
    cover_url = COVER_URL.format(album_mid) if album_mid else ""

    interval = item.get("interval") or 0
    try:
        duration = float(interval)
    except (TypeError, ValueError):
        duration = 0.0

    return MatchCandidate(
        source="QQ音乐",
        title=name,
        artist=artist,
        album=(album.get("name") or "").strip(),
        albumartist=artist,
        date="",
        track="",
        cover_url=cover_url,
        recording_id=str(item.get("mid") or item.get("songmid") or ""),
        duration=duration,
        raw_score=max(0.0, 0.82 - rank * 0.05),
        note=f"{int(duration)}s" if duration else "",
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
            SEARCH_URL,
            params={
                "w": term,
                "format": "json",
                "p": "1",
                "n": str(limit),
                "cr": "1",
                "new_json": "1",
                "t": "0",
            },
            min_interval=MIN_INTERVAL,
            attempts=2,
            headers=_headers(),
        )
    except NetworkError as exc:
        log.info("QQ 音乐检索失败：%s", exc)
        return []

    if not isinstance(payload, dict):
        log.info("QQ 音乐检索返回非对象（%s），按无结果处理", type(payload).__name__)
        return []
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []
    song = data.get("song") or {}
    if not isinstance(song, dict):
        return []
    songs = song.get("list") or []
    if not isinstance(songs, list):
        return []

    candidates: list[MatchCandidate] = []
    for rank, item in enumerate(songs):
        if not isinstance(item, dict):
            continue
        candidate = _to_candidate(item, rank)
        if candidate:
            candidates.append(candidate)
    return candidates


def lyrics(
    client: HttpClient,
    title: str,
    artist: str = "",
    song_mid: str = "",
    duration: float = 0.0,
) -> Optional[LyricsResult]:
    """取 LRC 歌词。优先用已解析出的 song_mid，否则先搜再取。"""
    picked: Optional[MatchCandidate] = None
    if not song_mid:
        from .netmusic import pick_best  # 同一套候选挑选规则，避免两处各写一遍

        picked = pick_best(search(client, title, artist, limit=5), duration, title)
        if picked is None:
            return None
        song_mid = picked.recording_id
    if not song_mid:
        return None

    try:
        payload = client.get_json(
            LYRIC_URL,
            params={
                "songmid": song_mid,
                "format": "json",
                "nobase64": "1",
                "g_tk": "5381",
            },
            min_interval=MIN_INTERVAL,
            attempts=2,
            headers=_headers(),
        )
    except NetworkError as exc:
        log.info("QQ 音乐歌词获取失败：%s", exc)
        return None
    if not isinstance(payload, dict):
        return None

    text = (payload.get("lyric") or "").strip()
    if not text:
        return None
    matched_title = picked.title if picked else title
    matched_artist = picked.artist if picked else artist
    matched_duration = picked.duration if picked else 0.0
    if is_synced(text):
        return LyricsResult(
            synced=text,
            plain="",
            source="QQ音乐",
            matched_title=matched_title,
            matched_artist=matched_artist,
            duration=matched_duration,
        )
    return LyricsResult(
        synced="",
        plain=text,
        source="QQ音乐",
        matched_title=matched_title,
        matched_artist=matched_artist,
        duration=matched_duration,
    )
