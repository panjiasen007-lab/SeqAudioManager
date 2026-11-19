"""跨文件共享常量（架构文档第十节）。

为什么单独一个模块
------------------
采样率、块大小、状态枚举这些值会同时出现在解码器（拼 ffmpeg 命令）、输出
（开 PortAudio 流）、引擎（状态机）、界面（展示文案）四个地方。任何一处
各自写一遍字面量，就一定会出现「解码按 44.1k 开、设备按 48k 开」这类
只在真机上才暴露的错位。所以这里的规则是：**只允许 import，不允许另写**。

枚举统一用 ``class X(str, enum.Enum)`` 而不是 ``enum.StrEnum``：本项目
``pyproject.toml`` 声明的下限是 Python 3.10，``StrEnum`` 是 3.11 才有的，
而 ``model.py`` 里既有的 ``TrackStatus`` / ``ConflictMode`` 也都是
``str + Enum`` 写法，保持一致比追新更重要。取字符串时一律显式 ``.value``。
"""

from __future__ import annotations

import enum
import logging

# ---------------------------------------------------------------- 采样率

SR_44K = 44100
SR_48K = 48000
SR_88K2 = 88200
SR_96K = 96000
SR_176K4 = 176400
SR_192K = 192000
SR_352K8 = 352800

#: 声卡可能支持的常见采样率（不做枚举校验，仅用于界面与探测顺序）
SUPPORTED_RATES = (SR_44K, SR_48K, SR_88K2, SR_96K, SR_176K4, SR_192K, SR_352K8)

#: 架构文档第十节的最小回退链（源率之后依次尝试）
RATE_FALLBACK_CHAIN = ("device_default", SR_48K, SR_44K)

#: 开流时实际尝试的扩展候选（在最小回退链之外补上 88.2k / 96k）。
#: 之所以把设备默认率放在最后：设备默认率通常就是 Windows 混音格式，
#: 先试源率与高倍率能保住「采样率跟随」，只有全失败才落到混音格式。
RATE_FALLBACK_FULL = (SR_88K2, SR_96K, SR_48K, SR_44K)

#: DSD 转 PCM 的可选目标率（FR-OUT-006：只做 DSD→PCM）
DSD_PCM_RATE_DEFAULT = SR_176K4
DSD_PCM_RATE_CHOICES = (SR_88K2, SR_176K4, SR_352K8)

# ---------------------------------------------------------------- 缓冲与节奏

#: 回调块帧数默认值（44.1k 下约 23 ms）
BLOCK_FRAMES_DEFAULT = 1024
BUFFER_MS_DEFAULT = 80
BUFFER_MS_MIN = 40
BUFFER_MS_MAX = 500

#: 起播前预填块数：低于 1 块必然立刻欠载，2 块是实测的起播延迟/欠载折中点
PREFILL_BLOCKS = 2
#: 环形缓冲容量（秒）。2 s 足够吸收一次文件头解析或磁盘抖动
RING_SECONDS = 2.0

#: 解码线程写环时空间不足的自旋间隔（秒）
FEED_STALL_SLEEP_SEC = 0.002
#: 自旋超过该时长视为解码停滞（架构文档 5.4）
DECODE_STALL_TIMEOUT_SEC = 0.5

#: 连续欠载告警阈值（架构文档 5.4）
UNDERRUN_BURST_COUNT = 20
UNDERRUN_BURST_WINDOW_SEC = 2.0

#: 界面位置轮询周期（ms）
POSITION_POLL_MS = 100

#: 命令队列容量（架构文档 5.2：非阻塞 put，满即丢弃）
COMMAND_QUEUE_MAXSIZE = 32

#: 默认声道数（立体声）
DEFAULT_CHANNELS = 2

# ---------------------------------------------------------------- DSP

EQ_BANDS_HZ = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
EQ_Q = 1.41
EQ_MAX_DB = 12.0
EQ_MIN_DB = -12.0

RAMP_MS_MIN = 1
RAMP_MS_MAX = 5
RAMP_MS_DEFAULT = 5

CROSSFADE_MIN_SEC = 1.0
CROSSFADE_MAX_SEC = 10.0

#: 低于该 dB 视为静音：线性换算 ``10 ** (db / 20)``
DB_FLOOR_MUTE = -60.0

# ---------------------------------------------------------------- 后端键

