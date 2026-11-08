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
from ..core.text import similarity_strict
from . import artist as artist_mod
from . import coverart, itunes, lrclib, musicbrainz, netmusic, qqmusic
from .http import HttpClient, sniff_image_mime

log = logging.getLogger(__name__)


def _first_nonempty(
    providers: list[tuple[str, Callable[[], list[MatchCandidate]]]],
    accept: Optional[Callable[[MatchCandidate], bool]] = None,
    keep_as_fallback: Optional[Callable[[MatchCandidate], bool]] = None,
) -> list[MatchCandidate]:
    """按顺序尝试，返回第一个「有结果且能通过校验」的源。

    ``accept`` 用来挡掉明显搜错的源：数据源对冷门/中文曲目的召回质量参差，
    经常把完全无关的热门歌排在第一（搜「终有一天感动你」返回
    「Right Here Waiting」）。不加校验就返回，等于把错的结果当成命中。

    ``keep_as_fallback`` 决定「全源都不合格」时留什么当备胎：只保留歌名至少
    对得上的那几条，不能整批原样带回 —— 那样等于校验白做。
    """
    fallback: list[MatchCandidate] = []
    for name, fetch in providers:
        try:
            candidates = fetch()
        except Exception as exc:  # noqa: BLE001 - 单个源抛错不影响后续源
            log.debug("数据源 %s 异常：%s", name, exc)
            continue
        if not candidates:
            continue
        if accept is None:
            log.debug("元数据命中 %s（%d 条）", name, len(candidates))
            return candidates
        kept = [c for c in candidates if accept(c)]
        if kept:
            log.debug("元数据命中 %s（%d/%d 条通过校验）", name, len(kept), len(candidates))
            return kept
        log.debug("数据源 %s 的 %d 条候选都与查询不符，换下一个", name, len(candidates))
        if not fallback and keep_as_fallback is not None:
            similar = [c for c in candidates if keep_as_fallback(c)]
            if similar:
                fallback = similar
    return fallback


def search_metadata(
    client: HttpClient,
    title: str,
    artist: str = "",
    album: str = "",
    *,
    duration: Optional[float] = None,
    use_mb: bool = True,
    use_itunes: bool = True,
    use_netmusic: bool = True,
    use_qqmusic: bool = True,
) -> list[MatchCandidate]:
    """按优先级多源检索：**网易云 → QQ 音乐 → MusicBrainz → iTunes**。

    为什么国内源排前面：中文曲库里它们的覆盖率最高，专辑名、曲序、封面都是
    中文原版信息；MusicBrainz 对华语流行的收录经常只有英文拼写或干脆没有，
    iTunes 的专辑名则常被本地化成「xxx - Single」。以前把 MB 排第一，结果是
    中文专辑大量落到 iTunes 的英文条目上，封面和曲序都跟着偏。

    每个源的结果先过一遍「像不像这首歌」的校验（标题相似度或时长接近），
    全都不像就换下一个源；四个源都不像时退回第一个源的结果当备胎，
    免得冷门曲目直接变成「未匹配」。

    MB / iTunes 仍然保留在末尾兜底：冷门与海外曲目只有它们有。
    """

    def accept(cand: MatchCandidate) -> bool:
        # 三道关，按可信度从高到低 ——
        # 1) 标题像 + 歌手像：最可靠；
        # 2) 标题像但歌手不同：只在时长吻合时放行（合唱/翻唱常写成不同艺名）；
        # 3) 标题不像：只在时长几乎一致时放行（改过名的版本）。
        # 用 strict 版本：评分那边允许「同长度中文」的宽容兜底，但选源不能 ——
        # 否则「旧朋友」会把「给朋友」当成命中，正确的源反而轮不到。
        duration_ok = bool(
            duration and cand.duration and abs(cand.duration - duration) <= 3.0
        )
        title_ok = similarity_strict(title, cand.title) >= 0.75
        if not title_ok:
            return duration_ok
        if not artist:
            # 没歌手可比（很多文件本来就没填），只能信标题
            return True
        if similarity_strict(artist, cand.artist) >= 0.5:
            return True
        return duration_ok

    providers: list[tuple[str, Callable[[], list[MatchCandidate]]]] = []
    if use_netmusic:
        providers.append(("网易云", lambda: netmusic.search(client, title, artist)))
    if use_qqmusic:
        providers.append(("QQ音乐", lambda: qqmusic.search(client, title, artist)))
    if use_mb:
        providers.append(("MusicBrainz", lambda: musicbrainz.search_recordings(client, title, artist, album)))
    if use_itunes:
        providers.append(("iTunes", lambda: itunes.search(client, title, artist)))

    def keep_as_fallback(cand: MatchCandidate) -> bool:
        # 备胎只留「歌名至少对得上」的：整批原样带回会把《偷心》《吻别》
        # 这种搜错的热门歌也一起塞给评分层，等于没校验。
        return similarity_strict(title, cand.title) >= 0.5

    candidates = _first_nonempty(providers, accept, keep_as_fallback)
    if candidates:
        return candidates
    # 全空时放宽：歌手可能写错，只按标题再试一次（仍然遵循优先级顺序）
    if use_netmusic:
        candidates = netmusic.search(client, title, "")
    if not candidates and use_qqmusic:
        candidates = qqmusic.search(client, title, "")
    if not candidates and use_mb:
        candidates = musicbrainz.search_recordings(client, title, "", album)
    if not candidates and use_itunes:
        candidates = itunes.search(client, title, "")
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
    use_qqmusic: bool = True,
) -> Optional[lrclib.LyricsResult]:
    """歌词：**网易云 → QQ 音乐 → LRCLIB**。

    国内源排前面是因为中文歌的歌词在它们那里最全，且带逐行时间轴；LRCLIB 是
    社区库，华语老歌经常没有条目。``duration`` 会传给国内源，让它们按时长挑
    候选（避免贴上同名 LIVE / 翻唱 / 伴奏版的歌词）。
    """
    if use_netmusic:
        result = netmusic.lyrics(client, title, artist, duration=duration or 0.0)
        if result:
            return result
    if use_qqmusic:
        result = qqmusic.lyrics(client, title, artist, duration=duration or 0.0)
        if result:
            return result
    if use_lrclib:
        result = lrclib.fetch_lyrics(client, title, artist, album, duration)
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
    """按候选已有信息抓封面。

    顺序随来源走：**国内源（网易云 / QQ）优先用候选自带的 cover_url** ——
    那是原版专辑图，尺寸和清晰度都比 CAA 的扫描件稳定；其它来源（MusicBrainz
    等）先试 CAA（release_id 精确对应那张发行版），再退回 cover_url。
    """
    domestic = candidate.source in ("网易云", "QQ音乐")
    if domestic and use_remote:
        url = candidate.cover_url
        if not url and candidate.source == "网易云":
            url = netmusic.song_cover_url(client, candidate)
        if url:
            data, mime = itunes.cover(client, url)
            if data:
                return data, mime
    if use_coverart and candidate.release_id:
        data, mime = coverart.cover_for_release(client, candidate.release_id)
        if data:
            return data, mime
    if use_remote and candidate.cover_url:
        data, mime = itunes.cover(client, candidate.cover_url)
        if data:
            return data, mime
    return None, ""


