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

#: 搜索接口。**不要用 ``/api/search/get/web``** —— 境外 IP 访问那个端点时，网易云
#: 会返回 ``{"code":200,"abroad":true,"result":"<十六进制密文>"}``，`result` 不是
#: 对象而是加密字符串，解析必然失败（表现为「网易云永远匹配不到」）。
#: 去掉 ``/web`` 的旧端点不做这层处理，境内境外都返回明文 JSON，
#: 而且对中文曲名的召回还更准（实测搜「旧朋友 张学友」，第一条就是原曲）。
SEARCH_BASE = "https://music.163.com/api/search/get"
LYRIC_BASE = "https://music.163.com/api/song/lyric"
#: 单曲详情：搜索结果里 ``album`` 只有 ``picId`` 没有 ``picUrl``，而拼
#: ``p1.music.126.net/{picId}/{picId}.jpg`` 是错的（真实地址里还有一段
#: 加密串），必须走详情接口拿现成的 URL。
DETAIL_BASE = "https://music.163.com/api/song/detail"
MIN_INTERVAL = 0.5


def song_cover_url(client: HttpClient, candidate: MatchCandidate) -> str:
    """给网易云候选补封面地址（搜索接口不返回 picUrl）。

    只在候选自带 ``cover_url`` 为空时调用，一首歌一次额外请求。
    """
    song_id = (candidate.recording_id or "").strip()
    if not song_id:
        return ""
    try:
        payload = client.get_json(
            DETAIL_BASE,
            params={"ids": f"[{song_id}]"},
            min_interval=MIN_INTERVAL,
            attempts=2,
        )
    except NetworkError as exc:
        log.info("网易云封面详情获取失败：%s", exc)
        return ""
    if not isinstance(payload, dict):
        return ""
    songs = payload.get("songs") or []
    if not isinstance(songs, list) or not songs:
        return ""
    album = songs[0].get("album") or {}
    if not isinstance(album, dict):
        return ""
    url = (album.get("picUrl") or "").strip()
    if url.startswith("http://"):
        url = "https://" + url[7:]
    # 详情接口给的是原图（实测有 3MB+ 的 PNG、下载要 30 秒）。封面最终要嵌进每首歌，
    # 300×300 在界面里已经够清晰（约 180KB）；param 是网易云的图片缩放参数。
    # 注意它只缩放不转格式 —— 原图是 PNG 的话回来还是 PNG。
    if url and "?" not in url:
        url += "?param=300y300"
    return url


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

    # 接口偶尔直接返回一段字符串（限流页 / 上游变更），不是 JSON 对象。
    # 以前这里直接 .get()，会抛 AttributeError 并冒到上层 —— 以前网易云排第二，
    # 异常被多源接力的 try/except 吞掉所以没暴露；现在排第一，必须自己兜住。
    if not isinstance(payload, dict):
        log.info("网易云检索返回非对象（%s），按无结果处理", type(payload).__name__)
        return []

    songs = payload.get("result") or {}
    if not isinstance(songs, dict):
        return []
    items = songs.get("songs") or []
    if not isinstance(items, list):
        return []
    candidates: list[MatchCandidate] = []
    for rank, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        candidate = _to_candidate(item, rank)
        if candidate:
            candidates.append(candidate)
    return candidates


def pick_best(candidates: list[MatchCandidate], duration: float = 0.0,
              title: str = "") -> Optional[MatchCandidate]:
    """从搜索候选里挑最可能是同一首的那条。

    光取第一条不够稳：翻唱、同名 LIVE、伴奏版经常排在第一。给了 ``title`` 就先
    按歌名筛（相似度 < 0.7 的直接排除 —— 搜「旧朋友」时列表里混着《等你等到我
    心痛》，按时长最近反而更容易选错）；给了 ``duration`` 再按时长差排序
    （差 3 秒内视为同一录音）。时长差超过 30 秒的直接排除 —— 那是另一首歌，
    宁可不取歌词也别贴错。
    """
    usable = [c for c in candidates if c.recording_id]
    if title:
        from ..core.text import similarity_strict

        by_title = [c for c in usable if similarity_strict(title, c.title) >= 0.7]
        if not by_title:
            # 一个都不像：这是另一首歌，宁可不取歌词也别贴错。
            # （早先这里是「过滤为空就退回原列表」，结果搜「旧朋友」时把
            # 《给朋友》的歌词贴了上去 —— 标题明明对不上，只是时长碰巧接近。）
            return None
        usable = by_title
    if not usable:
        return None
    if duration and duration > 0:
        def delta(c: MatchCandidate) -> float:
            return abs(c.duration - duration) if c.duration else 999.0

        near = [c for c in usable if delta(c) <= 30.0]
        if near:
            return min(near, key=delta)
        # 全都差得远：说明搜到的不是这首，交回调用方换下一个源
        return None
    return usable[0]


def lyrics(
    client: HttpClient,
    title: str,
    artist: str = "",
    song_id: str = "",
    duration: float = 0.0,
) -> Optional[LyricsResult]:
    """取 LRC 歌词。优先用已解析出的 song_id，否则先搜再取。"""
    picked: Optional[MatchCandidate] = None
    if not song_id:
        picked = pick_best(search(client, title, artist, limit=5), duration, title)
        if picked is None:
            return None
        song_id = picked.recording_id
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
        matched_title=picked.title if picked else title,
        matched_artist=picked.artist if picked else artist,
        duration=picked.duration if picked else 0.0,
    )