#: 存进 Settings 的后端键（勿用中文/枚举名，见第十节）
BACKEND_WASAPI = "wasapi"
BACKEND_DIRECTSOUND = "directsound"
BACKEND_MME = "mme"
BACKEND_WDMKS = "wdmks"
#: ASIO：v1.7 只做「能力可见 + 明确不可用」（设计 §5）。
#: 官方 sounddevice wheel 的 PortAudio 构建**不含** ASIO，所以本机 backend_present("asio")
#: 恒为 False；用户若自己换了带 ASIO 的 DLL，这段代码也能识别并正常工作。
BACKEND_ASIO = "asio"

BACKEND_KEYS = (
    BACKEND_WASAPI, BACKEND_DIRECTSOUND, BACKEND_MME, BACKEND_WDMKS, BACKEND_ASIO,
)

#: 后端键 -> PortAudio hostapi 名里的识别片段（大小写不敏感）。
#: 各平台 hostapi 名不同，用「包含匹配」而不是等值匹配才不会在新版 PortAudio 上失配。
BACKEND_HOSTAPI_HINTS = {
    BACKEND_WASAPI: ("wasapi",),
    BACKEND_DIRECTSOUND: ("directsound", "direct sound"),
    BACKEND_MME: ("mme", "mm e"),
    BACKEND_WDMKS: ("wdm-ks", "wdmks", "wdm ks"),
    BACKEND_ASIO: ("asio",),
}

# ---------------------------------------------------------------- 样本格式

#: ffmpeg 侧 ``-acodec`` 取值。
#:
#: 与架构文档第十节的表有两处**刻意偏差**（原始表把 ``-f`` 的 muxer 名和
#: ``-acodec`` 的编解码器名混在一起，照抄会拼出 ``-acodec f32le`` 这种非法参数，
#: ffmpeg 直接拒绝启动、一个样本都不出）：
#:   1. ``f32`` 的编解码器名是 ``pcm_f32le``，``f32le`` 只是 ``-f`` 的管道格式；
#:   2. ``s24`` 走 ``pcm_s32le``：numpy/sounddevice 都没有 3 字节定宽类型，
#:      24 位样本必须在 int32 容器里传输（ffmpeg 左移 8 位补齐，幅度不变）。
PCM_FMT_TO_FFMPEG = {
    "s16": "pcm_s16le",
    "s24": "pcm_s32le",
    "s32": "pcm_s32le",
    "f32": "pcm_f32le",
}

#: ffmpeg 侧 ``-f``（管道格式）取值。s24 的裸流是 3 字节/样本，
#: numpy 没有 24 位定宽类型，因此统一用 s32 容器承载（见 decoder 注释）。
PCM_FMT_TO_PIPE = {
    "s16": "s16le",
    "s24": "s32le",
    "s32": "s32le",
    "f32": "f32le",
}

#: ffmpeg 样本格式 -> numpy dtype。s24 落 int32 是刻意的：
#: sounddevice 不支持「3 字节/样本」的交错缓冲，24 位源统一按 s32 传输。
PCM_FMT_TO_NUMPY = {
    "s16": "int16",
    "s24": "int32",
    "s32": "int32",
    "f32": "float32",
}

#: sounddevice 支持的输出 dtype（其 PortAudio 绑定只认这三种）
SD_OUTPUT_DTYPES = ("int16", "int32", "float32")

#: 默认样本格式：浮点。只有整数通路（位完美/旁路）才会改成 s16/s24/s32
DEFAULT_SAMPLE_FMT = "f32"


def numpy_dtype_name(sample_fmt: str) -> str:
    """ffmpeg 样本格式名（``s16`` / ``f32`` …）→ numpy dtype 名。

    这是「样本格式 → numpy dtype」的唯一入口：``PcmRingBuffer`` 只认 numpy
    dtype 名（``int16`` / ``float32``），而 ``DecodeSpec.sample_fmt`` 用的是
    ffmpeg 口径（``s16`` / ``f32``），两者之间必须经过本函数，
    绝不允许别处再各自 if/else 一遍位深。
    """
    text = str(sample_fmt or "")
    return PCM_FMT_TO_NUMPY.get(text, text)

# ---------------------------------------------------------------- 独占输出模式（v1.7）

#: WASAPI 的三个独占参数说明见 `docs/1.7-独占输出设计.md` §4.1。
#: ``auto_convert`` 恒为 False（本项目不借助系统转换，重采样一律由 ffmpeg 做，路径可查）。


