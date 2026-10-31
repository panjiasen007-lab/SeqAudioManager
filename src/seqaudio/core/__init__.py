"""音频处理核心层（不依赖 Qt，可独立测试）。"""

from .model import (
    AudioInfo,
    ConflictMode,
    CueSheet,
    CueTrack,
    LyricsMode,
    Tags,
    Track,
    TrackStatus,
    SUPPORTED_EXTS,
    LOSSLESS_EXTS,
    ENCRYPTED_EXTS,
)

__all__ = [
    "AudioInfo",
    "ConflictMode",
    "CueSheet",
    "CueTrack",
    "LyricsMode",
    "Tags",
    "Track",
    "TrackStatus",
    "SUPPORTED_EXTS",
    "LOSSLESS_EXTS",
    "ENCRYPTED_EXTS",
]
