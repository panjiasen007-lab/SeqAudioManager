"""核心数据模型：音频信息、标签、曲目条目、处理状态。

设计原则：
1. ``original`` 保留磁盘上的原始标签快照，任何写入都基于 ``working`` 生成，
   便于「撤销 / 不覆盖已有信息 / 差异预览」。
2. 所有字段可序列化，支持任务保存与断点续处理。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional, Any


# ---------------------------------------------------------------- 格式清单

#: 无损格式（P0 重点适配）
LOSSLESS_EXTS: frozenset[str] = frozenset(
    {".flac", ".ape", ".wav", ".wave", ".wv", ".tta", ".m4a", ".dsf", ".dff", ".aif", ".aiff"}
)

#: 有损格式（兼容兜底）
LOSSY_EXTS: frozenset[str] = frozenset({".mp3", ".aac", ".ogg", ".oga", ".opus", ".mpc", ".wma"})

SUPPORTED_EXTS: frozenset[str] = LOSSLESS_EXTS | LOSSY_EXTS

#: 加密格式：明确不支持、不解密，导入时直接跳过并提示
ENCRYPTED_EXTS: frozenset[str] = frozenset(
    {".ncm", ".qmc", ".qmc0", ".qmc2", ".qmc3", ".qmcflac", ".qmcogg", ".kgm", ".kgma", ".tm0", ".tm2", ".tm3", ".xm"}
)

CUE_EXT = ".cue"


class TrackStatus(str, enum.Enum):
    """单条曲目的处理状态。"""

    PENDING = "待处理"
    READING = "读取中"
    MATCHED = "已匹配"
    NEEDS_REVIEW = "待人工确认"
    WRITING = "写入中"
    DONE = "完成"
    SKIPPED = "已跳过"
    FAILED = "失败"
    UNSUPPORTED = "不支持"

    @property
    def is_terminal(self) -> bool:
        return self in (TrackStatus.DONE, TrackStatus.SKIPPED, TrackStatus.FAILED, TrackStatus.UNSUPPORTED)


class ConflictMode(str, enum.Enum):
    """已有标签冲突策略（需求 3.2.6）。"""

    KEEP_EXISTING = "不覆盖已有信息"
    OVERWRITE = "强制覆盖"
    FILL_EMPTY_ONLY = "仅空白才填充"


class LyricsMode(str, enum.Enum):
    """歌词输出模式（需求 3.3）。"""

    EMBED = "内嵌到标签"
    SIDECAR = "生成 .lrc 文件"
    BOTH = "两者都要"
    OFF = "不处理歌词"


# ---------------------------------------------------------------- 音频技术信息


@dataclass
class AudioInfo:
    """音频流技术参数，用于列表展示与筛选（需求 3.1.3）。"""

    fmt: str = ""                     # FLAC / APE / WAV / DSF / MP3 ...
    lossless: bool = False
    bitrate: Optional[int] = None      # bps
    sample_rate: Optional[int] = None  # Hz
    bit_depth: Optional[int] = None    # bit
    channels: Optional[int] = None
    duration: Optional[float] = None   # 秒
    dsd_rate: Optional[str] = None     # DSD64 / DSD128 ...
    file_size: int = 0

    # ---- 展示辅助 ----
    @property
    def bitrate_text(self) -> str:
        if not self.bitrate:
            return "—"
        return f"{self.bitrate / 1000:.0f} kbps"

    @property
    def sample_rate_text(self) -> str:
        if not self.sample_rate:
            return "—"
        return f"{self.sample_rate / 1000:g} kHz"

    @property
    def depth_text(self) -> str:
        if self.dsd_rate:
            return self.dsd_rate
        if self.bit_depth:
            return f"{self.bit_depth} bit"
        return "—"

    @property
    def spec_text(self) -> str:
        """一行式规格描述，如 ``24bit/96kHz`` 或 ``DSD64/2.8MHz``。"""
        if self.dsd_rate:
            return self.dsd_rate
        parts = []
        if self.bit_depth:
            parts.append(f"{self.bit_depth}bit")
        if self.sample_rate:
            parts.append(f"{self.sample_rate / 1000:g}kHz")
        return "/".join(parts) or "—"

    @property
    def duration_text(self) -> str:
        if not self.duration:
            return "—"
        total = int(self.duration)
        return f"{total // 60}:{total % 60:02d}"

    @property
    def kind_text(self) -> str:
        return "无损" if self.lossless else "有损"


# ---------------------------------------------------------------- 标签集合


@dataclass
class Tags:
    """统一标签视图，屏蔽 VorbisComment / ID3 / APEv2 / MP4 原子差异。"""

    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    date: str = ""          # 发行时间，尽量保留 ``YYYY`` 或 ``YYYY-MM-DD``
    genre: str = ""
    track: str = ""         # 曲目号，可含 ``3/12`` 形式
    disc: str = ""          # 碟号
    comment: str = ""
    lyrics: str = ""
    cover: Optional[bytes] = None       # 封面原始字节（JPG/PNG）
    cover_mime: str = ""
    extra: dict[str, list[str]] = field(default_factory=dict)  # 未映射的原始键值

    #: 参与「补全」的文本字段（封面与歌词单独处理）
    TEXT_FIELDS: tuple[str, ...] = (
        "title", "artist", "album", "albumartist", "date", "genre", "track", "disc",
    )

    def get(self, key: str) -> str:
        return getattr(self, key, "") or ""

    def set(self, key: str, value: str) -> None:
        if hasattr(self, key):
            setattr(self, key, value or "")

    def is_blank(self) -> bool:
        return not any(self.get(k).strip() for k in self.TEXT_FIELDS)

    def filled_ratio(self) -> float:
        """字段填充率，用于置信度评分。"""
        keys = self.TEXT_FIELDS
        return sum(1 for k in keys if self.get(k).strip()) / len(keys)

    def copy(self) -> "Tags":
        return Tags(**{**{f: getattr(self, f) for f in self.__dataclass_fields__}})

    def merge(self, new: "Tags", mode: ConflictMode = ConflictMode.FILL_EMPTY_ONLY) -> list[str]:
        """按冲突策略把 ``new`` 合并进自身，返回实际发生变更的字段名列表。"""
        changed: list[str] = []
        for key in self.TEXT_FIELDS:
            incoming = (new.get(key) or "").strip()
            if not incoming:
                continue
            current = (self.get(key) or "").strip()
            if mode is ConflictMode.KEEP_EXISTING and current:
                continue
            if mode is ConflictMode.FILL_EMPTY_ONLY and current:
                continue
            if current != incoming:
                setattr(self, key, incoming)
                changed.append(key)
        if new.cover and mode is not ConflictMode.KEEP_EXISTING:
            if self.cover is None or mode is ConflictMode.OVERWRITE:
                if self.cover != new.cover:
                    self.cover, self.cover_mime = new.cover, new.cover_mime
                    changed.append("cover")
        if new.lyrics and mode is ConflictMode.OVERWRITE:
            if self.lyrics != new.lyrics:
                self.lyrics = new.lyrics
                changed.append("lyrics")
        return changed

    def to_dict(self) -> dict[str, Any]:
        d = {k: v for k, v in asdict(self).items() if k != "cover"}
        d["has_cover"] = bool(self.cover)
        return d


@dataclass
class MatchCandidate:
    """联网匹配得到的一条候选结果（需求 3.2.5 候选列表）。"""

    source: str
    title: str = ""
    artist: str = ""
    album: str = ""
    albumartist: str = ""
    date: str = ""
    genre: str = ""
    track: str = ""
    disc: str = ""
    release_id: str = ""
    recording_id: str = ""
    cover_url: str = ""
    cover: Optional[bytes] = None
    cover_mime: str = ""
    raw_score: float = 0.0      # 数据源自带的相关度 0~1
    confidence: float = 0.0     # 聚合后的最终置信度 0~1
    note: str = ""

    def to_tags(self) -> "Tags":
        return Tags(
            title=self.title,
            artist=self.artist,
            album=self.album,
            albumartist=self.albumartist or self.artist,
            date=self.date,
            genre=self.genre,
            track=self.track,
            disc=self.disc,
            cover=self.cover,
            cover_mime=self.cover_mime,
        )

    @property
    def label(self) -> str:
        flags = []
        if self.album:
            flags.append(self.album)
        if self.date:
            flags.append(self.date[:4])
        head = f"{self.artist} - {self.title}" if self.artist else self.title
        return head + (f"（{' / '.join(flags)}）" if flags else "")


# ---------------------------------------------------------------- CUE 结构


@dataclass
class CueTrack:
    """CUE 中的单条曲目。"""

    number: int
    title: str = ""
    performer: str = ""
    start_ms: int = 0
    end_ms: int = 0

    @property
    def duration(self) -> float:
        return max(0.0, (self.end_ms - self.start_ms) / 1000.0)


@dataclass
class CueSheet:
    """整轨 CUE 解析结果（需求 3.1.2）。"""

    cue_path: Path
    audio_file: Optional[Path] = None
    title: str = ""
    performer: str = ""
    album: str = ""
    genre: str = ""
    date: str = ""
    tracks: list[CueTrack] = field(default_factory=list)
    encoding_used: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def is_single_track(self) -> bool:
        return len(self.tracks) <= 1


# ---------------------------------------------------------------- 曲目条目


@dataclass(eq=False)
class Track:
    """列表中的一条曲目（一个音频文件）。

    ``eq=False``：刻意使用**身份比较**。dataclass 默认的字段级 ``==`` 会让
    两个内容相同但不同文件的 Track 互相相等，导致 ``list.index(track)``
    定位到错误的行 —— 深层比较上百个字段在大曲库下还会明显拖慢刷新。
    """

    path: Path
    info: AudioInfo = field(default_factory=AudioInfo)
    original: Tags = field(default_factory=Tags)   # 磁盘原始标签快照
    working: Tags = field(default_factory=Tags)    # 待写入的目标标签
    status: TrackStatus = TrackStatus.PENDING
    confidence: float = 0.0                        # 匹配置信度 0~1
    source: str = ""                               # 匹配来源
    notes: list[str] = field(default_factory=list)  # 处理说明 / 错误详情
    cue: Optional[CueSheet] = None
    lyrics_src: str = ""                           # 歌词来源（本地/网络）
    lyrics_sidecar: str = ""                       # 待写入 .lrc 的歌词正文
    changed_fields: list[str] = field(default_factory=list)
    backup_path: Optional[Path] = None
    elapsed_ms: int = 0

    # ---- 派生属性 ----
    @property
    def name(self) -> str:
        return self.path.name

    @property
    def title(self) -> str:
        return self.working.title or self.original.title or self.path.stem

    @property
    def artist(self) -> str:
        return self.working.artist or self.original.artist

    @property
    def album(self) -> str:
        return self.working.album or self.original.album

    @property
    def exists(self) -> bool:
        try:
            return self.path.exists()
        except OSError:
            return False

    def note(self, message: str) -> None:
        if message and message not in self.notes:
            self.notes.append(message)

    def reset_working(self) -> None:
        """把 working 回滚为原始标签。"""
        self.working = self.original.copy()
        self.changed_fields.clear()

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "status": self.status.value,
            "confidence": round(self.confidence, 3),
            "source": self.source,
            "notes": self.notes,
            "info": asdict(self.info),
            "tags": self.working.to_dict(),
        }
