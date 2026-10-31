"""iTunes Search API 元数据/封面源（Apple 免费公开接口，无需鉴权）。

作为 MusicBrainz 之外的**第二元数据源**：
* 中文曲库里 iTunes 的简体条目往往比 MusicBrainz 的繁体条目更贴近用户本地标签，
  二者互相校验能明显压低误匹配；
* 封面直接给 ``artworkUrl``（600×600），绕开了 Cover Art Archive 在部分网络下
  完全不可达的问题。

接口：``https://itunes.apple.com/search``（``media=music&entity=song``）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..core.model import MatchCandidate
from .http import HttpClient, NetworkError

log = logging.getLogger(__name__)

BASE = "https://itunes.apple.com/search"
MIN_INTERVAL = 0.4          # 苹果不要求严格限流，但仍保持礼貌


def _upgrade_artwork(url: str, size: int = 600) -> str:
    """把 100×100 缩略图升级为 600×600（Apple 的图片 URL 可无损放大）。"""
    for token in ("100x100bb", "100x100"):
        if token in url:
            return url.replace(token, f"{size}x{size}bb")
    return url


def _to_candidate(item: dict[str, Any], rank: int) -> Optional[MatchCandidate]:
    title = (item.get("trackName") or "").strip()
    artist = (item.get("artistName") or "").strip()
    if not title or not artist:
        return None
    date = (item.get("releaseDate") or "")[:4]
    track = item.get("trackNumber")
    duration_ms = item.get("trackTimeMillis") or 0
    return MatchCandidate(
        source="iTunes",
        title=title,
        artist=artist,
        album=(item.get("collectionName") or "").strip(),
        albumartist=artist,
        date=date,
        genre=(item.get("primaryGenreName") or "").strip(),
        track=str(track) if track else "",
        disc=str(item.get("discNumber") or "") if item.get("discNumber") else "",
        cover_url=_upgrade_artwork(item.get("artworkUrl100") or ""),
        recording_id=str(item.get("trackId") or ""),
        # iTunes 无相关度分，用返回顺序近似：越靠前越相关
        raw_score=max(0.0, 0.9 - rank * 0.06),
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
    # 不指定 country（默认美国商店）：实测 CN 商店对中文曲目几乎无结果，
    # 而 US 商店反而有大量中文条目（含翻唱），作为兜底源更实用
    params = {"term": term, "media": "music", "entity": "song", "limit": str(limit)}
    try:
        payload = client.get_json(BASE, params=params, min_interval=MIN_INTERVAL, attempts=2)
    except NetworkError as exc:
        log.info("iTunes 检索失败：%s", exc)
        return []
    results = (payload or {}).get("results") or []
    candidates: list[MatchCandidate] = []
    for rank, item in enumerate(results):
        candidate = _to_candidate(item, rank)
        if candidate:
            candidates.append(candidate)
    return candidates


def cover(client: HttpClient, url: str, *, max_bytes: int = 5 * 1024 * 1024) -> tuple[Optional[bytes], str]:
    """下载封面。"""
    if not url:
        return None, ""
    try:
        data = client.get_bytes(url, max_size=max_bytes, attempts=1, timeout=5.0)
    except NetworkError:
        return None, ""
    if not data or len(data) <= 128:
        return None, ""
    from .http import sniff_image_mime

    return data, sniff_image_mime(data)
