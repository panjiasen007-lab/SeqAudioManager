"""配置与预设方案（需求 3.2.7 / 3.6.13 / 4.5）。

配置文件位置遵循 ``paths.data_dir()``：绿色运行时落在程序目录 ``data/``，
不可写时回落到 ``%APPDATA%/SeqAudioManager``。
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

from . import paths
from .model import ConflictMode, LyricsMode
from .rename import DEFAULT_TEMPLATE
from .watermark import DEFAULT_SEGMENT_PATTERNS, DEFAULT_STRONG_PATTERNS


@dataclass
class Settings:
    # ---- 免责声明 ----
    accepted_disclaimer: str = ""          # 记录接受版本与时间
    disclaimer_version: str = "1.0"

    # ---- 标签覆盖策略 ----
    conflict_mode: str = ConflictMode.FILL_EMPTY_ONLY.value
    fill_album_from_cue: bool = True

    # ---- 网络与数据源 ----
    online_enabled: bool = True
    use_musicbrainz: bool = True
    use_coverart: bool = True
    use_lrclib: bool = True
    use_itunes: bool = True          # iTunes Search：元数据 + 封面（Apple 公开接口）
    use_netmusic: bool = True        # 网易云：元数据 + 歌词 + 封面（社区接口，兜底）
    use_qqmusic: bool = True         # QQ 音乐：元数据 + 歌词 + 封面（社区接口，兜底）
    use_local_lyrics: bool = True
    acoustid_key: str = ""                 # 音频指纹（P1，可留空）
    http_proxy: str = ""
    concurrency: int = 4
    request_timeout: float = 12.0
    min_request_interval: float = 1.1      # MusicBrainz 要求 1 req/s
    cache_ttl_days: int = 30
    match_threshold: float = 0.62          # 低于该置信度转人工确认

    # ---- 歌词 ----
    lyrics_mode: str = LyricsMode.SIDECAR.value
    clean_lyrics: bool = True
    prefer_synced: bool = True

    # ---- 写入与备份 ----
    backup_enabled: bool = True
    backup_mirror: bool = True
    backup_root: str = ""
    id3_version: int = 4
    compat_mode: bool = False              # 老播放器兼容：ID3v2.3 + UTF-16
    clear_cover_when_no_match: bool = False

    # ---- 水印 ----
    watermark_enabled: bool = True
    clean_title_watermark: bool = True
    clean_albumartist_watermark: bool = True
    clear_comment: bool = True             # 默认清空注释字段全部内容（抓轨/下载工具塞的垃圾）
    strong_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_STRONG_PATTERNS))
    segment_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_SEGMENT_PATTERNS))
    custom_words: list[str] = field(default_factory=list)
    replace_watermarked_cover: bool = True
    strip_track_numbers: bool = True       # 清洗时去除标题开头的曲目序号

    # ---- 重命名 ----
    rename_enabled: bool = False
    rename_template: str = DEFAULT_TEMPLATE

    # ---- 扫描与过滤 ----
    recursive_scan: bool = True
    min_duration_sec: float = 30.0         # 过滤短视频 BGM / 广告音频
    skip_encrypted: bool = True

    # ---- 界面 ----
    theme: str = "深空"                     # 主题名（见 ui/theme.THEMES）
    language: str = "zh_CN"                 # 界面语言（见 ui/i18n.LOCALES）
    space_background: bool = True          # 星空动态背景
    cleanup_include_suspect: bool = True   # 清理时是否包含「疑似」项

    # ---- 文件夹封面 ----
    ensure_folder_cover: bool = True       # 处理完成后每个文件夹至少一张 cover（专辑封面→歌手照片）

    # ---- 书写体统一 ----
    unify_artist_script: bool = True       # 同目录艺术家名字统一为简体（简繁不混搭）

    # ---- 其他 ----
    offline_mode: bool = False
    last_folder: str = ""

    # ------------------------------------------------------------ 存取

    def backup_dir(self) -> Path:
        if self.backup_root.strip():
            return Path(self.backup_root)
        return paths.default_backup_dir()

    def conflict(self) -> ConflictMode:
        try:
            return ConflictMode(self.conflict_mode)
        except ValueError:
            return ConflictMode.FILL_EMPTY_ONLY

    def lyrics_mode_enum(self) -> LyricsMode:
        try:
            return LyricsMode(self.lyrics_mode)
        except ValueError:
            return LyricsMode.SIDECAR

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Settings":
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        instance = cls()
        for key, value in clean.items():
            setattr(instance, key, value)
        return instance

    def save(self, path: Optional[Path] = None) -> None:
        target = path or paths.config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Settings":
        target = path or paths.config_path()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except (OSError, json.JSONDecodeError):
            return cls()


# ---------------------------------------------------------------- 预设方案


class PresetStore:
    """整套配置的命名预设，一键切换不同曲库的处理方案。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = path or paths.presets_path()
        self._data: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return raw if isinstance(raw, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _flush(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def names(self) -> list[str]:
        return sorted(self._data)

    def save(self, name: str, settings: Settings) -> None:
        self._data[name] = settings.to_dict()
        self._flush()

    def load(self, name: str) -> Optional[Settings]:
        data = self._data.get(name)
        return Settings.from_dict(data) if data else None

    def delete(self, name: str) -> bool:
        if name in self._data:
            del self._data[name]
            self._flush()
            return True
        return False
