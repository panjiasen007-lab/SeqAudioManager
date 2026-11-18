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
    cue_split_trim_pregap: bool = False    # CUE 分轨在下一轨 INDEX 00 处截止（去掉轨尾预间隙）
    #: 分轨输出格式：``flac`` 无损压缩 / ``wav`` 无压缩 PCM（体积与原盘接近）。
    #: WAV 的位深跟随源文件（16bit 出 s16、24bit 出 s24），不做降位深。
    cue_split_format: str = "flac"
    skip_encrypted: bool = True

    # ---- 界面 ----
    theme: str = "深空"                     # 主题名（见 ui/theme.THEMES）
    language: str = "zh_CN"                 # 界面语言（见 ui/i18n.LOCALES）
    ui_font: str = "noto"                   # 界面字体 id（见 ui/fonts.UI_CHOICES）
    mono_font: str = "jetbrains"            # 数据字体 id（见 ui/fonts.MONO_CHOICES）
    font_scale: float = 1.0                 # 字号倍率 0.85 ~ 1.35
    column_widths: list[int] = field(default_factory=list)   # 列表列宽（退出时保存）
    hidden_columns: list[int] = field(default_factory=list)  # 隐藏的列索引
    sort_column: int = -1                   # 排序列（-1 = 不排序）
    sort_order: int = 0                     # 0 升序 / 1 降序
    view_state_saved: bool = False          # 是否已有保存过的列布局
    cleanup_include_suspect: bool = True   # 清理时是否包含「疑似」项

    # ---- 文件夹封面 ----
    ensure_folder_cover: bool = True       # 处理完成后每个文件夹至少一张 cover（专辑封面→歌手照片）

    # ---- 播放（v1.6，架构文档第七节）----
    # 默认值就是产品定稿：共享输出、采样率跟随、DSP 全旁路、软件音量 0 dB 旁路、
    # 播放相关标签写回关。字段追加靠 from_dict 的白名单自动兼容旧 config.json，
    # 不需要迁移脚本。
    #
    # 这里刻意**不** import player.constants：依赖方向规定 core 不认识 player
    # （架构文档 2.3），所以字符串/数值默认值在此按字面量写，与
    # player/constants.py 的常量保持一致由测试守护。
    playback_enabled: bool = True                  # 播放功能总开关（关=隐藏播放条与入口）
    playback_bar_visible: bool = True              # 播放条显示（折叠态 56 px 单行）
    playback_signal_panel_visible: bool = False    # 信号路径面板展开态
    playback_auto_lyrics: bool = True              # 播放时自动联网匹配歌词（单次请求，不写盘）
    # 默认 **MME** 而不是 WASAPI：Windows 共享模式的 WASAPI 只接受系统混音格式
    # （本机实测 48000），44.1 kHz 源必然被重采样，与「采样率跟随」的核心承诺冲突；
    # MME/DirectSound 同一物理设备实测可接受 44100/48000/88200/96000/176400/192000，
    # 186 ms 上报延迟对音乐播放可接受。想独占/低延迟可自行切 WASAPI 并在面板看到回退标注。
    playback_output_backend: str = "mme"           # mme/directsound/wasapi/wdmks
    playback_exclusive: bool = False               # WASAPI 独占（可选而非默认）
    playback_device: str = ""                      # 设备名，空 = 系统默认
    playback_follow_source_rate: bool = True       # 采样率跟随
    playback_dsd_pcm_rate: int = 176400            # DSD→PCM 目标率（88.2k/176.4k/352.8k）
    playback_bit_perfect: bool = False             # 位完美模式（强制率/位深=源 + 0 dB + 空 DSP）
    playback_volume_mode: str = "bypass"           # bypass（固定 0 dB）/ software（浮点音量）
    playback_volume_db: float = 0.0                # 软件音量 dB，-60 视为静音
    playback_block_frames: int = 1024              # 回调块帧数
    playback_buffer_ms: int = 80                   # 目标缓冲深度（40 为下限）
    playback_dsp_bypass: bool = True               # 全局直通
    playback_eq_enabled: bool = False              # EQ 模块启用（与 bypass 互斥）
    playback_eq_gains: list[float] = field(default_factory=lambda: [0.0] * 10)      # 10 段增益 dB
    playback_eq_preset: str = "flat"               # flat/custom/A/B
    playback_eq_snapshot_a: list[float] = field(default_factory=lambda: [0.0] * 10)  # A/B 快照 A
    playback_eq_snapshot_b: list[float] = field(default_factory=lambda: [0.0] * 10)  # A/B 快照 B
    playback_gapless: bool = True                  # 同规格无缝续写
    playback_crossfade_sec: float = 0.0            # 交叉淡变秒数（0=关，1–10 s，与无缝互斥）
    playback_ramp_ms: int = 5                      # 切歌/暂停/seek 斜坡（1–5 ms）
    playback_mode: str = "sequential"              # sequential/shuffle/repeat_one/repeat_all
    playback_downmix_stereo: bool = False          # 多声道下混为 2.0
    playback_int_pipeline: bool = False            # 整数通路优先（音量旁路且无 DSP 时生效）
    playback_restore_session: bool = True          # 启动恢复队列/位置/模式/设备/音量
    playback_restore_position: bool = True         # 恢复时回到上次毫秒位置
    playback_lyrics_offset_ms: int = 0             # 歌词偏移（会话内的值优先）
    playback_writeback_tags: bool = False          # 播放相关写回标签（默认关）
    playback_exclusive_notice_shown: bool = False  # 独占首次说明是否已弹
    playback_mini_bar: bool = False                # 迷你条开关（弹性项）

    # ---- 曲库（v1.6）----
    library_roots: list[str] = field(default_factory=list)   # 曲库根目录（取代零散 last_folder）
    library_autoload: bool = True                            # 启动自动载入上次曲库
    library_recent_sort: bool = True                         # 启用「最近播放」排序键

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
