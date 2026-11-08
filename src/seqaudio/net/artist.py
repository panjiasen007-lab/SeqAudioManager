"""歌手照片获取源（免费公开 API）：Deezer → 网易云。

用途
----
「每个文件夹至少一张 cover」功能的兜底：当某个文件夹既没有内嵌封面、也匹配
不到专辑封面时，用歌手照片补一张，保证封面位不空。

Deezer 为免费公开 API（无需鉴权，图片 1000×1000）；网易云为社区逆向接口，
仅作兜底。照片版权归原权利人，软件仅下载到用户本地文件夹、不存储不分发。
"""

from __future__ import annotations

import logging
from typing import Optional

from .http import HttpClient, NetworkError, sniff_image_mime

log = logging.getLogger(__name__)

DEEZER_ARTIST = "https://api.deezer.com/search/artist"
NETMUSIC_SEARCH = "https://music.163.com/api/search/get/web"
NETMUSIC_ARTIST_V1 = "https://music.163.com/api/v1/artist/detail/"
NETMUSIC_ARTIST = "https://music.163.com/api/artist/"
MIN_INTERVAL = 0.4


def _download(client: HttpClient, url: str) -> tuple[Optional[bytes], str]:
    """下载图片字节并识别 mime。封面/照片属可选数据，单次尝试、短超时。"""
    if not url:
        return None, ""
    try:
        data = client.get_bytes(url, max_size=5 * 1024 * 1024, attempts=1, timeout=6.0)
    except NetworkError:
        return None, ""
    if not data or len(data) <= 128:
        return None, ""
    return data, sniff_image_mime(data)


def deezer_artist_photo(client: HttpClient, artist: str) -> tuple[Optional[bytes], str]:
    if not artist.strip():
        return None, ""
    try:
        payload = client.get_json(
            DEEZER_ARTIST,
            params={"q": artist, "limit": "1"},
            min_interval=MIN_INTERVAL,
            attempts=2,
        )
    except NetworkError as exc:
        log.info("Deezer 歌手检索失败：%s", exc)
        return None, ""
    data = (payload or {}).get("data") or []
    if not isinstance(data, list) or not data:
        return None, ""
    first = data[0]
    if not isinstance(first, dict):
        return None, ""
    # 优先高清图，逐级降级
    for key in ("picture_xl", "picture_big", "picture_medium", "picture"):
        url = (first.get(key) or "").strip()
        img, mime = _download(client, url)
        if img:
            return img, mime
    return None, ""


def netmusic_artist_photo(client: HttpClient, artist: str) -> tuple[Optional[bytes], str]:
    if not artist.strip():
        return None, ""
    try:
        payload = client.get_json(
            NETMUSIC_SEARCH,
            params={"s": artist, "type": "100", "limit": "1", "offset": "0"},
            min_interval=MIN_INTERVAL,
            attempts=2,
        )
    except NetworkError as exc:
        log.info("网易云歌手检索失败：%s", exc)
        return None, ""
    artists = ((payload or {}).get("result") or {}).get("artists") or []
    if not isinstance(artists, list) or not artists:
        return None, ""
    artist_id = artists[0].get("id")
    if not artist_id:
        return None, ""

    # 两个详情接口都试：v1 返回 avatar/picUrl，旧接口返回 img1v1Url
    for base in (NETMUSIC_ARTIST_V1, NETMUSIC_ARTIST):
        try:
            detail = client.get_json(
                f"{base}{artist_id}", min_interval=MIN_INTERVAL, attempts=2
            )
        except NetworkError as exc:
            log.debug("网易云歌手详情失败 %s：%s", base, exc)
            continue
        info = (detail or {}).get("artist") or {}
        if not isinstance(info, dict):
            info = {}
        for key in ("avatar", "picUrl", "img1v1Url", "cover"):
            url = (info.get(key) or "").strip()
            if url.startswith("http://"):
                url = "https://" + url[7:]
            img, mime = _download(client, url)
            if img:
                return img, mime
    return None, ""


def fetch_artist_photo(
    client: HttpClient,
    artist: str,
    *,
    use_deezer: bool = True,
    use_netmusic: bool = True,
) -> tuple[Optional[bytes], str, str]:
    """取歌手照片，返回 (图片字节, mime, 来源)。失败返回 (None, "", "")。

    顺序：**网易云 → Deezer**。中文歌手在网易云的图是官方宣传照，且按中文名
    一定能搜到；Deezer 对华语艺人经常只有一个灰色占位图或干脆没有。
    """
    if use_netmusic:
        img, mime = netmusic_artist_photo(client, artist)
        if img:
            return img, mime, "网易云"
    if use_deezer:
        img, mime = deezer_artist_photo(client, artist)
        if img:
            return img, mime, "Deezer"
    return None, "", ""
