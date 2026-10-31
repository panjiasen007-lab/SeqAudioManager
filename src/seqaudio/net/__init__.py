"""联网数据源层：MusicBrainz / Cover Art Archive / LRCLIB（全部开源平台）。"""

from .http import APP_UA, DiskCache, HttpClient, NetworkError, RetryPolicy
from .lrclib import LyricsResult, fetch_lyrics, search_lyrics

__all__ = [
    "APP_UA",
    "DiskCache",
    "HttpClient",
    "NetworkError",
    "RetryPolicy",
    "LyricsResult",
    "fetch_lyrics",
    "search_lyrics",
]
