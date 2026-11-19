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
from dataclasses import dataclass, field
from typing import Any, Optional

from .constants import (
    BACKEND_HOSTAPI_HINTS,
    SR_96K,
    SR_192K,
    BACKEND_KEYS,
    BACKEND_WASAPI,
    RATE_FALLBACK_CHAIN,
    RATE_FALLBACK_FULL,
    SD_OUTPUT_DTYPES,
    SR_44K,
    SR_48K,
    OutputMode,
    normalize_output_mode,
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

    # 纯数字按 PortAudio 全局索引解释：引擎/界面常把索引当设备标识保存，
    # 按名字精确匹配永远匹配不上（会静默退回默认设备，用户看到的是「设置没生效」）。
    if text.isdigit():
        info = device_by_index(int(text))
        if info is not None:
            return info
        log.warning("设备索引 %s 不存在，改用默认输出设备", text)

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


# ---------------------------------------------------------------- 独占能力（v1.7）

#: 严格独占的样本格式白名单：**只有 int32**。
#:
#: 两条依据：① 补测 C：f32 + explicit_sample_format=True 必 -9997，所以不能是浮点；
#: ② QA 的 30 格真开流矩阵里严格列一律用 int32 测，且 Q11 拍板「16bit 源统一进
#: int32 容器」。因此**探测也必须用 int32**——用 int16 探会把只有 int16 能开的
#: 设备误报成"支持严格"，然后引擎强制 int32 开流失败（又是一次假阳性）。
STRICT_DTYPES = ("int32",)

#: 能力矩阵要探的采样率（设计 §9.1 的表格就是这四行 + 88.2/176.4 备用）。
#: 不能直接用 RATE_FALLBACK_FULL：那是"回退候选"，不含 192k —— 而
#: 「独占解锁 192k」正是本版最强的用户价值（Q2），漏探就等于把卖点藏起来。
CAPABILITY_RATES = (SR_44K, SR_48K, 88200, SR_96K, 176400, SR_192K)


def extra_settings_for(mode: OutputMode | str, device: DeviceInfo | int | None = None) -> Any:
    """模式 → ``sd.WasapiSettings``；共享或非 WASAPI 设备返回 ``None``。

    **这是全仓唯一构造 ``WasapiSettings`` 的地方**。散着写会出现「探测用宽松、
    开流用严格」这种前后不一致，而用户看到的只是"开了独占还是重采样"。

    ``auto_convert`` 恒为 False：本项目不借助系统做格式转换 —— 共享模式的重采样
    由 ffmpeg 完成并写进信号路径，转成什么样用户查得到。
    """
    mode_value = normalize_output_mode(mode)
    if not mode_value.is_exclusive:
        return None
    if device is not None:
        info = device if isinstance(device, DeviceInfo) else device_by_index(int(device))
        if info is not None and info.backend_key != BACKEND_WASAPI:
            return None  # 非 WASAPI 后端没有独占概念
    sd = sounddevice()
    try:
        return sd.WasapiSettings(
            exclusive=True,
            auto_convert=False,
            explicit_sample_format=mode_value.explicit_format,
        )
    except Exception as exc:  # noqa: BLE001 - 旧版 sounddevice / 驱动不支持
        log.warning("构造 WasapiSettings 失败（%s）：%s", mode_value.value, exc)
        return None


@dataclass
class OutputPlan:
    """一次起播的输出决定（采样率 + 模式 + 回退层级 + 说明）。"""

    rate: int
    mode: OutputMode
    #: 1=原样达成 2=模式降级（严格→宽松） 3=独占内降采样率 4=退回共享
    level: int = 1
    fallback: bool = False
    requested_rate: int = 0
    requested_mode: OutputMode = OutputMode.SHARED
    note: str = ""
    supported_rates: tuple[int, ...] = ()

    @property
    def exclusive(self) -> bool:
        return self.mode.is_exclusive

    @property
    def rate_fallback(self) -> bool:
        return bool(self.requested_rate) and int(self.rate) != int(self.requested_rate)

    @property
    def mode_fallback(self) -> bool:
        return self.requested_mode is not self.mode

    def to_dict(self) -> dict[str, Any]:
        return {
            "rate": int(self.rate),
            "mode": self.mode.value,
            "level": int(self.level),
            "fallback": bool(self.fallback),
            "requested_rate": int(self.requested_rate),
            "requested_mode": self.requested_mode.value,
            "note": self.note,
            "supported_rates": list(self.supported_rates),
        }


@dataclass
class DeviceCapability:
    """一台设备的能力矩阵：``rate -> {shared/exclusive/strict: bool}``。

    实测依据（设计 §2.2 补测 A/D）：带 ``extra_settings`` 的
    ``check_output_settings()`` 与真实开流结论 **12/12 一致**，所以这张表可以
    当唯一判据，不需要试错开流（零出声、可缓存、< 30 ms）。
    """

    device_index: int
    device_name: str = ""
    channels: int = 0
    fingerprint: str = ""
    #: rate -> {mode_value: bool}
    rates: dict[int, dict[str, bool]] = field(default_factory=dict)
    #: rate -> 该率下第一个可用的 dtype（记录实测用的格式，便于排障）
    dtypes: dict[int, str] = field(default_factory=dict)

    def supports(self, rate: int, mode: OutputMode | str = OutputMode.SHARED) -> bool:
        mode_value = normalize_output_mode(mode)
        return bool(self.rates.get(int(rate), {}).get(mode_value.value, False))

    def supported_rates(
        self, mode: OutputMode | str = OutputMode.SHARED
    ) -> tuple[int, ...]:
        mode_value = normalize_output_mode(mode)
        return tuple(
            sorted(rate for rate, flags in self.rates.items() if flags.get(mode_value.value))
        )

    @property
    def exclusive_usable(self) -> bool:
        return bool(self.supported_rates(OutputMode.EXCLUSIVE))

    @property
    def strict_usable(self) -> bool:
        return bool(self.supported_rates(OutputMode.EXCLUSIVE_STRICT))

    def unlocked_rates(self) -> tuple[int, ...]:
        """「只在独占下可用」的采样率 —— dev21 的 96k/192k 就属于这一类。

        这正是本版最强的用户价值（设计 Q2）：文案必须限定"本设备实测支持"。
        """
        shared = set(self.supported_rates(OutputMode.SHARED))
        exclusive = set(self.supported_rates(OutputMode.EXCLUSIVE))
        return tuple(sorted(exclusive - shared))

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_index": int(self.device_index),
            "device_name": self.device_name,
            "channels": int(self.channels),
            "fingerprint": self.fingerprint,
            "rates": {str(rate): dict(flags) for rate, flags in self.rates.items()},
            "dtypes": {str(rate): name for rate, name in self.dtypes.items()},
            "exclusive_usable": self.exclusive_usable,
            "strict_usable": self.strict_usable,
            "unlocked_rates": list(self.unlocked_rates()),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DeviceCapability":
        rates: dict[int, dict[str, bool]] = {}
        raw_rates = (data or {}).get("rates") or {}
        if isinstance(raw_rates, dict):
            for key, flags in raw_rates.items():
                try:
                    rate = int(key)
                except (TypeError, ValueError):
                    continue
                if isinstance(flags, dict):
                    rates[rate] = {str(m): bool(v) for m, v in flags.items()}
        dtypes: dict[int, str] = {}
        for key, name in ((data or {}).get("dtypes") or {}).items():
            try:
                dtypes[int(key)] = str(name)
            except (TypeError, ValueError):
                continue
        return cls(
            device_index=int((data or {}).get("device_index", -1) or -1),
            device_name=str((data or {}).get("device_name", "") or ""),
            channels=int((data or {}).get("channels", 0) or 0),
            fingerprint=str((data or {}).get("fingerprint", "") or ""),
            rates=rates,
            dtypes=dtypes,
        )


def device_fingerprint(devices: Optional[list[DeviceInfo]] = None) -> str:
    """设备表指纹：``(index, name, hostapi)`` 集合的哈希。

    缓存失效条件之一（设计 §4.2）：插拔设备、换驱动后指纹变化 → 整表重探。
    """
    items = list_output_devices() if devices is None else list(devices)
    payload = "|".join(
        f"{d.index}:{d.name}:{d.hostapi_index}" for d in sorted(items, key=lambda x: x.index)
    )
    return str(hash(payload))


def probe_capabilities(
    device: DeviceInfo | int,
    *,
    channels: int = 0,
    rates: tuple[int, ...] = CAPABILITY_RATES,
) -> DeviceCapability:
    """探测一台设备的完整能力矩阵（共享/宽松/严格 × 采样率）。

    **零出声**：只调 ``check_output_settings()``（内部是 ``Pa_IsFormatSupported``），
    不下发任何音频数据。

    声道数按**设备可用声道数**取，不是写死 2（设计 §2.2 补测 B：dev19 是 8 声道
    设备，2ch 探测必然假阴性 —— 那正是 v1.6 里「Strix 7.1 两模式都失败」的真因）。
    """
    info = device if isinstance(device, DeviceInfo) else device_by_index(int(device))
    if info is None:
        raise NoOutputDeviceError(f"设备 {device!r} 不存在")
    ch = int(channels) if int(channels) > 0 else max(1, int(info.channels))
    sd = sounddevice()
    capability = DeviceCapability(
        device_index=info.index,
        device_name=info.name,
        channels=ch,
        fingerprint=device_fingerprint(),
    )
    for rate in rates:
        flags: dict[str, bool] = {}
        for mode in (OutputMode.SHARED, OutputMode.EXCLUSIVE, OutputMode.EXCLUSIVE_STRICT):
            extra = extra_settings_for(mode, info)
            dtypes = STRICT_DTYPES if mode.explicit_format else SD_OUTPUT_DTYPES
            ok = False
            for dtype in dtypes:
                try:
                    sd.check_output_settings(
                        device=info.index,
                        samplerate=int(rate),
                        channels=ch,
                        dtype=dtype,
                        extra_settings=extra,
                    )
                except Exception:  # noqa: BLE001 - 换下一种 dtype/模式继续
                    continue
                ok = True
                capability.dtypes.setdefault(int(rate), dtype)
                break
            flags[mode.value] = ok
        capability.rates[int(rate)] = flags
    log.debug(
        "能力探测 %s（%d ch）：%s",
        info.name, ch,
        {rate: [m for m, ok in flags.items() if ok] for rate, flags in capability.rates.items()},
    )
    return capability


class ProbeCache:
    """进程内能力矩阵缓存（设计 §4.2 的缓存三条失效条件）。"""

    def __init__(self) -> None:
        self._items: dict[tuple[int, int], DeviceCapability] = {}
        self._fingerprint = ""

    def clear(self) -> None:
        self._items.clear()
        self._fingerprint = ""

    def invalidate(self, device: DeviceInfo | int) -> None:
        index = device.index if isinstance(device, DeviceInfo) else int(device)
        for key in [k for k in self._items if k[0] == index]:
            self._items.pop(key, None)

    def invalidate_all(self) -> None:
        self._items.clear()

    def get(
        self, device: DeviceInfo | int, channels: int = 0, *, refresh: bool = False
    ) -> DeviceCapability:
        info = device if isinstance(device, DeviceInfo) else device_by_index(int(device))
        if info is None:
            raise NoOutputDeviceError(f"设备 {device!r} 不存在")
        ch = int(channels) if int(channels) > 0 else max(1, int(info.channels))
        key = (info.index, ch)
        current = device_fingerprint()
        if current != self._fingerprint:
            # 设备表变了（插拔/换驱动）：整表作废，避免拿旧矩阵骗人
            self._items.clear()
            self._fingerprint = current
        if refresh or key not in self._items:
            self._items[key] = probe_capabilities(info, channels=ch)
        return self._items[key]

    def to_dict(self) -> dict[str, Any]:
        """序列化（跨会话复用：设计 §10 的 ``playback_device_probe_cache``）。

        带上当前设备表指纹：下次启动若设备表变了（插拔/换驱动），这份缓存
        会整体作废 —— 拿旧矩阵骗人比不缓存更糟。
        """
        current = device_fingerprint()
        if current != self._fingerprint:
            return {"fingerprint": current, "items": {}}
        return {
            "fingerprint": current,
            "items": {
                f"{index}:{channels}": capability.to_dict()
                for (index, channels), capability in self._items.items()
            },
        }

    def load_dict(self, data: Any) -> int:
        """从序列化数据恢复；指纹不符或结构异常时**静默忽略**。返回恢复条数。"""
        if not isinstance(data, dict):
            return 0
        current = device_fingerprint()
        if str(data.get("fingerprint", "")) != current:
            log.debug("设备表指纹已变化，丢弃上次会话的能力缓存")
            return 0
        items = data.get("items")
        if not isinstance(items, dict):
            return 0
        restored = 0
        for key, payload in items.items():
            try:
                index_text, channels_text = str(key).split(":", 1)
                index, channels = int(index_text), int(channels_text)
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            capability = DeviceCapability.from_dict(payload)
            if capability.device_index < 0:
                capability.device_index = index
            self._items[(index, channels)] = capability
            restored += 1
        self._fingerprint = current
        return restored

    def has(self, device: DeviceInfo | int, channels: int = 0) -> bool:
        """缓存里是否已有该设备的能力矩阵（不触发探测）。"""
        info = device if isinstance(device, DeviceInfo) else device_by_index(int(device))
        if info is None:
            return False
        ch = int(channels) if int(channels) > 0 else max(1, int(info.channels))
        return (info.index, ch) in self._items

    @property
    def size(self) -> int:
        return len(self._items)


#: 进程内单例（设置面板与引擎共用同一份，避免各探一遍）
_CACHE = ProbeCache()


def probe_cache() -> ProbeCache:
    return _CACHE


def supports_rate(
    device: DeviceInfo | int,
    rate: int,
    channels: int = 0,
    *,
    mode: OutputMode | str = OutputMode.SHARED,
) -> bool:
    """设备能否以 ``rate`` + ``mode`` 打开（零出声探测）。

    相比 v1.6 有两处关键修正（设计 §2.2 补测 A/B）：

    1. **带 ``extra_settings``**：不带的话问的是"共享模式支持吗"，独占用户在
       探测阶段就被判失败 → 解码器改成 48k → 独占开了 48k 却已经重采样。
    2. **声道数用设备可用声道**：v1.6 恒取 ``min(2, max_out)``，对 8 声道设备
       必然假阴性（dev19 实测 8ch 才通）。
    """
    info = device if isinstance(device, DeviceInfo) else device_by_index(int(device))
    if info is None:
        return False
    mode_value = normalize_output_mode(mode)
    ch = int(channels) if int(channels) > 0 else max(1, int(info.channels))
    if ch <= 0:
        return False
    extra = extra_settings_for(mode_value, info)
    if mode_value.is_exclusive and extra is None:
        return False  # 非 WASAPI 设备没有独占
    sd = sounddevice()
    dtypes = STRICT_DTYPES if mode_value.explicit_format else SD_OUTPUT_DTYPES
    for dtype in dtypes:
        try:
            sd.check_output_settings(
                device=info.index,
                samplerate=int(rate),
                channels=ch,
                dtype=dtype,
                extra_settings=extra,
            )
            return True
        except Exception:  # noqa: BLE001
            continue
    return False


def _check_rate(device, rate, channels, mode) -> bool:
    """可以注入的探测函数（测试用 Fake 替换，验证四级回退全部分支）。"""
    return supports_rate(device, rate, channels=channels, mode=mode)


def plan_output(
    device: DeviceInfo | None,
    want: int,
    *,
    channels: int = 0,
    mode: OutputMode | str = OutputMode.SHARED,
    extended: bool = True,
    probe=None,
    skip_levels: frozenset[int] | set[int] | tuple[int, ...] = (),
) -> OutputPlan:
    """按四级阶梯产出**候选计划**，返回第一个未被跳过的一级。

    两条硬规则（设计 §4.3）：

    * **判据是"开流成功"，不是"探测通过"**。探测只用来给候选排序、少试几次。
      dev18 44.1k 独占就是活证据：探测 ✓ 而开流 `-9996` —— 引擎必须拿到"下一级"
      继续试，而不是在这里判死。
    * **同一级只降一次**：调用方把失败过的 level 放进 ``skip_levels``，
      本次起播就不再出同一级（避免原地重试同一参数）。

    本函数只负责"该试哪些"，**不负责真开流**：探测到的候选一律要由引擎真正打开
    一次才算数。

    Args:
        skip_levels: 本次起播里已经失败过的层级（1–4），会被跳过。
        probe: 注入的探测函数 ``(device, rate, channels, mode) -> bool``；
            测试用 Fake 覆盖所有分支而不碰声卡。
    """
    if device is None:
        try:
            device = pick_default_device()
        except NoOutputDeviceError as exc:
            raise RateUnsupportedError(str(exc)) from exc
    requested_mode = normalize_output_mode(mode)
    check = probe or _check_rate
    ch = int(channels) if int(channels) > 0 else 0
    want_rate = int(want) if int(want) > 0 else int(device.rate or SR_44K)
    candidates = candidate_rates(device, want_rate, extended=extended)
    skipped = {int(level) for level in (skip_levels or ())}

    plans: list[OutputPlan] = []

    def _add(plan: OutputPlan) -> None:
        if plan.level not in skipped:
            plans.append(plan)

    # ---- ①②：源采样率上先试严格、再试宽松 ----
    strict_note = ""
    if requested_mode is OutputMode.EXCLUSIVE_STRICT:
        if check(device, want_rate, ch, OutputMode.EXCLUSIVE_STRICT):
            _add(OutputPlan(want_rate, OutputMode.EXCLUSIVE_STRICT, 1, False,
                            want_rate, requested_mode, "", (want_rate,)))
        strict_note = "严格独占不可用，已降为宽松独占"
    if requested_mode.is_exclusive:
        if check(device, want_rate, ch, OutputMode.EXCLUSIVE):
            level = 2 if requested_mode.explicit_format else 1
            _add(OutputPlan(want_rate, OutputMode.EXCLUSIVE, level,
                            requested_mode.explicit_format, want_rate, requested_mode,
                            strict_note, (want_rate,)))

    # ---- ③：独占 + 回退采样率（候选里第一个探测可用的率）----
    if requested_mode.is_exclusive:
        for rate in candidates:
            if rate == want_rate:
                continue
            if check(device, rate, ch, OutputMode.EXCLUSIVE):
                note = (strict_note + "；" if strict_note else "") + (
                    f"独占下不支持 {want_rate} Hz，回退 {rate} Hz"
                )
                _add(OutputPlan(rate, OutputMode.EXCLUSIVE, 3, True, want_rate,
                                requested_mode, note, (rate,)))
                break

    # ---- ④：共享（系统混音）----
    if skipped and requested_mode.is_exclusive and all(
        level in skipped for level in (1, 2, 3)
    ):
        base_note = "独占各级开流均失败，已退回共享"
    else:
        base_note = "该设备不支持独占，已退回共享" if requested_mode.is_exclusive else ""
    for rate in candidates:
        if check(device, rate, ch, OutputMode.SHARED):
            rate_fell_back = rate != want_rate
            note = base_note
            if rate_fell_back:
                shared_note = f"设备不支持 {want_rate} Hz，已回退 {rate} Hz（共享）"
                note = (note + "；" if note else "") + shared_note
            _add(OutputPlan(
                rate, OutputMode.SHARED,
                4 if requested_mode.is_exclusive else (3 if rate_fell_back else 1),
                bool(requested_mode.is_exclusive or rate_fell_back),
                want_rate, requested_mode, note, (rate,),
            ))
            break

    if plans:
        return plans[0]
    raise RateUnsupportedError(
        f"设备 {device.label} 在共享/独占模式下都不支持候选采样率 {candidates}"
        + (f"（本次已跳过层级 {sorted(skipped)}）" if skipped else ""),
        tried=candidates,
    )


def demote_plan(
    plan: OutputPlan,
    want: int,
    *,
    device: DeviceInfo,
    channels: int = 0,
    skip_levels: frozenset[int] | set[int] | tuple[int, ...] = (),
    probe=None,
) -> OutputPlan | None:
    """取「下一级」计划；已到顶返回 ``None``。

    引擎开流失败后调用它，而不是自己猜"该降成什么"——阶梯定义只留在
    ``plan_output`` 一处，避免两处不一致（设计 §12.2 的契约）。
    """
    skipped = {int(level) for level in (skip_levels or ())}
    skipped.add(int(plan.level))
    try:
        return plan_output(
            device, want, channels=channels, mode=plan.requested_mode,
            probe=probe, skip_levels=skipped,
        )
    except RateUnsupportedError:
        return None


def pick_rate_for_mode(
    device: DeviceInfo | None,
    want: int,
    *,
    channels: int = 0,
    mode: OutputMode | str = OutputMode.SHARED,
) -> tuple[int, bool, OutputMode]:
    """``(实际率, 是否回退, 实际模式)`` —— 设计 §4.2 给引擎用的薄封装。"""
    plan = plan_output(device, want, channels=channels, mode=mode)
    return plan.rate, plan.fallback, plan.mode


def backend_present(key: str) -> bool:
    """某后端是否存在于当前 PortAudio 构建（ASIO 在官方 wheel 里不存在）。

    刻意不走 ``resolve_backend()``：那个函数在找不到时会打 WARNING（对"用户选了
    一个不存在的后端"是合适的），而这里问的是"这个构建有没有它"，属于正常查询，
    不该污染日志。
    """
    hints = BACKEND_HOSTAPI_HINTS.get(str(key or "").strip().lower(), ())
    if not hints:
        return False
    for name in _hostapi_names():
        lowered = name.lower()
        if any(hint in lowered for hint in hints):
            return True
    return False


def backend_available(
    key: str = "",
    *,
    rate: int = 0,
    channels: int = 2,
    mode: OutputMode | str = OutputMode.SHARED,
    dtype: str = "int16",
    device: DeviceInfo | int | None = None,
    duration_sec: float = 1.0,
) -> tuple[bool, str, int]:
    """真开一条流再关（**唯一允许的真开流探测**，只能由用户显式点击触发）。

    用途：设置面板的「测试此设备」按钮。请求里已经做了模式感知的零出声探测，
    但"能不能真的开出来"只有开一次才知道（驱动/占用/独占争用都会在开流时暴露）。

    Returns:
        ``(是否成功, 中文原因, 实际采样率)``。
    """
    import numpy as np  # 局部导入：只有这个函数需要造静音缓冲

    sd = sounddevice()
    info = (
        device
        if isinstance(device, DeviceInfo)
        else (device_by_index(int(device)) if device is not None else None)
    ) or resolve_device("", key)
    mode_value = normalize_output_mode(mode)
    probe_rate = int(rate) if int(rate) > 0 else (info.rate or SR_44K)
    extra = extra_settings_for(mode_value, info)
    if mode_value.is_exclusive and extra is None:
        return False, "该后端/设备不支持独占（仅 WASAPI 支持）", 0
    silence = np.zeros((max(1, int(probe_rate * min(1.0, duration_sec))), int(channels)),
                       dtype=dtype)
    try:
        stream = sd.OutputStream(
            samplerate=probe_rate,
            channels=int(channels),
            dtype=dtype,
            device=info.index,
            extra_settings=extra,
        )
        try:
            stream.start()
        finally:
            try:
                stream.stop()
            except Exception:  # noqa: BLE001
                pass
            stream.close()
        actual = int(getattr(stream, "samplerate", probe_rate) or probe_rate)
        del silence
        return True, "", actual
    except Exception as exc:  # noqa: BLE001
        # 函数内延迟导入：output.py 在模块级 import 本模块，反过来只在调用时才需要
        from .output import map_portaudio_error

        mapped = map_portaudio_error(
            exc, mode=mode_value, dtype=dtype, rate=probe_rate
        )
        return False, str(mapped), 0


# ---------------------------------------------------------------- 采样率能力


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
    device: DeviceInfo,
    rates: tuple[int, ...] = RATE_FALLBACK_FULL,
    *,
    mode: OutputMode | str = OutputMode.SHARED,
) -> tuple[int, ...]:
    """列出设备在给定模式下实测支持的采样率（供设置面板显示与排障）。"""
    return tuple(rate for rate in rates if supports_rate(device, rate, mode=mode))


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
    "STRICT_DTYPES",
    "CAPABILITY_RATES",
    "extra_settings_for",
    "OutputPlan",
    "DeviceCapability",
    "ProbeCache",
    "probe_cache",
    "device_fingerprint",
    "probe_capabilities",
    "plan_output",
    "demote_plan",
    "pick_rate_for_mode",
    "backend_present",
    "backend_available",
]
