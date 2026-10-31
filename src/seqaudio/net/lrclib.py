"""LRCLIB 歌词源（开源免费 LRC API，主歌词源，需求 3.2.1 / 3.3）。

接口
----
* ``/api/get``    精确匹配（artist + track + album + duration），返回同步歌词；
* ``/api/search`` 模糊检索，用于精确匹配失败时回退。

LRCLIB 不要求鉴权，但明确禁止高频爬取 —— 走与其它源一致的限流与缓存策略。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .http import CircuitBreaker, HttpClient, NetworkError

log = logging.getLogger(__name__)

BASE = "https://lrclib.net/api"
MIN_INTERVAL = 1.2

#: LRCLIB 高峰期常返回 503（临时过载，通常几十秒自愈）。
#: 阈值放得比较宽：过早熔断会让整批曲目一首歌词都拿不到，
#: 真正需要"快速放弃"的是封面这种可选数据。
BREAKER = CircuitBreaker("LRCLIB", threshold=6, cooldown=90.0)


@dataclass
class LyricsResult:
    synced: str = ""       # 带时间轴
    plain: str = ""
    source: str = "LRCLIB"
    matched_title: str = ""
    matched_artist: str = ""
    duration: float = 0.0

    @property
    def text(self) -> str:
        return self.synced or self.plain

    @property
    def is_synced(self) -> bool:
        return bool(self.synced)


def _to_result(payload: dict[str, Any]) -> Optional[LyricsResult]:
    if not payload:
        return None
    synced = (payload.get("syncedLyrics") or "").strip()
    plain = (payload.get("plainLyrics") or "").strip()
    if not synced and not plain:
        return None
    return LyricsResult(
        synced=synced,
        plain=plain,
        matched_title=payload.get("trackName") or "",
        matched_artist=payload.get("artistName") or "",
        duration=float(payload.get("duration") or 0),
    )


def fetch_lyrics(
    client: HttpClient,
    title: str,
    artist: str = "",
    album: str = "",
    duration: Optional[float] = None,
) -> Optional[LyricsResult]:
    """先精确匹配，再按「歌名+歌手」模糊检索（需求 3.3）。"""
    if not title.strip():
        return None
    if not BREAKER.allow():
        log.debug("LRCLIB 熔断中，跳过查询")
        return None

    params: dict[str, Any] = {"track_name": title}
    if artist:
        params["artist_name"] = artist
    if album:
        params["album_name"] = album
    if duration:
        params["duration"] = int(round(duration))

    try:
        payload = client.get_json(f"{BASE}/get", params=params, min_interval=MIN_INTERVAL, attempts=2)
        result = _to_result(payload if isinstance(payload, dict) else {})
        if result:
            BREAKER.success()
            return _release_duration_check(result, duration)
        BREAKER.success()
    except NetworkError as exc:
        log.debug("LRCLIB 精确匹配失败：%s", exc)
        BREAKER.failure()

    return search_lyrics(client, title, artist, duration)


def search_lyrics(
    client: HttpClient,
    title: str,
    artist: str = "",
    duration: Optional[float] = None,
    *,
    limit: int = 5,
) -> Optional[LyricsResult]:
    query = f"{title} {artist}".strip()
    if not query or not BREAKER.allow():
        return None
    try:
        payload = client.get_json(
            f"{BASE}/search", params={"q": query}, min_interval=MIN_INTERVAL, attempts=2
        )
    except NetworkError as exc:
        log.info("LRCLIB 搜索失败：%s", exc)
        BREAKER.failure()
        return None

    items = payload if isinstance(payload, list) else []
    results = [r for r in (_to_result(i) for i in items[:limit]) if r]
    if not results:
        return None

    def rank(item: LyricsResult) -> tuple[int, int, float]:
        synced = 1 if item.is_synced else 0
        title_hit = 1 if item.matched_title.strip().lower() == title.strip().lower() else 0
        delta = abs(item.duration - duration) if (duration and item.duration) else 99
        return synced, title_hit, -delta

    best = max(results, key=rank)
    return _release_duration_check(best, duration)


def _release_duration_check(result: LyricsResult, expected: Optional[float]) -> LyricsResult:
    """时长偏差过大时在来源标记中提示，交由上层决定是否采信。"""
    if expected and result.duration:
        delta = abs(result.duration - expected)
        if delta > 8:
            result.source = f"LRCLIB(时长差 {delta:.0f}s)"
    return result