def _download_remote(client: HttpClient, url: str) -> tuple[Optional[bytes], str]:
    if not url:
        return None, ""
    try:
        data = client.get_bytes(url, max_size=5 * 1024 * 1024, attempts=1, timeout=6.0)
    except Exception:  # noqa: BLE001
        return None, ""
    if not data or len(data) <= 128:
        return None, ""
    return data, sniff_image_mime(data)


def _cover_from_search(
    search_fn: Callable[..., list[MatchCandidate]],
    client: HttpClient,
    query: str,
    artist: str,
    resolve=None,
) -> tuple[Optional[bytes], str]:
    """用某个源的搜索结果里第一条带图的候选下载封面。

    ``resolve`` 用于「候选没带图片地址」的来源（网易云搜索接口只给 picId），
    传进来一个 ``(client, candidate) -> url`` 的回调去补一次详情。
    """
    try:
        candidates = search_fn(client, query, artist)
    except Exception as exc:  # noqa: BLE001 - 单个源失败不影响后续
        log.debug("封面检索失败（%s）：%s", query, exc)
        return None, ""
    for cand in candidates[:3]:  # 前三条够了，避免逐条补详情拖慢
        url = cand.cover_url
        if not url and resolve is not None:
            try:
                url = resolve(client, cand)
            except Exception as exc:  # noqa: BLE001
                log.debug("补封面地址失败：%s", exc)
                url = ""
        if not url:
            continue
        img, mime = _download_remote(client, url)
        if img:
            return img, mime
    return None, ""


def fetch_album_cover(
    client: HttpClient,
    title: str,
    artist: str = "",
    album: str = "",
    *,
    use_itunes: bool = True,
    use_netmusic: bool = True,
    use_qqmusic: bool = True,
) -> tuple[Optional[bytes], str, str]:
    """按「专辑 + 歌手」搜一张封面，返回 (字节, mime, 来源)。

    这是「每文件夹一张 cover」的二级兜底：曲目本身没匹配到封面时，
    用专辑名再搜一次。用专辑名优先（比歌名更稳），搜不到再退回歌名。

    顺序：**网易云 → QQ 音乐 → iTunes**。中文专辑在两家国内源的图是原版封面且
    尺寸稳定；iTunes 对华语老碟常常只有低分辨率或干脆没有条目。
    """
    query_title = (album or title).strip()
    if not query_title:
        return None, "", ""

    providers: list[tuple[str, bool, Callable[..., list[MatchCandidate]], object]] = [
        # 网易云的搜索接口只返回 picId，要额外查一次详情才能拿到图片地址
        ("网易云", use_netmusic, netmusic.search, netmusic.song_cover_url),
        ("QQ音乐", use_qqmusic, qqmusic.search, None),
        ("iTunes", use_itunes, itunes.search, None),
    ]
    for name, enabled, search_fn, resolve in providers:
        if not enabled:
            continue
        img, mime = _cover_from_search(search_fn, client, query_title, artist, resolve)
        if img:
            return img, mime, name
        # 专辑名搜不到再按歌名搜一次
        if album and album != title:
            img, mime = _cover_from_search(search_fn, client, title, artist, resolve)
            if img:
                return img, mime, name

    return None, "", ""


def fetch_artist_photo(
    client: HttpClient,
    artist: str = "",
    *,
    use_deezer: bool = True,
    use_netmusic: bool = True,
) -> tuple[Optional[bytes], str, str]:
    """歌手照片兜底：Deezer → 网易云。返回 (字节, mime, 来源)。"""
    return artist_mod.fetch_artist_photo(
        client, artist, use_deezer=use_deezer, use_netmusic=use_netmusic
    )
