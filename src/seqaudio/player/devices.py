"""输出设备枚举、后端解析、采样率回退链。

设计约束
--------
* **探测不许出声**：判定「设备支持某采样率」只用 ``sd.check_output_settings()``
  （内部是 ``Pa_IsFormatSupported``），它不下发任何音频数据；绝不为了探测而
  ``start()`` 一条流。
* **异常定义放这里而不是 output.py**：``output.py`` 需要 import ``devices``，
  若异常反过来定义在 ``output.py`` 就成了循环导入。``output.py`` 会原样
  再导出这三个名字，对外用起来和架构文档的写法一致。
* **不主动打开设备**：枚举与探测都是只读查询，真正的流生命周期在 ``output.py``。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional

from .constants import (
    BACKEND_HOSTAPI_HINTS,
    BACKEND_KEYS,
    RATE_FALLBACK_CHAIN,
    RATE_FALLBACK_FULL,
    SD_OUTPUT_DTYPES,
    SR_44K,
    SR_48K,
)

log = logging.getLogger(__name__)

#: sounddevice 模块缓存（惰性导入：--audio-smoke 之外的大多数路径不需要它）
_SD: Any = None


class DeviceError(RuntimeError):
    """输出设备相关错误的基类。"""


class NoOutputDeviceError(DeviceError):
    """系统里没有任何可用的输出设备（对应发版检查清单 S8）。"""


class DeviceBusyError(DeviceError):
    """设备被独占（WASAPI 独占被浏览器/会议软件占用，S6）。"""


class DeviceLostError(DeviceError):
    """设备在播放过程中消失（USB 解码器拔出，FR-OUT-010）。"""


class RateUnsupportedError(DeviceError):
    """设备的任何候选采样率都打不开。"""

    def __init__(self, message: str, tried: tuple[int, ...] = ()) -> None:
        super().__init__(message)
        self.tried = tuple(tried)


def sounddevice():
    """惰性取 sounddevice 模块；缺失时抛 ``DeviceError``。"""
    global _SD
    if _SD is not None:
        return _SD
    try:
        import sounddevice as sd  # 局部导入：不拖慢 player 包的 import
    except Exception as exc:  # noqa: BLE001 - 缺 DLL / 权限异常都汇成一句
        raise DeviceError(f"无法加载 sounddevice/PortAudio：{exc}") from exc
    _SD = sd
    return _SD


@dataclass(frozen=True)
class DeviceInfo:
    """一个输出设备（PortAudio 设备表的一行投影）。"""

    index: int
    name: str
    channels: int
    default_samplerate: float
    is_default: bool = False
    hostapi_index: int = -1
    hostapi_name: str = ""

    @property
    def label(self) -> str:
        """``WASAPI · 扬声器 (Realtek)``，界面直接可用。"""
        if self.hostapi_name:
            return f"{self.hostapi_name} · {self.name}"
        return self.name

    @property
    def backend_key(self) -> str:
        """反查后端键（``wasapi`` / ``mme`` / …）；识别不出返回空串。"""
        return backend_key_for_hostapi(self.hostapi_name)

    @property
    def rate(self) -> int:
        """设备默认采样率取整（PortAudio 给的是 float）。"""
        return int(round(float(self.default_samplerate or 0)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "name": self.name,
            "label": self.label,
            "channels": self.channels,
            "default_samplerate": float(self.default_samplerate),
            "is_default": self.is_default,
            "hostapi": self.hostapi_name,
            "backend": self.backend_key,
        }


# ---------------------------------------------------------------- 后端解析


def _hostapi_names() -> list[str]:
    try:
        sd = sounddevice()
        return [str(api.get("name", "")) for api in sd.query_hostapis()]
    except Exception as exc:  # noqa: BLE001
        log.warning("读取 hostapi 列表失败：%s", exc)
        return []


def backend_key_for_hostapi(hostapi_name: str) -> str:
    """PortAudio hostapi 名 → 后端键（大小写不敏感的包含匹配）。"""
    text = str(hostapi_name or "").lower()
    if not text:
        return ""
    for key in BACKEND_KEYS:
        for hint in BACKEND_HOSTAPI_HINTS.get(key, ()):
            if hint in text:
                return key
    return ""


def resolve_backend(key: str) -> int:
    """后端键 → PortAudio hostapi 索引；``""`` / 无法识别时返回 ``-1``。

    ``-1`` 的语义是「不指定，交给系统默认」而不是「失败」：这样
    ``playback_output_backend`` 留空或写错时仍然能出声（宁可出声不要静音）。
    """
    text = str(key or "").strip().lower()
    if not text:
        return -1
    if text.isdigit():  # 允许直接写 hostapi 索引，便于排障
        return int(text)
    hints = BACKEND_HOSTAPI_HINTS.get(text, (text,))
    for index, name in enumerate(_hostapi_names()):
        lowered = name.lower()
        if any(hint in lowered for hint in hints):
            return index
    log.warning("未找到后端 %r，改用系统默认设备", key)
    return -1


def backend_default_output_index(hostapi_index: int) -> int:
    """某 hostapi 的默认输出设备索引；没有则 ``-1``。"""
    if hostapi_index < 0:
        return -1
    try:
        sd = sounddevice()
        apis = sd.query_hostapis()
        if 0 <= hostapi_index < len(apis):
            return int(apis[hostapi_index].get("default_output_device", -1))
    except Exception as exc:  # noqa: BLE001
        log.warning("读取 hostapi 默认设备失败：%s", exc)
    return -1


def system_default_output_index() -> int:
    """系统默认输出设备索引（PortAudio 的 ``default.device`` 输出项）；没有则 ``-1``。

    注意 ``sd.default.device`` 是 sounddevice 自定义的 ``_InputOutputPair``：
    它既**不是** ``tuple`` 实例，也不一定暴露 ``.output`` 属性（0.5.6 里只有
    ``__getitem__`` / ``_pair``）。早期版本用 ``isinstance(x, tuple)`` 判断，
    结果永远拿不到默认设备，``pick_default_device`` 会退化到「列表里第一台
    2 声道设备」（在本机就是 MME 的声音映射器虚拟设备，会让延迟读数与设备名
    都不对）。所以这里按「属性 → 下标」两段探测，都不行才算没有默认设备。
    """
    try:
        sd = sounddevice()
        device = sd.default.device
        value: Any = None
        try:
            value = device.output
        except (AttributeError, TypeError):
            value = None
        if value is None:
            try:
                value = device[1]
            except (TypeError, IndexError, KeyError):
                value = None
        if value is None and isinstance(device, int):
            value = device
        if value is None:
            return -1
        return int(value)
    except Exception as exc:  # noqa: BLE001
        log.warning("读取系统默认输出设备失败：%s", exc)
    return -1


# ---------------------------------------------------------------- 设备枚举


def list_output_devices(backend: Optional[str] = None) -> list[DeviceInfo]:
    """枚举所有可输出设备；``backend`` 非空时只保留该后端的设备。

    PortAudio 的设备索引是全局的（含输入设备），这里过滤掉
    ``max_output_channels <= 0`` 的项，并标记系统默认项。
    """
    try:
        sd = sounddevice()
        raw_devices = list(sd.query_devices())
        apis = list(sd.query_hostapis())
    except Exception as exc:  # noqa: BLE001 - 没声卡/没 DLL 都走这里
        log.warning("枚举输出设备失败：%s", exc)
        return []

    wanted_api = resolve_backend(backend) if backend else -1
    default_index = system_default_output_index()
    result: list[DeviceInfo] = []
    for index, info in enumerate(raw_devices):
        channels = int(info.get("max_output_channels", 0) or 0)
        if channels <= 0:
            continue
        api_index = int(info.get("hostapi", -1))
        if wanted_api >= 0 and api_index != wanted_api:
            continue
        api_name = ""
        if 0 <= api_index < len(apis):
            api_name = str(apis[api_index].get("name", ""))
        result.append(
            DeviceInfo(
                index=index,
                name=str(info.get("name", "")),
                channels=channels,
                default_samplerate=float(info.get("default_samplerate", 0.0) or 0.0),
                is_default=(index == default_index),
                hostapi_index=api_index,
                hostapi_name=api_name,
            )
        )
    return result


def device_by_index(index: int) -> Optional[DeviceInfo]:
    """按 PortAudio 全局索引取设备（含默认标记）。"""
    for device in list_output_devices():
        if device.index == int(index):
            return device
    return None


def pick_default_device(devices: Optional[list[DeviceInfo]] = None) -> DeviceInfo:
    """选默认输出设备：系统默认 → 首个 ≥2 声道 → 首个可用。

    Raises:
        NoOutputDeviceError: 一台可输出设备都没有（界面应显示「无可用输出设备」）。
    """
    items = list_output_devices() if devices is None else list(devices)
    if not items:
        raise NoOutputDeviceError("系统里没有可用的输出设备")
    default_index = system_default_output_index()
    for device in items:
        if device.is_default or (default_index >= 0 and device.index == default_index):
            return device
    for device in items:
        if device.channels >= 2:
            return device
    return items[0]


def resolve_device(name: str, backend: str = "") -> DeviceInfo:
    """按设备名（可空）+ 后端键定位设备。

    匹配顺序：
    1. 指定后端内的「名字包含」匹配（名字可能被 PortAudio 截断，故用包含）；
    2. 全后端范围内同样匹配（用户手填的设备名常不带后端限定）；
    3. 退化为后端默认设备 / 系统默认设备。

    Raises:
        NoOutputDeviceError: 一台可输出设备都没有。
    """
    text = str(name or "").strip()
    api_index = resolve_backend(backend)
    devices = list_output_devices()

    if text:
        scoped = [d for d in devices if api_index < 0 or d.hostapi_index == api_index]
        for pool in (scoped, devices):
            exact = [d for d in pool if d.name == text]
            if exact:
                return exact[0]
            lowered = text.lower()
            partial = [d for d in pool if lowered in d.name.lower()]
            if partial:
                return partial[0]
        log.warning("未找到设备 %r，改用默认输出设备", name)

    if api_index >= 0:
        default_index = backend_default_output_index(api_index)
        if default_index >= 0:
            for device in devices:
                if device.index == default_index:
                    return device
    return pick_default_device(devices)


# ---------------------------------------------------------------- 采样率能力


def supports_rate(device: DeviceInfo | int, rate: int, channels: int = 0) -> bool:
    """设备能否以 ``rate`` 打开（只做格式校验，绝不发声）。"""
    try:
        sd = sounddevice()
        index = device.index if isinstance(device, DeviceInfo) else int(device)
        ch = int(channels) if channels else 0
        if not ch:
            info = sd.query_devices(index)
            ch = min(2, int(info.get("max_output_channels", 0) or 0))
        if ch <= 0:
            return False
        probe_channels = max(1, min(ch, 2))
        for dtype in SD_OUTPUT_DTYPES:
            try:
                sd.check_output_settings(
                    device=index,
                    samplerate=int(rate),
                    channels=probe_channels,
                    dtype=dtype,
                )
                return True
            except Exception:  # noqa: BLE001 - 换下一种 dtype 再试
                continue
        return False
    except Exception as exc:  # noqa: BLE001
        log.debug("采样率探测失败（rate=%s）：%s", rate, exc)
        return False


def candidate_rates(
    device: DeviceInfo | None, want: int, *, extended: bool = True
) -> tuple[int, ...]:
    """回退候选序列（去重保序）。

    顺序按任务书 T02 的要求：源采样率 → 88.2k/96k/48k/44.1k → 设备默认率。
    架构文档第十节的 ``RATE_FALLBACK_CHAIN`` 候选集合被完整覆盖，只是把
    设备默认率放在最后（先尽量保住「采样率跟随」，全失败才落到混音格式）。
    """
    trials: list[int] = []
    if want and int(want) > 0:
        trials.append(int(want))
    trials.extend(RATE_FALLBACK_FULL if extended else (SR_48K, SR_44K))
    for item in RATE_FALLBACK_CHAIN:
        if item == "device_default":
            continue
        trials.append(int(item))
    default_rate = device.rate if device is not None else 0
    if default_rate > 0:
        trials.append(default_rate)
    seen: set[int] = set()
    ordered: list[int] = []
    for rate in trials:
        if rate in seen:
            continue
        seen.add(rate)
        ordered.append(rate)
    return tuple(ordered)


def pick_rate(
    device: DeviceInfo | None, want: int, *, extended: bool = True
) -> tuple[int, bool]:
    """选一个设备真正支持的开流采样率。

    Returns:
        ``(实际采样率, 是否发生回退)``。回退时打 WARNING（面板会标红）。

    Raises:
        RateUnsupportedError: 所有候选都失败（引擎应转 ``ERROR`` 并给出建议值）。
    """
    if device is None:
        try:
            device = pick_default_device()
        except NoOutputDeviceError as exc:
            raise RateUnsupportedError(str(exc)) from exc
    trials = candidate_rates(device, want, extended=extended)
    for rate in trials:
        if supports_rate(device, rate):
            fallback = bool(want) and rate != int(want)
            if fallback:
                log.warning(
                    "设备不支持 %d Hz，已回退 %d Hz（设备：%s）",
                    int(want), rate, device.label,
                )
            return rate, fallback
    raise RateUnsupportedError(
        f"设备 {device.label} 不支持候选采样率 {trials}", tried=trials
    )


def probe_supported_rates(
    device: DeviceInfo, rates: tuple[int, ...] = RATE_FALLBACK_FULL
) -> tuple[int, ...]:
    """列出设备实测支持的采样率（供设置面板显示与排障）。"""
    return tuple(rate for rate in rates if supports_rate(device, rate))


def reimport_sounddevice() -> None:
    """测试用：清掉模块缓存，模拟「重新加载 PortAudio」。"""
    global _SD
    _SD = None


__all__ = [
    "DeviceError",
    "NoOutputDeviceError",
    "DeviceBusyError",
    "DeviceLostError",
    "RateUnsupportedError",
    "DeviceInfo",
    "sounddevice",
    "backend_key_for_hostapi",
    "resolve_backend",
    "backend_default_output_index",
    "system_default_output_index",
    "list_output_devices",
    "device_by_index",
    "pick_default_device",
    "resolve_device",
    "supports_rate",
    "candidate_rates",
    "pick_rate",
    "probe_supported_rates",
    "reimport_sounddevice",
]
