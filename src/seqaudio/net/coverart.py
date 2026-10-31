"""Cover Art Archive 封面源（开源封面库，需求 3.2.1）。

优先取 250px 缩略图（体积小、写入标签更安全），失败再回退 500px 原图。
**绝不会**把超过 12MB 的图片塞进标签（老播放器会直接卡死）。
"""

from __future__ import annotations

import logging
from typing import Optional

from .http import CircuitBreaker, HttpClient, NetworkError, sniff_image_mime

log = logging.getLogger(__name__)

BASE = "https://coverartarchive.org"
#: CAA 在部分网络环境下完全不可达，连续失败即熔断，不再逐张等待
BREAKER = CircuitBreaker("CoverArtArchive", threshold=2, cooldown=300.0)

#: 只尝试两种尺寸，且每种 1 次、5 秒超时 —— 封面属于可选数据，不能拖慢整批
SIZES = ("front-500", "front-250")


def cover_for_release(
    client: HttpClient,
    release_id: str,
    *,
    max_bytes: int = 5 * 1024 * 1024,
) -> tuple[Optional[bytes], str]:
    """返回 (图片字节, mime)。找不到时返回 (None, "")。"""
    if not release_id or not BREAKER.allow():
        return None, ""
    for size in SIZES:
        url = f"{BASE}/release/{release_id}/{size}"
        try:
            data = client.get_bytes(url, max_size=max_bytes, attempts=1, timeout=3.5)
        except NetworkError as exc:
            # 超时/连不上同样要记失败 —— 否则熔断器永远不会打开，
            # 每首曲目都得白等两个 5 秒超时，一千首就是三小时
            log.debug("封面获取失败 %s：%s", url, exc)
            BREAKER.failure()
            if not BREAKER.allow():
                log.info("封面源连续不可达，本次任务跳过封面下载")
                break
            continue
        if data and len(data) > 128:
            BREAKER.success()
            return data, sniff_image_mime(data)
        BREAKER.failure()
    return None, ""


def cover_for_release_group(client: HttpClient, rg_id: str) -> tuple[Optional[bytes], str]:
    if not rg_id:
        return None, ""
    url = f"{BASE}/release-group/{rg_id}/front-500"
    try:
        data = client.get_bytes(url)
    except NetworkError:
        return None, ""
    if data:
        return data, sniff_image_mime(data)
    return None, ""


def has_cover(client: HttpClient, release_id: str) -> bool:
    url = f"{BASE}/release/{release_id}/front-250"
    try:
        data = client.get_bytes(url, max_size=2 * 1024 * 1024)
        return bool(data)
    except NetworkError:
        return False
