"""MusicBrainz 数据源适配（主元数据源，需求 3.2.1）。

公开 API：``https://musicbrainz.org/ws/2``
使用条款要求：必须带可识别的 User-Agent、单 IP 平均 1 req/s 限流、结果缓存。
这里通过 ``HostRateLimiter`` + ``DiskCache`` 双重保障，避免被封。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

from ..core.model import MatchCandidate
from .http import HttpClient, NetworkError

log = logging.getLogger(__name__)

BASE = "https://musicbrainz.org/ws/2"
#: MusicBrainz 硬性要求：1 次/秒
MIN_INTERVAL = 1.1

_LUCENE_SPECIAL = re.compile(r'([+\-&|!(){}\[\]^"~*?:\\/])')


def _escape(value: str) -> str:
    return _LUCENE_SPECIAL.sub(r"\\\1", value.replace('"', ""))


def build_query(title: str, artist: str = "", album: str = "") -> str:
    parts = []
    if title:
        parts.append(f'recording:"{_escape(title)}"')
    if artist:
        parts.append(f'artist:"{_escape(artist)}"')
    if album:
        parts.append(f'release:"{_escape(album)}"')
    return " AND ".join(parts) or f'recording:"{_escape(title)}"'


def search_recordings(
    client: HttpClient,
    title: str,
    artist: str = "",
    album: str = "",
    *,
    limit: int = 6,
) -> list[MatchCandidate]:
    """按标题/歌手检索录音，返回候选列表（已按源分数降序）。"""
    if not title.strip():
        return []
    query = build_query(title, artist, album)
    params = {"query": query, "fmt": "json", "limit": str(limit)}
    try:
        payload = client.get_json(f"{BASE}/recording", params=params, min_interval=MIN_INTERVAL)
    except NetworkError as exc:
        log.info("MusicBrainz 检索失败：%s", exc)
        return []
    except Exception as exc:  # noqa: BLE001
        log.warning("MusicBrainz 异常：%s", exc)
        return []

    recordings = (payload or {}).get("recordings") or []
    candidates: list[MatchCandidate] = []
    for item in recordings:
        candidate = _to_candidate(item)
        if candidate:
            candidates.append(candidate)
    candidates.sort(key=lambda c: c.raw_score, reverse=True)
    return candidates


def _artist_name(item: dict[str, Any]) -> str:
    credits = item.get("artist-credit") or []
    names: list[str] = []
    for credit in credits:
        if isinstance(credit, str):
            names.append(credit)
            continue
        name = credit.get("name") or (credit.get("artist") or {}).get("name") or ""
        if name:
            names.append(name + (credit.get("joinphrase") or ""))
    return "".join(names).strip()


def _pick_release(item: dict[str, Any]) -> Optional[dict[str, Any]]:
    releases = item.get("releases") or []
    if not releases:
        return None
    # 优先：带 media/track 信息、有日期、非合辑
    def score(release: dict[str, Any]) -> tuple[int, str]:
        media = release.get("media") or []
        has_tracks = any(m.get("track") for m in media)
        rg = release.get("release-group") or {}
        primary = (rg.get("primary-type") or "").lower()
        weight = 0
        if has_tracks:
            weight += 3
        if release.get("date"):
            weight += 2
        if primary in ("album", "ep"):
            weight += 2
        if primary in ("compilation", "live", "remix"):
            weight -= 2
        return weight, release.get("date") or ""

    return max(releases, key=score)


def _track_number(release: dict[str, Any], recording_id: str, title: str) -> tuple[str, str]:
    media = release.get("media") or []
    for medium in media:
        disc_pos = str(medium.get("position") or 1)
        for track in medium.get("track") or []:
            if recording_id and track.get("id") == recording_id:
                return str(track.get("number") or ""), disc_pos
        # 有些返回体没有 track.id，用标题回退
        for track in medium.get("track") or []:
            if title and (track.get("title") or "").strip().lower() == title.strip().lower():
                return str(track.get("number") or ""), disc_pos
    return "", ""


def _to_candidate(item: dict[str, Any]) -> Optional[MatchCandidate]:
    title = (item.get("title") or "").strip()
    if not title:
        return None
    recording_id = item.get("id") or ""
    release = _pick_release(item) or {}
    track_no, disc_no = _track_number(release, recording_id, title)
    rg = release.get("release-group") or {}
    genres = item.get("genres") or rg.get("genres") or []
    genre = ""
    if genres:
        best = max(genres, key=lambda g: g.get("count", 0) or 0)
        genre = (best.get("name") or "").strip()

    return MatchCandidate(
        source="MusicBrainz",
        title=title,
        artist=_artist_name(item),
        album=(release.get("title") or "").strip(),
        albumartist=_artist_name(item),
        date=(release.get("date") or "").strip(),
        genre=genre,
        track=track_no,
        disc=disc_no,
        release_id=release.get("id") or "",
        recording_id=recording_id,
        raw_score=float(item.get("score") or 0) / 100.0,
        note=(rg.get("primary-type") or ""),
    )


def lookup_release(client: HttpClient, release_id: str) -> dict[str, Any]:
    """取完整 release 详情（含 media/track 与厂牌），用于补充信息。"""
    if not release_id:
        return {}
    try:
        return client.get_json(
            f"{BASE}/release/{release_id}",
            params={"fmt": "json", "inc": "recordings+artist-credits+genres"},
            min_interval=MIN_INTERVAL,
        ) or {}
    except NetworkError as exc:
        log.info("MusicBrainz release 查询失败：%s", exc)
        return {}
