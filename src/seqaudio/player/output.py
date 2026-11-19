"""PortAudio 输出流：回调里只做「读环 → 补零 → 上报」三件事。

回调七条禁令（架构文档 5.3，逐条对应到本文件的实现）
--------------------------------------------------
1. **不分配内存**：本文件回调体里没有 ``np.zeros`` / ``np.empty`` / 列表推导 /
   字符串拼接；补零用 ``outdata[filled:] = 0`` 的切片赋值，读环用
   ``ring.read_into(outdata)``（内部只有切片赋值）。
2. **不加锁**：没有 ``Lock`` / ``queue`` / ``Event.wait``。暂停用普通 bool 属性。
3. **不做 I/O**：不 ``logging``、不 ``print``、不碰文件。错误只累加计数与
   保存一个 *预先格式化不了* 的异常对象引用，由引擎线程取出后记录。
4. **不调 Qt**：本模块根本不 import PySide6（硬边界，见架构文档 2.3）。
5. **不抛异常**：整个回调体包在 ``try/except BaseException``，异常只置
   ``_cb_errors`` 并写静音。
6. **不做数学重活**：增益/EQ/斜坡全部由引擎在 T03 的 DSP 里预计算；这里只
   调用一个可选的 ``hook``（默认 None）。
7. **不改状态机**：本类只上报 underrun / 设备状态标志，状态机归 ``PlayerEngine``。

音量为什么是直通
----------------
本期（v1.6）软件音量固定 0 dB 旁路（产品定稿 R3），音量控件在界面置灰。
``set_volume()`` 仍然保留：它只记录 dB 值并如实报告「本层是直通」，
真正的浮点增益是引擎 DSP 的事（T03），输出层绝不偷偷改样本。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from typing import Any, Callable, Optional

import numpy as np

from .constants import (
    BACKEND_WASAPI,
    BLOCK_FRAMES_DEFAULT,
    DB_FLOOR_MUTE,
    DEFAULT_CHANNELS,
    OutputMode,
    normalize_output_mode,
)
from .devices import (
    DeviceBusyError,
    DeviceError,
    DeviceInfo,
    DeviceLostError,
    NoOutputDeviceError,
    RateUnsupportedError,
    device_by_index,
    pick_rate,
    resolve_device,
    sounddevice,
)
from .ringbuffer import PcmRingBuffer

log = logging.getLogger(__name__)

#: 引擎 DSP 钩子签名：``hook(outdata, filled)``，就地把 ``outdata[:filled]`` 处理掉
DspHook = Callable[[np.ndarray, int], None]


@dataclass
class OutputConfig:
    """一条输出流的完整参数（架构文档 3.1 类图 + v1.7 独占三参数）。

    ``exclusive`` 是 v1.6 的布尔旧字段，**保留只为兼容**：真正的语义在 ``mode``。
    构造时两者互相镜像（``exclusive=True`` → ``mode=EXCLUSIVE``），别再直接读
    ``exclusive`` 判断独占等级 —— 严格/宽松要靠 ``mode`` 区分。
    """

    backend: str = ""
    device: str = ""
    exclusive: bool = False
    samplerate: int = 44100
    channels: int = DEFAULT_CHANNELS
    dtype: str = "int16"
    blocksize: int = BLOCK_FRAMES_DEFAULT
    latency: str = "high"
    #: 三档输出模式（v1.7）。``exclusive`` 为真但 mode 未给时按宽松独占处理。
    mode: OutputMode = OutputMode.SHARED
    #: 采样率是否已由上游（引擎的探测层）定案。True 时 ``prepare()`` 不再自行探测。
    rate_decided: bool = False
    #: 是否要求 PortAudio 原样使用请求的样本格式（严格独占恒为 True）。
    explicit_format: bool = False

    def __post_init__(self) -> None:
        self.mode = normalize_output_mode(self.mode)
        if self.exclusive and self.mode is OutputMode.SHARED:
            # 旧字段镜像：v1.6 的 `exclusive=True` 一律视为宽松独占
            self.mode = OutputMode.EXCLUSIVE
        self.exclusive = self.mode.is_exclusive
        if self.mode.explicit_format:
            self.explicit_format = True

    @property
    def mode_label(self) -> str:
        return self.mode.mode_label


def map_portaudio_error(
    exc: BaseException,
    *,
    mode: OutputMode | str | None = None,
    dtype: str = "",
    rate: int = 0,
    supported_rates: tuple[int, ...] = (),
) -> DeviceError:
    """把 PortAudio 的报错映射成「能让用户采取下一步动作」的异常。

    PortAudio 只给一句英文 + 一个负错误码，而且**不同后端会借用同一个码**：
    本机 WDM-KS 在「设备在、驱动拒绝这个设备/格式组合」时给的是 ``-9996``
    ``paInvalidDevice``，与「USB 解码器被拔」共用一个码。一律说成「可能已被拔出」
    会让用户去拔插头（QA 实测反馈）。所以按错误码逐条给准信：

    ============  ====================================  ==========================
    码             含义                                  映射
    ============  ====================================  ==========================
    -9997          paInvalidSampleRate                    ``RateUnsupportedError``
    -9998          paInvalidChannelCount                  不支持该声道数
    -9994          paSampleFormatNotSupported             不支持该样本格式
    -9996          paInvalidDevice                        后端/驱动拒绝此设备或格式
    -9985          paDeviceUnavailable                    被其它程序占用（忙）
    -9999          paUnanticipatedHostError               驱动内部错误（建议换后端）
    ============  ====================================  ==========================

    只有明确是「设备消失」的文本才归到 ``DeviceLostError``（运行期拔线），
    这样界面能分开提示「换后端」与「插回设备」。
    """
    if isinstance(exc, (DeviceError,)):
        return exc
    text = str(exc).lower()
    mode_value = normalize_output_mode(mode) if mode is not None else None

    # -9997 是**双义**的（设计 §4.4）：严格模式下 float32 会被 explicit_sample_format
    # 拒绝，PortAudio 报的却是 "Invalid sample rate"。若这里仍说"采样率不支持"，
    # 用户会去改采样率 —— 改一辈子也没用，因为问题在样本格式。
    if "-9997" in text or "invalid sample rate" in text:
        if mode_value is not None and mode_value.explicit_format:
            is_float = str(dtype).startswith("float") or str(dtype) in ("f32", "f32le")
            if is_float:
                return RateUnsupportedError(
                    "严格独占要求整数样本格式，float32 不被接受："
                    "请开启「整数通路」（int16/int32），或改用宽松独占。"
                    f"（{exc}）"
                )
            hint = ""
            if supported_rates:
                hint = "；该设备在独占模式下实测支持：" + "/".join(
                    str(item) for item in supported_rates
                ) + " Hz"
            rate_text = f"{int(rate)} Hz" if rate else "该采样率"
            return RateUnsupportedError(
                f"该设备在独占模式下不接受 {rate_text}{hint}（{exc}）"
            )
        return RateUnsupportedError(f"设备不支持该采样率：{exc}")
    if "-9998" in text or "invalid channel count" in text or "channel count" in text:
        return DeviceError(f"设备不支持的声道数：{exc}")
    if "-9994" in text or "sample format not supported" in text:
        return DeviceError(f"设备不支持该样本格式：{exc}")
    if "removed" in text or "unplugged" in text or "no longer available" in text:
        return DeviceLostError(f"输出设备已断开（可能被拔出）：{exc}")
    if "-9996" in text or "invalid device" in text or "device index" in text:
        if mode_value is not None and mode_value.is_exclusive:
            return DeviceError(
                "该设备不支持独占模式（驱动或系统保留了它）：请改用共享模式，"
                f"或换一个输出设备/后端再试（{exc}）"
            )
        return DeviceError(
            "无法打开该输出设备：后端/驱动拒绝了它。"
            f"请换一个输出后端（如 MME / DirectSound）或另一台设备再试：{exc}"
        )
    if "-9985" in text or "busy" in text or "unavailable" in text:
        if mode_value is not None and mode_value.is_exclusive:
            return DeviceBusyError(
                f"该设备已被其它程序独占，请关闭那个程序后重试（{exc}）"
            )
        return DeviceBusyError(f"输出设备忙或被其它程序占用：{exc}")
    if "-9999" in text or "unanticipated host error" in text:
        return DeviceError(f"输出后端驱动内部错误（建议换一个后端再试）：{exc}")
    return DeviceError(f"打开输出流失败：{exc}")


class AudioOutput:
    """PortAudio 输出流 + 实时回调。

    Args:
        ring: 数据来源（解码线程写、回调线程读，唯一消费者就是这里）。
        device: 设备名或 PortAudio 索引；空 = 用系统/后端默认设备。
        backend: 后端键（``wasapi`` / ``mme`` …）；空 = 不指定。
            刻意**不**默认成 ``wasapi``：后端选择是用户设置
            （``settings.playback_output_backend``），库层面默认值不该假装知道。
        exclusive: WASAPI 独占（仅在 WASAPI 设备上生效）。
        blocksize: 回调块帧数。
        latency: PortAudio 延迟提示（``"low"`` / ``"high"`` / 秒数）。
        hook: 引擎 DSP 钩子，``None`` 表示纯直通。
    """

    def __init__(
        self,
        ring: PcmRingBuffer,
        *,
        device: str | int = "",
        backend: str = "",
        exclusive: bool = False,
        mode: OutputMode | str | None = None,
        blocksize: int = BLOCK_FRAMES_DEFAULT,
        latency: str = "high",
        hook: Optional[DspHook] = None,
    ) -> None:
        self.ring = ring
        self._device = device
        self._backend = backend
        # 请求值（用户想要的）与**实际值**（PortAudio 真正接受的）必须分开存：
        # v1.6 把请求值当实际值上报，勾了独占但降级共享时界面照样显示"独占"（会骗人）。
        self._mode_requested = (
            normalize_output_mode(mode)
            if mode not in (None, "")
            else (OutputMode.EXCLUSIVE if exclusive else OutputMode.SHARED)
        )
        self._mode_actual = OutputMode.SHARED
        self._exclusive_actual = False
        self._exclusive = bool(exclusive)
        self._blocksize = int(blocksize) if int(blocksize) > 0 else BLOCK_FRAMES_DEFAULT
        self._latency = latency
        self._hook = hook

        self._stream: Any = None
        self._config: Optional[OutputConfig] = None
        self._device_info: Optional[DeviceInfo] = None

        # ---- 回调共享的普通属性（单字节码写，引用替换原子）----
        self._paused = False
        self._frames_out = 0
        self._underruns = 0
        self._pa_flags = 0
        self._cb_errors = 0
        self._last_cb_error: Optional[str] = None
        self._last_underrun_frame = -1

        # ---- 非回调线程写的诊断信息 ----
        self._want_rate = 0
        self._fallback = False
        self._system_mix_rate = 0
        self._mode_downgrade_note = ""
        self._volume_db = 0.0
        self._volume_warned = False
        self._error_text = ""
        self._lock = threading.Lock()  # 只保护 open/close 的成对调用，回调不碰它

    # ------------------------------------------------------------ 只读属性

    @property
    def is_open(self) -> bool:
        """流句柄是否已创建且未关闭。"""
        return self._stream is not None

    @property
    def paused(self) -> bool:
        return bool(self._paused)

    @property
    def config(self) -> Optional[OutputConfig]:
        return self._config

    @property
    def device_info(self) -> Optional[DeviceInfo]:
        return self._device_info

    @property
    def underruns(self) -> int:
        return int(self._underruns)

    @property
    def frames_out(self) -> int:
        return int(self._frames_out)

    @property
    def mode_requested(self) -> OutputMode:
        """用户请求的模式（可能因设备/后端限制而降级）。"""
        return self._mode_requested

    @property
    def mode_actual(self) -> OutputMode:
        """**实际**生效的模式：只在流真的按该模式打开后才不是 SHARED。"""
        return self._mode_actual

    @property
    def exclusive_actual(self) -> bool:
        """流是否真的拿到了独占（信号路径与徽标只认这个值）。"""
        return bool(self._exclusive_actual)

    def actual_mode(self) -> OutputMode:
        """``mode_actual`` 的方法形式（设计文档 11.1 类图用的是它）。"""
        return self._mode_actual

    @property
    def system_mix_rate(self) -> int:
        """设备默认（= 系统混音）采样率。独占时它就是"被绕开的那个率"。"""
        return int(self._system_mix_rate)

    @property
    def downgraded(self) -> bool:
        """请求了独占但实际是共享（面板要标红并说明原因）。"""
        return self._mode_requested.is_exclusive and not self._exclusive_actual

    # ------------------------------------------------------------ 开流

    def _resolve_target(self) -> DeviceInfo:
        """把构造参数（名字 / 索引 / 空）解析成具体设备。"""
        raw = self._device
        if isinstance(raw, int) or (isinstance(raw, str) and raw.strip().isdigit()):
            info = device_by_index(int(raw))
            if info is not None:
                return info
            # 索引不存在（设备被拔掉）→ 退到后端/系统默认，不让起播直接失败
            return resolve_device("", self._backend)
        if isinstance(raw, str) and raw.strip():
            return resolve_device(raw, self._backend)
        return resolve_device("", self._backend)

    def _extra_settings_for(self, mode: OutputMode) -> Any:
        """模式 → ``sd.WasapiSettings``（唯一构造点，设计 §4.2）。

        返回 ``None`` 表示「共享」或「设备不是 WASAPI」：

        * 共享：不带 ``extra_settings``；
        * 宽松独占：``exclusive=True, auto_convert=False, explicit_sample_format=False``；
        * 严格独占：同上但 ``explicit_sample_format=True``（**只接受整数格式**）。

        ``auto_convert`` 恒为 False：本项目从不借助系统做格式转换，共享模式的
        重采样由 ffmpeg 完成并写进信号路径，转成什么样用户查得到。
        """
        if not mode.is_exclusive:
            return None
        device_backend = self._device_info.backend_key if self._device_info else ""
        if device_backend != BACKEND_WASAPI:
            # 非 WASAPI 后端没有独占概念：如实降级为共享，不抛错（T01 ⑤）
            log.info(
                "后端 %r 不支持独占，按共享模式打开（设备：%s）",
                device_backend or self._backend or "默认",
                self._device_info.name if self._device_info else self._device,
            )
            self._mode_actual = OutputMode.SHARED
            self._exclusive_actual = False
            self._mode_downgrade_note = "该后端不支持独占（仅 WASAPI 支持）"
            return None
        sd = sounddevice()
        try:
            return sd.WasapiSettings(
                exclusive=True,
                auto_convert=False,
                explicit_sample_format=mode.explicit_format,
            )
        except Exception as exc:  # noqa: BLE001 - 老版本/驱动不支持这些参数
            log.warning("构造 WASAPI 独占设置失败，改用共享模式：%s", exc)
            self._mode_actual = OutputMode.SHARED
            self._exclusive_actual = False
            self._mode_downgrade_note = f"无法构造独占参数：{exc}"
            return None

    def prepare(
        self,
        rate: int,
        channels: int = 0,
        *,
        mode: OutputMode | str | None = None,
        rate_decided: bool = False,
    ) -> int:
        """**只开流、不启动回调**，返回设备实际采样率。

        引擎先 ``prepare()`` 再起解码器预填，最后 ``begin()`` —— 这样「解码器
        已经在写、却还没有消费者」的窗口只有一个开流动作（几十毫秒），
        不会顶到解码线程 500 ms 的停滞阈值（T03 实测过：先起解码器再开流，
        慢设备上会丢掉 480 帧）。

        Args:
            rate: 目标采样率（``rate_decided=True`` 时就是最终值，不再探测）。
            channels: 声道数，0 = 用环形缓冲的声道数。
            mode: 覆盖构造时的输出模式（引擎按设置传，避免实例被复用后模式串味）。
            rate_decided: 上游（引擎探测层）已经定案，本层不得再调 ``pick_rate``。
                设计 §4.2「探测一次」原则：两次探测之间设备可能变化，也是
                v1.6 里 44.1k→48k 抖动的来源。
        """
        with self._lock:
            if self._stream is not None:
                raise DeviceError("输出流已经打开，先 stop() 再重新 prepare()")
            ch = int(channels) if channels else int(self.ring.channels)
            if ch != int(self.ring.channels):
                raise DeviceError(
                    f"声道数不一致：环形缓冲 {self.ring.channels}，请求 {ch}"
                )
            dtype = str(self.ring.dtype)
            if dtype not in ("int16", "int32", "float32"):
                raise DeviceError(f"PortAudio 不支持的输出 dtype：{dtype}")

            if mode is not None:
                self._mode_requested = normalize_output_mode(mode)
            requested_mode = self._mode_requested
            # 严格独占 + 浮点 = 必然 -9997（设计 §2.2 补测 C）。在**开流之前**
            # 拦下来，给能行动的中文原因，而不是让 PortAudio 抛一句
            # "Invalid sample rate" 让用户去改采样率。
            if requested_mode.explicit_format and dtype == "float32":
                raise DeviceError(
                    "严格独占要求整数样本格式（int16/int32），float32 不被接受："
                    "请开启「整数通路」，或改用宽松独占"
                )

            device = self._resolve_target()
            self._device_info = device
            self._system_mix_rate = int(device.rate)
            plan_mode = requested_mode
            if rate_decided:
                # 上游（引擎）已经定案：不再探测，也不改模式
                actual_rate, fallback = int(rate), False
            else:
                # 独立使用者（测试/工具直接 new AudioOutput）也要模式感知：
                # 否则「请求 44.1k 独占」会先被共享模式探测定罪，回退成 48k。
                from .devices import plan_output

                plan = plan_output(device, int(rate), channels=ch, mode=requested_mode)
                actual_rate, fallback = plan.rate, plan.fallback
                plan_mode = plan.mode
                if plan.note and not self._mode_downgrade_note:
                    self._mode_downgrade_note = plan.note
            config = OutputConfig(
                backend=(
                    self._backend
                    if str(self._backend or "").strip()
                    else device.backend_key
                ),
                device=str(self._device) if self._device not in ("", None) else str(device.index),
                exclusive=plan_mode.is_exclusive,
                mode=plan_mode,
                rate_decided=bool(rate_decided),
                samplerate=int(actual_rate),
                channels=ch,
                dtype=dtype,
                blocksize=self._blocksize,
                latency=self._latency,
            )
            opened = self.open(config)
            self._want_rate = int(rate)
            self._fallback = bool(fallback)
            return int(opened.samplerate)

    def begin(self) -> None:
        """启动回调（``prepare()`` 之后调用）。"""
        stream = self._stream
        if stream is None:
            raise DeviceError("输出流尚未打开，先 prepare()")
        try:
            stream.start()
        except Exception as exc:  # noqa: BLE001
            mapped = map_portaudio_error(
                exc,
                mode=self._mode_requested,
                dtype=str(self._config.dtype) if self._config else str(self.ring.dtype),
                rate=int(self._config.samplerate) if self._config else 0,
            )
            self._error_text = str(mapped)
            self._mode_actual = OutputMode.SHARED
            self._exclusive_actual = False
            self._teardown_stream()
            raise mapped from exc

    def start(self, rate: int, channels: int = 0) -> int:
        """``prepare()`` + ``begin()`` 的合并形式；返回设备**实际**采样率。

        采样率回退由 ``devices.pick_rate`` 决定（源率 → 88.2k/96k/48k/44.1k
        → 设备默认率）。发生回退时调用方（引擎）必须按返回的真实率重新
        配置解码器，否则会变调 —— 本层只如实上报，不偷偷重采样。
        """
        actual_rate = self.prepare(rate, channels)
        self.begin()
        return actual_rate

    def open(self, cfg: OutputConfig, callback: Optional[DspHook] = None) -> OutputConfig:
        """创建 PortAudio 流（不自动 start，方便引擎先预填再出声）。

        Args:
            cfg: 目标参数。``dtype`` 必须与环形缓冲一致（否则回调要转换，
                那就是回调里做重活，明确禁止）。
            callback: 可选的 DSP 钩子，就地处理 ``outdata[:filled]``；
                传了就覆盖构造时的 ``hook``。
        """
        if callback is not None:
            self._hook = callback
        if str(cfg.dtype) != str(self.ring.dtype):
            raise DeviceError(
                f"输出流 dtype（{cfg.dtype}）必须等于环形缓冲 dtype（{self.ring.dtype}）"
            )
        sd = sounddevice()
        candidate = str(cfg.device or "").strip()
        index: int | None = None
        if candidate:
            try:
                index = int(candidate)
            except ValueError:
                index = None
        if index is None:
            info = resolve_device(candidate, cfg.backend)
            index = info.index
            self._device_info = info
        else:
            # 索引是 PortAudio 的全局索引，直接反查即可（按名字反查会失配）
            self._device_info = device_by_index(index) or resolve_device("", cfg.backend)

        # 三参数的唯一构造点在 _extra_settings_for()：open() 只负责用它开流
        requested_mode = normalize_output_mode(cfg.mode)
        # cfg 明确要独占 → 它是用户的请求；cfg 已被降级成共享时**保留**用户原始请求
        # （否则「请求独占/实际共享」这个最有用的信息就丢了，面板没法解释降级原因）。
        if requested_mode.is_exclusive:
            self._mode_requested = requested_mode
        self._mode_downgrade_note = self._mode_downgrade_note or ""
        if requested_mode.explicit_format and str(cfg.dtype) == "float32":
            raise DeviceError(
                "严格独占要求整数样本格式（int16/int32），float32 不被接受："
                "请开启「整数通路」，或改用宽松独占"
            )
        extra_settings = self._extra_settings_for(requested_mode)

        kwargs: dict[str, Any] = {
            "samplerate": int(cfg.samplerate),
            "channels": int(cfg.channels),
            "dtype": str(cfg.dtype),
            "blocksize": int(cfg.blocksize),
            "device": index,
            "callback": self._audio_callback,
        }
        if cfg.latency:
            kwargs["latency"] = cfg.latency
        if extra_settings is not None:
            kwargs["extra_settings"] = extra_settings

        try:
            stream = sd.OutputStream(**kwargs)
        except Exception as exc:  # noqa: BLE001
            mapped = map_portaudio_error(
                exc,
                mode=requested_mode,
                dtype=str(cfg.dtype),
                rate=int(cfg.samplerate),
            )
            self._error_text = str(mapped)
            self._mode_actual = OutputMode.SHARED
            self._exclusive_actual = False
            raise mapped from exc

        self._stream = stream
        self._config = replace(cfg, device=str(index), mode=requested_mode)
        # 实际值：只有真的把独占参数交给了 PortAudio 且开流成功才算独占
        self._mode_actual = requested_mode if extra_settings is not None else OutputMode.SHARED
        self._exclusive_actual = self._mode_actual.is_exclusive
        if requested_mode.is_exclusive and not self._exclusive_actual and not self._mode_downgrade_note:
            self._mode_downgrade_note = "设备/后端未接受独占参数"
        self._error_text = ""
        self._paused = False
        self._frames_out = 0
        self._underruns = 0
        self._pa_flags = 0
        self._last_underrun_frame = -1
        log.info(
            "输出流已建立：%s %s %d Hz %d ch %s，块 %d，延迟提示 %s",
            self._config.backend or "默认后端",
            self._mode_actual.value,
            self._config.samplerate,
            self._config.channels,
            self._config.dtype,
            self._config.blocksize,
            self._config.latency,
        )
        if self._exclusive_actual:
            log.info(
                "已取得设备独占（%s），系统混音率 %d Hz 已被绕开",
                self._mode_actual.value, self._system_mix_rate or 0,
            )
        return self._config

    # ------------------------------------------------------------ 关流

    def stop(self, abort: bool = False) -> None:
        """停止并关闭流（状态机语义：停止 = 关流 → IDLE，不留半开句柄）。

        Args:
            abort: ``True`` 时用 ``Pa_AbortStream`` 直接丢弃缓冲。切歌/停止前
                引擎已经做完 5 ms 淡出，缓冲里剩下的是静音，没必要等它播完 ——
                实测 MME 上 ``stop()`` 要等 266 ms（缓冲区长度），``abort()`` 只要 4 ms。
                默认 ``False``：不吞掉未播音频，行为可预测。
        """
        with self._lock:
            stream = self._stream
            if stream is None:
                return
            try:
                if stream.active:
                    if abort and hasattr(stream, "abort"):
                        stream.abort()
                    else:
                        stream.stop()
            except Exception as exc:  # noqa: BLE001 - 关流失败不能挡住清理
                log.debug("停止输出流时出错（忽略）：%s", exc)
            self._teardown_stream()

    def close(self) -> None:
        """``stop()`` 的别名（架构文档 3.1 类图另有 close 语义，这里合并）。"""
        self.stop()

    def _teardown_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.close()
        except Exception as exc:  # noqa: BLE001
            log.debug("关闭输出流时出错（忽略）：%s", exc)

    # ------------------------------------------------------------ 播放控制

    def pause(self) -> None:
        """暂停：不关流，回调改写信静音。"""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def set_volume(self, db: float) -> None:
        """记录软件音量（本层直通）。

        本期输出层是 0 dB 直通：真正的浮点增益由引擎 DSP 负责。
        非 0 dB 时只 WARNING 一次，绝不悄悄改样本 —— 悄悄衰减会让
        「信号路径显示 0 dB 旁路」变成谎言。
        """
        value = float(db)
        self._volume_db = value
        if abs(value) > 1e-9 and not self._volume_warned:
            self._volume_warned = True
            log.warning(
                "输出层为 0 dB 直通，%.1f dB 由引擎浮点增益实现（本层不改样本）", value
            )
        if value <= DB_FLOOR_MUTE:
            log.debug("音量 %.1f dB 视为静音", value)

    def set_hook(self, hook: Optional[DspHook]) -> None:
        """替换 DSP 钩子（引擎在切换 EQ/旁路时整体替换，不做半更新）。"""
        self._hook = hook

    # ------------------------------------------------------------ 实时回调

    def _audio_callback(
        self,
        outdata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        """PortAudio 回调：读环 → 不足补零 → 交给钩子 → 计数。

        **这里不许有任何阻塞、分配、I/O、日志、Qt 调用**（见模块 docstring）。
        """
        try:
            if status:
                # 状态标志（溢出/欠载提示）只累加，不写日志
                self._pa_flags = self._pa_flags + 1
            if self._paused:
                outdata.fill(0)
                self._frames_out = self._frames_out + frames
                return
            filled = self.ring.read_into(outdata)
            if filled < frames:
                # 补零并记一次 underrun；不在这里格式化字符串
                outdata[filled:] = 0
                self._underruns = self._underruns + 1
                self._last_underrun_frame = self._frames_out + filled
            hook = self._hook
            if hook is not None and filled > 0:
                hook(outdata, filled)
            self._frames_out = self._frames_out + frames
        except BaseException as exc:  # noqa: BLE001 - 回调里绝不能让异常外泄
            self._cb_errors = self._cb_errors + 1
            try:
                self._last_cb_error = f"{type(exc).__name__}: {exc}"
            except BaseException:  # pragma: no cover - 极端情况下连名字都取不到
                self._last_cb_error = "unknown"
            try:
                outdata.fill(0)
            except BaseException:  # pragma: no cover
                pass

    # ------------------------------------------------------------ 读数

    def latency_ms(self) -> float:
        """当前流上报的输出延迟（毫秒）。"""
        stream = self._stream
        if stream is None:
            return 0.0
        try:
            value = stream.latency
        except Exception:  # noqa: BLE001
            return 0.0
        if isinstance(value, (tuple, list)):
            value = value[-1] if value else 0.0
        try:
            return float(value) * 1000.0
        except (TypeError, ValueError):
            return 0.0

    def device_format(self) -> dict[str, Any]:
        """设备侧上报格式（信号路径第 9 级用）。"""
        cfg = self._config
        return {
            "rate": int(cfg.samplerate) if cfg else 0,
            "channels": int(cfg.channels) if cfg else 0,
            "dtype": str(cfg.dtype) if cfg else str(self.ring.dtype),
            "latency_ms": round(self.latency_ms(), 1),
            "device": self._device_info.label if self._device_info else "",
            "backend": str(cfg.backend) if cfg else self._backend,
            # 只报**实际**值：请求了独占但降级共享时这里必须是 False
            "exclusive": bool(self._exclusive_actual),
            "mode": self._mode_actual.value,
            "mode_requested": self._mode_requested.value,
            "explicit_format": bool(self._mode_actual.explicit_format),
            "system_mix_rate": int(self._system_mix_rate),
            "downgraded": bool(self.downgraded),
            "mode_note": self._mode_downgrade_note,
        }

    def snapshot(self) -> dict[str, Any]:
        """只读快照（引擎/桥接 100 ms 轮询用，不阻塞回调）。

        键固定，便于 T03/T04 直接消费：
        ``is_open / rate / want_rate / fallback / channels / dtype / device /
        backend / exclusive / blocksize / latency_ms / underruns / frames_out /
        pa_flags / cb_errors / paused / volume_db / error``。
        """
        cfg = self._config
        return {
            "is_open": self.is_open,
            "rate": int(cfg.samplerate) if cfg else 0,
            "want_rate": int(self._want_rate),
            "fallback": bool(self._fallback),
            "channels": int(cfg.channels) if cfg else int(self.ring.channels),
            "dtype": str(cfg.dtype) if cfg else str(self.ring.dtype),
            "device": self._device_info.label if self._device_info else "",
            "device_index": self._device_info.index if self._device_info else -1,
            "backend": str(cfg.backend) if cfg else self._backend,
            # 只报**实际**值：请求了独占但降级共享时这里必须是 False
            "exclusive": bool(self._exclusive_actual),
            "mode": self._mode_actual.value,
            "mode_requested": self._mode_requested.value,
            "explicit_format": bool(self._mode_actual.explicit_format),
            "system_mix_rate": int(self._system_mix_rate),
            "downgraded": bool(self.downgraded),
            "mode_note": self._mode_downgrade_note,
            "blocksize": int(cfg.blocksize) if cfg else self._blocksize,
            "latency_ms": round(self.latency_ms(), 1),
            "underruns": int(self._underruns),
            "frames_out": int(self._frames_out),
            "pa_flags": int(self._pa_flags),
            "cb_errors": int(self._cb_errors),
            "cb_error": self._last_cb_error or "",
            "paused": bool(self._paused),
            "volume_db": float(self._volume_db),
            "error": self._error_text,
        }


__all__ = [
    "AudioOutput",
    "OutputConfig",
    "DspHook",
    "map_portaudio_error",
    "DeviceError",
    "DeviceBusyError",
    "DeviceLostError",
    "RateUnsupportedError",
    "NoOutputDeviceError",
]
