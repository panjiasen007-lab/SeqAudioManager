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
    """一条输出流的完整参数（架构文档 3.1 类图）。"""

    backend: str = ""
    device: str = ""
    exclusive: bool = False
    samplerate: int = 44100
    channels: int = DEFAULT_CHANNELS
    dtype: str = "int16"
    blocksize: int = BLOCK_FRAMES_DEFAULT
    latency: str = "high"


def map_portaudio_error(exc: BaseException) -> DeviceError:
    """把 PortAudio 的报错文本映射成四类可处理异常。

    PortAudio 只给一句英文 + 负的错误码，界面要的是「设备忙，请关闭占用它的
    程序」这种能行动的信息，所以在这里做一次归类，而不是让字符串到处漏。
    """
    if isinstance(exc, (DeviceError,)):
        return exc
    text = str(exc).lower()
    if "-9985" in text or "busy" in text or "unavailable" in text:
        return DeviceBusyError(f"输出设备忙或被占用：{exc}")
    if "-9997" in text or "invalid sample rate" in text or "sample rate" in text:
        return RateUnsupportedError(f"设备不支持该采样率：{exc}")
    if "-9996" in text or "invalid device" in text or "device index" in text:
        return DeviceLostError(f"输出设备不可用（可能已被拔出）：{exc}")
    if "-9999" in text or "unanticipated host error" in text:
        return DeviceLostError(f"输出设备异常：{exc}")
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
        blocksize: int = BLOCK_FRAMES_DEFAULT,
        latency: str = "high",
        hook: Optional[DspHook] = None,
    ) -> None:
        self.ring = ring
        self._device = device
        self._backend = backend
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

    def start(self, rate: int, channels: int = 0) -> int:
        """按「源采样率 + 声道」开流并开始回调；返回设备**实际**采样率。

        采样率回退由 ``devices.pick_rate`` 决定（源率 → 88.2k/96k/48k/44.1k
        → 设备默认率）。发生回退时调用方（引擎）必须按返回的真实率重新
        配置解码器，否则会变调 —— 本层只如实上报，不偷偷重采样。
        """
        with self._lock:
            if self._stream is not None:
                raise DeviceError("输出流已经打开，先 stop() 再重新 start()")
            ch = int(channels) if channels else int(self.ring.channels)
            if ch != int(self.ring.channels):
                raise DeviceError(
                    f"声道数不一致：环形缓冲 {self.ring.channels}，请求 {ch}"
                )
            dtype = str(self.ring.dtype)
            if dtype not in ("int16", "int32", "float32"):
                raise DeviceError(f"PortAudio 不支持的输出 dtype：{dtype}")

            device = self._resolve_target()
            actual_rate, fallback = pick_rate(device, int(rate))
            config = OutputConfig(
                backend=(
                    self._backend
                    if str(self._backend or "").strip()
                    else device.backend_key
                ),
                device=str(self._device) if self._device not in ("", None) else str(device.index),
                exclusive=self._exclusive,
                samplerate=int(actual_rate),
                channels=ch,
                dtype=dtype,
                blocksize=self._blocksize,
                latency=self._latency,
            )
            opened = self.open(config)
            stream = self._stream
            if stream is None:  # pragma: no cover - open 成功即非空
                raise DeviceError("输出流打开失败")
            try:
                stream.start()
            except Exception as exc:  # noqa: BLE001
                mapped = map_portaudio_error(exc)
                self._error_text = str(mapped)
                self._teardown_stream()
                raise mapped from exc
            self._want_rate = int(rate)
            self._fallback = bool(fallback)
            return int(opened.samplerate)

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

        extra_settings = None
        if cfg.exclusive:
            device_backend = self._device_info.backend_key if self._device_info else ""
            if device_backend == BACKEND_WASAPI:
                try:
                    extra_settings = sd.WasapiSettings(exclusive=True)
                except Exception as exc:  # noqa: BLE001 - 驱动不支持独占
                    log.warning("构造 WASAPI 独占设置失败，改用共享模式：%s", exc)
                    extra_settings = None
            else:
                log.info(
                    "后端 %r 不支持独占，按共享模式打开（设备：%s）",
                    device_backend or cfg.backend or "默认", cfg.device,
                )

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
            mapped = map_portaudio_error(exc)
            self._error_text = str(mapped)
            raise mapped from exc

        self._stream = stream
        self._config = replace(cfg, device=str(index))
        self._error_text = ""
        self._paused = False
        self._frames_out = 0
        self._underruns = 0
        self._pa_flags = 0
        self._last_underrun_frame = -1
        log.info(
            "输出流已建立：%s %d Hz %d ch %s，块 %d，延迟提示 %s",
            self._config.backend or "默认后端",
            self._config.samplerate,
            self._config.channels,
            self._config.dtype,
            self._config.blocksize,
            self._config.latency,
        )
        return self._config

    # ------------------------------------------------------------ 关流

    def stop(self) -> None:
        """停止并关闭流（状态机语义：停止 = 关流 → IDLE，不留半开句柄）。"""
        with self._lock:
            stream = self._stream
            if stream is None:
                return
            try:
                if stream.active:
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
            "exclusive": bool(cfg.exclusive) if cfg else False,
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
            "exclusive": bool(cfg.exclusive) if cfg else False,
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