class OutputMode(str, enum.Enum):
    """输出模式（v1.7）。

    三档不是「三个开关」，而是三个**互斥**的开流参数组合：

    * ``SHARED``：``extra_settings=None``，走系统混音器；
    * ``EXCLUSIVE``（宽松）：``WasapiSettings(exclusive=True)``，
      允许 PortAudio 协商样本格式（因此浮点也能开）；
    * ``EXCLUSIVE_STRICT``（严格）：``exclusive=True, explicit_sample_format=True``。
      **实测 float32 会被拒绝（-9997）**，所以严格模式与整数通路是强绑定
      （见设计 §2.2 补测 C），不是两个独立开关。

    ``exclusive=False, explicit=True`` 是无效组合（共享 + explicit 全部失败），
    配置层与面板都不允许出现。
    """

    SHARED = "shared"
    EXCLUSIVE = "exclusive"
    EXCLUSIVE_STRICT = "exclusive_strict"

    @property
    def i18n_key(self) -> str:
        return f"dev_mode_{self.value}"

    @property
    def is_exclusive(self) -> bool:
        """是否要求独占（宽松或严格）。"""
        return self is not OutputMode.SHARED

    @property
    def explicit_format(self) -> bool:
        """是否要求 PortAudio 原样使用请求的样本格式（不做格式协商）。"""
        return self is OutputMode.EXCLUSIVE_STRICT

    @property
    def mode_label(self) -> str:
        """信号路径用的短标签：``shared`` / ``loose`` / ``strict``。"""
        return {
            OutputMode.SHARED: "shared",
            OutputMode.EXCLUSIVE: "loose",
            OutputMode.EXCLUSIVE_STRICT: "strict",
        }[self]


def normalize_output_mode(value: object, default: OutputMode = OutputMode.SHARED) -> OutputMode:
    """把任意输入（枚举/字符串/布尔/None）收敛成 ``OutputMode``。

    * ``True``/``False``：来自 v1.6 的旧字段 ``playback_exclusive``；
    * 认不出的字符串：记 WARNING 并回 ``default``（不抛，配置层不能因为一个
      拼错的值就让整个设置页打不开）。
    """
    if isinstance(value, OutputMode):
        return value
    if isinstance(value, bool):
        return OutputMode.EXCLUSIVE if value else OutputMode.SHARED
    text = str(value or "").strip().lower()
    if not text:
        return default
    # 别名：能力矩阵的表头/信号路径用的是 short label（shared/loose/strict），
    # 调用方（测试、日志、面板）常直接把它传回来，这里统一认。
    aliases = {"loose": OutputMode.EXCLUSIVE, "strict": OutputMode.EXCLUSIVE_STRICT}
    if text in aliases:
        return aliases[text]
    try:
        return OutputMode(text)
    except ValueError:
        logging.getLogger(__name__).warning(
            "未知输出模式 %r，按 %s 处理", value, default.value
        )
        return default


# ---------------------------------------------------------------- 枚举


class PlayerState(str, enum.Enum):
    """播放状态机（架构文档 3.2 的 5 态裁剪版）。"""

    IDLE = "IDLE"
    LOADING = "LOADING"
    PLAYING = "PLAYING"
    PAUSED = "PAUSED"
    ERROR = "ERROR"

    @property
    def i18n_key(self) -> str:
        """界面取词用的 key（``play_state_idle`` 等）。"""
        return f"play_state_{self.value.lower()}"


class PlayMode(str, enum.Enum):
    """队列播放模式（FR-PL-002 本期四种）。"""

    SEQUENTIAL = "sequential"
    SHUFFLE = "shuffle"
    REPEAT_ONE = "repeat_one"
    REPEAT_ALL = "repeat_all"

    @property
    def i18n_key(self) -> str:
        return f"pl_mode_{self.value}"


class StageStatus(str, enum.Enum):
    """信号路径单级状态。

    架构文档 3.3 给的是 4 态（PASSTHROUGH/CHANGED/BYPASSED/ERROR），任务书
    T03 又要求「采样率回退时 resample 级状态为 fallback」。两者都要满足，
    所以取并集：多一个 ``FALLBACK``，语义是「本来要做的没做成，设备替我们
    降级了」——它必须和 ``CHANGED`` 区分开：``CHANGED`` 是我们主动改的，
    ``FALLBACK`` 是设备不支持被迫改的，界面要标红并给出原因。
    """

    PASSTHROUGH = "PASSTHROUGH"
    CHANGED = "CHANGED"
    BYPASSED = "BYPASSED"
    FALLBACK = "FALLBACK"
    ERROR = "ERROR"

    @property
    def i18n_key(self) -> str:
        return f"sig_status_{self.value.lower()}"


