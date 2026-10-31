"""多源聚合与自动切换（需求 3.2.4 / 3.2.7）。

元数据 / 歌词 / 封面都走这里统一调度，而不是散落在 Matcher 里：

* **顺序即优先级**：默认 元数据 MusicBrainz → iTunes → 网易云；
  歌词 LRCLIB → 网易云；封面 Cover Art Archive → iTunes/网易云。
* **自动切换**：某一源失败（网络错误或返回空）就静默换下一个，
  任何一个成功即返回；全失败返回空。任何单源故障都不会拖慢整批任务。
* 每个源内部已有自己的限流与熔断，这里只负责"接力"，不重复造轮子。

网易云为社区逆向的公开接口，仅作兜底；歌词版权归原权利人，软件不存储不分发。
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from ..core.model import MatchCandidate
from . import coverart, itunes, lrclib, musicbrainz, netmusic
from .http import HttpClient

log = logging.getLogger(__name__)


def _first_nonempty(providers: list[tuple[str, Callable[[], list[MatchCandidate]]]]) -> list[MatchCandidate]:
    """按顺序尝试，返回第一个非空的结果。"""
    for name, fetch in providers:
        try:
            candidates = fetch()
        except Exception as exc:  # noqa: BLE001 - 单个源抛错不影响后续源
            log.debug("数据源 %s 异常：%s", name, exc)
            continue
        if candidates:
            log.debug("元数据命中 %s（%d 条）", name, len(candidates))
            return candidates
    return []


def search_metadata(
    client: HttpClient,
    title: str,
    artist: str = "",
    album: str = "",
    *,
    use_mb: bool = True,
    use_itunes: bool = True,
    use_netmusic: bool = True,
) -> list[MatchCandidate]:
    providers: list[tuple[str, Callable[[], list[MatchCandidate]]]] = []
    if use_mb:
        providers.append(("MusicBrainz", lambda: musicbrainz.search_recordings(client, title, artist, album)))
    if use_itunes:
        providers.append(("iTunes", lambda: itunes.search(client, title, artist)))
    if use_netmusic:
        providers.append(("网易云", lambda: netmusic.search(client, title, artist)))

    candidates = _first_nonempty(providers)
    if candidates:
        return candidates
    # 全空时放宽：歌手可能写错，只按标题再试一次（仍然遵循优先级顺序）
    if use_mb:
        candidates = musicbrainz.search_recordings(client, title, "", album)
    if not candidates and use_itunes:
        candidates = itunes.search(client, title, "")
    if not candidates and use_netmusic:
        candidates = netmusic.search(client, title, "")
    return candidates


def fetch_lyrics(
    client: HttpClient,
    title: str,
    artist: str = "",
    album: str = "",
    duration: Optional[float] = None,
    *,
    use_lrclib: bool = True,
    use_netmusic: bool = True,
) -> Optional[lrclib.LyricsResult]:
    if use_lrclib:
        result = lrclib.fetch_lyrics(client, title, artist, album, duration)
        if result:
            return result
    if use_netmusic:
        result = netmusic.lyrics(client, title, artist)
        if result:
            return result
    return None


def fetch_cover(
    client: HttpClient,
    candidate: MatchCandidate,
    *,
    use_coverart: bool = True,
    use_remote: bool = True,
) -> tuple[Optional[bytes], str]:
    """按候选已有信息抓封面：CAA（release_id）→ 远程 cover_url。"""
    if use_coverart and candidate.release_id:
        data, mime = coverart.cover_for_release(client, candidate.release_id)
        if data:
            return data, mime
    if use_remote and candidate.cover_url:
        data, mime = itunes.cover(client, candidate.cover_url)
        if data:
            return data, mime
    return None, ""
