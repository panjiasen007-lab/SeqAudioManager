"""零依赖 HTTP 客户端：超时、重试退避、按主机限流、代理、磁盘缓存。

为什么不用 requests / httpx
--------------------------
目标 API（MusicBrainz / Cover Art Archive / LRCLIB）都是简单 GET，
不需要 HTTP/2 与连接池复用；用标准库可以把依赖压到只剩 mutagen + PySide6，
单文件打包更小、DLL 冲突面更少，嵌入式 Python 环境下也更稳。
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import random
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from ..core import paths

log = logging.getLogger(__name__)

APP_UA = "SeqAudioManager/0.1 ( https://github.com/seqaudio )"
DEFAULT_TIMEOUT = 12.0

#: 网易云图片 CDN 域名后缀，命中则补 Referer 防盗链头（否则部分封面 403）
_NETMUSIC_IMAGE_HOST_SUFFIX = "music.126.net"


class NetworkError(Exception):
    """统一的网络异常，携带可读原因，供 UI 展示。"""


@dataclass
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 1.2
    max_delay: float = 12.0


class CircuitBreaker:
    """简易熔断器：某数据源连续失败 N 次后冷却一段时间，避免整批任务被拖死。

    需求 3.2.7 要求「数据源故障自动切换、限流退避」，熔断是其中最关键的一环：
    当 LRCLIB 大面积 503 或 Cover Art Archive 超时时，继续逐首重试毫无意义，
    只会把 1000 首的批量任务从几分钟拖到几小时。
    """

    def __init__(self, name: str, threshold: int = 3, cooldown: float = 180.0):
        self.name = name
        self.threshold = max(1, threshold)
        self.cooldown = cooldown
        self._failures = 0
        self._open_until = 0.0
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._open_until and time.monotonic() >= self._open_until:
                self._open_until = 0.0
                self._failures = 0
                return False
            return self._open_until > 0

    def allow(self) -> bool:
        if self.is_open:
            return False
        return True

    def success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.threshold and not self._open_until:
                self._open_until = time.monotonic() + self.cooldown
                log.warning(
                    "%s 连续失败 %d 次，熔断 %.0f 秒后重试", self.name, self._failures, self.cooldown
                )


class HostRateLimiter:
    """按主机最小间隔限流 + 全局并发闸门（防止被 API 封 IP）。"""

    def __init__(self, max_concurrency: int = 4):
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._semaphore = threading.Semaphore(max(1, max_concurrency))

    def wait(self, host: str, min_interval: float) -> None:
        if min_interval <= 0:
            return
        with self._lock:
            last = self._last.get(host, 0.0)
            now = time.monotonic()
            delay = last + min_interval - now
            if delay > 0:
                time.sleep(delay)
                now = time.monotonic()
            self._last[host] = now

    def gate(self):
        return self._semaphore


class DiskCache:
    """URL 级 JSON 缓存，减少重复请求（需求 3.2.7 检索结果本地缓存）。"""

    def __init__(self, directory: Optional[Path] = None, ttl_days: int = 30):
        self.directory = directory or (paths.cache_dir() / "http")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(1, ttl_days) * 86400

    def _key(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.directory / digest[:2] / f"{digest}.json"

    def get(self, url: str) -> Optional[Any]:
        path = self._key(url)
        try:
            if not path.exists():
                return None
            if time.time() - path.stat().st_mtime > self.ttl_seconds:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

    def set(self, url: str, payload: Any) -> None:
        path = self._key(url)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError:
            pass

    def clear(self) -> int:
        count = 0
        for item in self.directory.rglob("*.json"):
            try:
                item.unlink()
                count += 1
            except OSError:
                continue
        return count


class HttpClient:
    def __init__(
        self,
        *,
        proxy: str = "",
        timeout: float = DEFAULT_TIMEOUT,
        user_agent: str = APP_UA,
        concurrency: int = 4,
        retry: Optional[RetryPolicy] = None,
        cache: Optional[DiskCache] = None,
        offline: bool = False,
    ):
        self.proxy = proxy.strip()
        self.timeout = timeout
        self.user_agent = user_agent
        self.retry = retry or RetryPolicy()
        self.cache = cache if cache is not None else DiskCache()
        self.offline = offline
        self.limiter = HostRateLimiter(concurrency)
        self._build_opener()

    def _build_opener(self) -> None:
        if self.proxy:
            if "://" not in self.proxy:
                self.proxy = "http://" + self.proxy
            handler = urllib.request.ProxyHandler({"http": self.proxy, "https": self.proxy})
            self._opener = urllib.request.build_opener(handler)
        else:
            # 不传 handler：沿用系统代理设置（IE/系统级代理）
            self._opener = urllib.request.build_opener()

    # ------------------------------------------------------------ 请求

    def _request(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        accept: str = "application/json",
        attempts: Optional[int] = None,
        timeout: Optional[float] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> tuple[bytes, str]:
        if self.offline:
            raise NetworkError("离线模式已开启")
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
            url = f"{url}{'&' if '?' in url else '?'}{query}"

        last_error: Optional[Exception] = None
        total_attempts = max(1, attempts if attempts is not None else self.retry.attempts)
        use_timeout = timeout if timeout is not None else self.timeout

        base_headers = {
            "User-Agent": self.user_agent,
            "Accept": accept,
            "Accept-Encoding": "gzip",
        }
        if headers:
            base_headers.update(headers)

        with self.limiter.gate():
            for attempt in range(1, total_attempts + 1):
                request = urllib.request.Request(
                    url,
                    headers=base_headers,
                )
                try:
                    with self._opener.open(request, timeout=use_timeout) as response:
                        raw = response.read()
                        if response.headers.get("Content-Encoding") == "gzip":
                            raw = gzip.decompress(raw)
                        return raw, response.headers.get("Content-Type", "")
                except urllib.error.HTTPError as exc:
                    last_error = exc
                    if exc.code in (404, 400):
                        raise NetworkError(f"未找到（HTTP {exc.code}）") from exc
                    if exc.code == 429 or exc.code >= 500:
                        delay = min(
                            self.retry.max_delay,
                            self.retry.base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.4),
                        )
                        log.warning("HTTP %s %s，%.1fs 后重试（%d/%d）", exc.code, url, delay,
                                    attempt, total_attempts)
                        if attempt < total_attempts:
                            time.sleep(delay)
                        continue
                    raise NetworkError(f"HTTP {exc.code}：{url}") from exc
                except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
                    last_error = exc
                    delay = min(
                        self.retry.max_delay,
                        self.retry.base_delay * (2 ** (attempt - 1)),
                    )
                    log.warning("网络错误 %s，%.1fs 后重试（%d/%d）：%s", url, delay,
                                attempt, total_attempts, exc)
                    if attempt < total_attempts:
                        time.sleep(delay)
                    continue
        raise NetworkError(f"请求失败：{last_error}")

    # ------------------------------------------------------------ 便捷方法

    def get_json(
        self,
        url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        min_interval: float = 0.0,
        use_cache: bool = True,
        attempts: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        full = url
        if params:
            query = urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
            full = f"{url}?{query}"
        if use_cache:
            cached = self.cache.get(full)
            if cached is not None:
                return cached
        self.limiter.wait(urllib.parse.urlparse(full).netloc, min_interval)
        raw, _ = self._request(url, params=params, attempts=attempts, timeout=timeout)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise NetworkError(f"响应不是合法 JSON：{exc}") from exc
        if use_cache:
            self.cache.set(full, payload)
        return payload

    def get_bytes(
        self,
        url: str,
        *,
        min_interval: float = 0.0,
        max_size: int = 12 * 1024 * 1024,
        attempts: int = 1,
        timeout: float = 8.0,
    ) -> Optional[bytes]:
        """取二进制资源（封面图）。

        封面属于「有则更好」的可选数据，默认只尝试 1 次、8 秒超时 ——
        不能让一张取不到的图把整批任务拖死。
        """
        self.limiter.wait(urllib.parse.urlparse(url).netloc, min_interval)
        headers: Optional[dict[str, str]] = None
        if urllib.parse.urlparse(url).netloc.lower().endswith(_NETMUSIC_IMAGE_HOST_SUFFIX):
            headers = {"Referer": "https://music.163.com/"}
        raw, _ = self._request(
            url,
            accept="image/*",
            attempts=attempts,
            timeout=min(self.timeout, timeout),
            headers=headers,
        )
        if len(raw) > max_size:
            log.warning("封面体积超限已丢弃：%s bytes", len(raw))
            return None
        return raw


def sniff_image_mime(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    return "image/jpeg"