# ---------------------------------------------------------------- 信号路径各级

#: 固定 9 级，顺序即处理链顺序（架构文档 3.3 / FR-DSP-001）
STAGE_KEYS = (
    "source",
    "decode",
    "format",
    "gain",
    "eq",
    "fade",
    "resample",
    "output",
    "device",
)

#: 信号级 key -> i18n key
STAGE_I18N_KEYS = {key: f"sig_stage_{key}" for key in STAGE_KEYS}

# ---------------------------------------------------------------- 引擎事件名

#: 引擎事件名与 Qt 信号名同源（第十节：``on_event(kind, payload)``）
EVENT_STATE = "state"
EVENT_TRACK = "track"
EVENT_POSITION = "position"
EVENT_SIGNAL = "signal"
EVENT_ERROR = "error"
EVENT_QUEUE = "queue"
EVENT_GAP = "gap"

EVENT_NAMES = (
    EVENT_STATE,
    EVENT_TRACK,
    EVENT_POSITION,
    EVENT_SIGNAL,
    EVENT_ERROR,
    EVENT_QUEUE,
    EVENT_GAP,
)

# ---------------------------------------------------------------- 杂项

#: ffmpeg 子进程信息行解析的超时（秒）：只读文件头，不该久等
FFMPEG_PROBE_TIMEOUT_SEC = 15.0
#: 停止解码器时等待 pump 线程退出的上限（秒）
DECODER_JOIN_TIMEOUT_SEC = 2.0
#: stderr 环形缓存行数：只留最后几行用于报错摘要，绝不无限累积
DECODER_STDERR_LINES = 32


__all__ = [
    "SR_44K", "SR_48K", "SR_88K2", "SR_96K", "SR_176K4", "SR_192K", "SR_352K8",
    "SUPPORTED_RATES", "RATE_FALLBACK_CHAIN", "RATE_FALLBACK_FULL",
    "DSD_PCM_RATE_DEFAULT", "DSD_PCM_RATE_CHOICES",
    "BLOCK_FRAMES_DEFAULT", "BUFFER_MS_DEFAULT", "BUFFER_MS_MIN", "BUFFER_MS_MAX",
    "PREFILL_BLOCKS", "RING_SECONDS", "FEED_STALL_SLEEP_SEC", "DECODE_STALL_TIMEOUT_SEC",
    "UNDERRUN_BURST_COUNT", "UNDERRUN_BURST_WINDOW_SEC", "POSITION_POLL_MS",
    "COMMAND_QUEUE_MAXSIZE", "DEFAULT_CHANNELS",
    "EQ_BANDS_HZ", "EQ_Q", "EQ_MAX_DB", "EQ_MIN_DB",
    "RAMP_MS_MIN", "RAMP_MS_MAX", "RAMP_MS_DEFAULT",
    "CROSSFADE_MIN_SEC", "CROSSFADE_MAX_SEC", "DB_FLOOR_MUTE",
    "BACKEND_WASAPI", "BACKEND_DIRECTSOUND", "BACKEND_MME", "BACKEND_WDMKS",
    "BACKEND_ASIO",
    "BACKEND_KEYS", "BACKEND_HOSTAPI_HINTS",
    "PCM_FMT_TO_FFMPEG", "PCM_FMT_TO_PIPE", "PCM_FMT_TO_NUMPY",
    "SD_OUTPUT_DTYPES", "DEFAULT_SAMPLE_FMT", "numpy_dtype_name",
    "PlayerState", "PlayMode", "StageStatus", "OutputMode", "normalize_output_mode",
    "STAGE_KEYS", "STAGE_I18N_KEYS",
    "EVENT_STATE", "EVENT_TRACK", "EVENT_POSITION", "EVENT_SIGNAL", "EVENT_ERROR",
    "EVENT_QUEUE", "EVENT_GAP", "EVENT_NAMES",
    "FFMPEG_PROBE_TIMEOUT_SEC", "DECODER_JOIN_TIMEOUT_SEC", "DECODER_STDERR_LINES",
]
