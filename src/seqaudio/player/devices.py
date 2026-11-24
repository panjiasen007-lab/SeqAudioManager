"""输出设备枚举、后端解析、采样率回退链。

设计约束
--------
* **探测不许出声**：判定「设备支持某采样率」只用 ``sd.check_output_settings()``
  （内部是 ``Pa_IsFormatSupported``），它不下发任何音频数据；绝不为了探测而
  ``start()`` 一条流。
  **唯一例外（v1.8.2）**：ASIO 可用性验证（``_asio_open_stream_ok``）。空壳
  ASIO 驱动（驱动残留但硬件不可用）会骗过 ``check_output_settings``——它只
  验证参数组合是否被驱动**声明**支持，不真正开流。所以 ASIO 必须再开一条
  **静音**短流才能识别真伪：缓冲是全零样本，依旧不出声；但"开流"毕竟不是
  只读查询，因此只在**没有播放进行时**才允许执行（见 ``set_playing_query``）。
* **异常定义放这里而不是 output.py**：``output.py`` 需要 import ``devices``，
  若异常反过来定义在 ``output.py`` 就成了循环导入。``output.py`` 会原样
  再导出这三个名字，对外用起来和架构文档的写法一致。
* **不主动打开设备**：枚举与探测都是只读查询，真正的流生命周期在 ``output.py``。
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .constants import (
    BACKEND_ASIO,
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

#: ASIO 构建加载失败过（仅本进程内有效）。一旦为真就**永远**走普通构建：
#: 反复重试一个明确加载不了的 DLL 只会把每次枚举都拖慢几十毫秒。
_ASIO_LOAD_FAILED: bool = False

#: 入口显式关闭 ASIO 时写入的标记（``run.py`` / ``app.py``）。为什么需要它：
#: ``sounddevice()`` 会 ``setdefault("SD_ENABLE_ASIO", "1")`` 兜底，而 ``--no-asio``
#: 只是把变量**删掉** —— 两者看起来一模一样，兜底会把用户的关闭动作悄悄推翻。
_ASIO_DISABLED_FLAG = "SEQAUDIO_ASIO_DISABLED"
#: 认作"真"的文本（与 run.py / app.py 保持同一份取值）
_TRUE_TEXT = ("1", "true", "yes", "on")


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


def asio_unavailable_reason() -> str:
    """ASIO 不可用的原因键：``"load_failed"`` / ``"switched_off"`` / ``""``。

    界面需要区分这两件事，因为用户能做的动作完全不同：

    * ``load_failed``：包体缺 ``libportaudio64bit-asio.dll`` 或依赖缺失 →
      换安装包 / 修包，重启没用；
    * ``switched_off``：用户自己用 ``--no-asio`` 关的 → 去掉参数重启即可。

    只说"ASIO 不可用"等于让用户去猜，两种原因两种修法，必须分开说。
    """
    if _ASIO_LOAD_FAILED:
        return "load_failed"
    if str(os.environ.get(_ASIO_DISABLED_FLAG, "")).strip().lower() in _TRUE_TEXT:
        return "switched_off"
    return ""


def asio_switch_disabled() -> bool:
    """本次运行是否**不该**尝试 ASIO 构建（用户关闭 或 上次加载失败）。"""
    return bool(asio_unavailable_reason())


# ---------------------------------------------------------------- 播放状态钩子

#: 「引擎是否正在播放」的查询钩子（由 ``PlayerBridge`` 启动时注入）。
#:
#: 为什么放模块级函数指针而不是参数：``asio_usable_device`` 的调用点分散在
#: 桥接层与设置面板，把"是否在播"逐层传下去会让所有调用方都背上这个参数；
#: 而 ``devices`` 层（player 包）**不许** import UI/引擎模块（架构 2.3），
#: 所以由 UI 层在创建桥接时把一个无参可调用对象注册进来，本模块只保存并
#: 调用它——依赖方向仍然是 UI → player，不出现反向导入。
_PLAYING_QUERY: Optional[Callable[[], bool]] = None


def set_playing_query(query: Optional[Callable[[], bool]]) -> None:
    """注册/清除「是否正在播放」查询（``query() -> bool``；``None`` 清除）。

    传入的 callable 必须线程安全且不抛异常（内部实现自己兜底）。
    """
    global _PLAYING_QUERY
    _PLAYING_QUERY = query


def playback_in_progress() -> bool:
    """当前是否有播放正在进行（钩子未注册或查询失败时按"没有"处理）。"""
    query = _PLAYING_QUERY
    if query is None:
        return False
    try:
        return bool(query())
    except Exception:  # noqa: BLE001 - 状态查询失败不能拖垮探测流程
        log.debug("播放状态查询钩子抛异常，按「未在播放」处理", exc_info=True)
        return False


# ---------------------------------------------------------------- ASIO 可用性


#: ASIO 开流验证的静音流时长（秒）。0.1~0.2 s 足以让驱动暴露"空壳"
#: （开流即抛 -9985/-9999），又不至于被用户察觉任何声音或延迟。
ASIO_OPEN_PROBE_SECONDS = 0.15
#: 开流验证的样本格式。选 int16：候选采样率下所有 ASIO 驱动都必然支持 16bit
#: 交错立体声（这是 ASIO 规范的最小公约数），不会把"驱动不支持 int16"误判
#: 成"硬件不可用"。
ASIO_OPEN_PROBE_DTYPE = "int16"
#: 开流验证的声道数：与 ``asio_usable_device`` 的探测口径一致（立体声）。
ASIO_OPEN_PROBE_CHANNELS = 2


def _asio_open_stream_ok(
    device: DeviceInfo,
    rate: int,
    channels: int = ASIO_OPEN_PROBE_CHANNELS,
    duration_sec: float = ASIO_OPEN_PROBE_SECONDS,
) -> bool:
    """对一台 ASIO 设备做「零出声短开流」验证；能开并正常关闭才返回 ``True``。

    为什么 ``check_output_settings`` 不够（v1.8.2 用户实测）：它只问驱动
    "这个参数组合你**声明**支持吗"（``Pa_IsFormatSupported``），而空壳 ASIO
    驱动（卸载残留/硬件拔除后驱动还在）对任何参数都会答"支持"——枚举得到、
    探测通过、一开流就 ``-9985``（设备被占用）/``-9999``（无效参数）。

    这里真开一条 ``duration_sec`` 秒的**全零静音**输出流：构造 + ``start()``
    不抛异常、``stop()``/``close()`` 正常收尾，才认定设备真正可用。任何一步
    抛异常都向上抛给调用方（调用方把该设备判为不可用）。
    """
    import numpy as np  # 局部导入：只有真开流验证需要造静音缓冲

    sd = sounddevice()
    frames = max(1, int(int(rate) * float(duration_sec)))
    silence = np.zeros((frames, int(channels)), dtype=ASIO_OPEN_PROBE_DTYPE)
    # ASIO 没有 WasapiSettings（extra_settings_for 对非 WASAPI 返回 None），
    # 这里显式传 None，语义与 supports_rate 的 ASIO 分支一致。
    stream = sd.OutputStream(
        samplerate=int(rate),
        channels=int(channels),
        dtype=ASIO_OPEN_PROBE_DTYPE,
        device=int(device.index),
        extra_settings=None,
    )
    try:
        stream.start()
        # 写入静音缓冲；个别驱动对极短流的 write 报错不影响"能开流"这一结论，
        # 开流本身才是验证目标，所以写失败只吞掉不作为判据。
        try:
            stream.write(silence)
        except Exception:  # noqa: BLE001
            log.debug("ASIO 开流验证写入静音缓冲失败（不影响结论）", exc_info=True)
    finally:
        try:
            stream.stop()
        except Exception:  # noqa: BLE001
            pass
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass
    return True


def asio_usable_device(
    probe=None,
    *,
    open_probe=None,
    playing: Optional[bool] = None,
) -> Optional["DeviceInfo"]:
    """找一台「零出声探测 + 短开流验证」都通过的 ASIO 设备；全失败返回 ``None``。

    两级验证（v1.8.2 起）
    --------------------
    1. **枚举 + ``check_output_settings``**（零出声、不开流）：设备声明默认率
       优先，44.1k/48k 兜底；这一级过滤掉参数声明都不支持的设备。
    2. **静音短开流**（``_asio_open_stream_ok``）：exclusive + int16 + 候选率
       开一条 0.1~0.2 s 的静音流，能开并正常关闭才算真可用。空壳驱动会在
       这一级暴露（-9985/-9999），判该设备不可用，换下一台。

    冲突保护：引擎正在播放时（尤其正在用 ASIO 独占播放），**跳过开流验证**、
    只做第 1 级判断——此时再开第二条流要么打断播放、要么因设备被自己独占而
    吃 -9985，把好设备误判成坏设备。播放状态优先取参数 ``playing``（测试用），
    缺省查 ``playback_in_progress()``（由 ``PlayerBridge`` 注入）。

    Args:
        probe: 注入的零出声探测函数 ``(device, rate, channels, mode) -> bool``。
        open_probe: 注入的开流验证函数 ``(device, rate, channels) -> bool``；
            测试用 Fake 替换，避免真开声卡。
        playing: 显式指定"是否正在播放"（``True`` 跳过开流验证）；``None``
            表示由播放状态钩子决定。

    Returns:
        第一台两级验证都通过的设备；一台都没有则 ``None``。
    """
    devices = list_output_devices("asio")
    if not devices:
        return None
    check = probe or _check_rate
    verify_open = open_probe or _asio_open_stream_ok
    skip_open = playback_in_progress() if playing is None else bool(playing)
    for device in devices:
        base = int(device.rate or 0)
        for rate in dict.fromkeys((base, 44100, 48000)):  # 去重保序
            if rate <= 0:
                continue
            try:
                if not check(device, rate, 2, OutputMode.EXCLUSIVE):
                    continue
            except Exception:  # noqa: BLE001 - 单台探测异常换下一台
                continue
            if skip_open:
                # 正在播放：绝不开第二条流，零出声探测通过即认定可用
                return device
            try:
                if verify_open(device, rate, 2):
                    return device
                # 开流被拒（未抛异常的假阴性）：换下一个候选率再试
            except Exception:  # noqa: BLE001 - 开流失败（-9985/-9999 等）
                # 这些都是**设备级**失败（驱动加载不了/被独占/设备无效），
                # 换个采样率不会变好 → 直接换下一台，别再戳这台驱动
                break
    return None


def _import_sounddevice(enable_asio: bool):
    """按需 import sounddevice（``enable_asio`` 决定 dlopen 哪个 DLL）。"""
    if enable_asio:
        os.environ.setdefault("SD_ENABLE_ASIO", "1")
    else:
        os.environ.pop("SD_ENABLE_ASIO", None)
    import sounddevice as sd  # 局部导入：不拖慢 player 包的 import

    return sd


def sounddevice():
    """惰性取 sounddevice 模块；缺失时抛 ``DeviceError``。

    ASIO 构建加载失败**不等于**没声卡
    --------------------------------
    v1.8.1 实测踩到的坑：便携版包体只带了 ``libportaudio64bit.dll``，没有
    ``libportaudio64bit-asio.dll``。此时 ``SD_ENABLE_ASIO`` 一旦置上，sounddevice
    在 import 阶段就 ``dlopen`` 失败（``error 0x7e 找不到模块``），整个 PortAudio
    随之不可用 —— 表现是"程序能开、但完全放不出声"，比"没有 ASIO 后端"严重得多。
    所以这里必须**降级重试**：先按 ASIO 建，失败就摘掉变量用普通构建再 import 一次；
    只有两次都失败才报 ``DeviceError``。
    """
    global _SD, _ASIO_LOAD_FAILED
    if _SD is not None:
        # 已经 dlopen 过：此时再改环境变量没有任何作用，只记一条 DEBUG 说明
        # 「要改必须重启」——否则用户会以为在设置里改了什么就该立刻生效。
        if "SD_ENABLE_ASIO" not in os.environ:
            log.debug(
                "ASIO 环境变量已缺省，但 PortAudio 已在本次进程中加载："
                "改环境变量不会生效，需重启程序"
            )
        return _SD
    # 兜底：即使调用方没走 run.py / app.py，也在 import sounddevice 之前把
    # ASIO 构建标记放进环境（sounddevice 只看这个键**是否存在**）。
    # 但入口显式关过、或上次已加载失败，就绝不补回 —— 否则「关闭」与「降级」
    # 都会在下一次枚举时被悄悄推翻。
    want_asio = not asio_switch_disabled()
    try:
        sd = _import_sounddevice(want_asio)
    except Exception as exc:  # noqa: BLE001 - 缺 DLL / 权限异常都汇成一句
        if not want_asio:
            raise DeviceError(f"无法加载 sounddevice/PortAudio：{exc}") from exc
        log.warning(
            "ASIO 版 PortAudio 加载失败（%s）。已自动改用普通构建：本次运行没有 "
            "ASIO 后端，WASAPI/MME 不受影响。若是便携版，请确认安装包里带有 "
            "libportaudio64bit-asio.dll",
            exc,
        )
        _ASIO_LOAD_FAILED = True
        try:
            sd = _import_sounddevice(False)
        except Exception as exc2:  # noqa: BLE001
            raise DeviceError(f"无法加载 sounddevice/PortAudio：{exc2}") from exc2
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


# ---------------------------------------------------------------- 设备热插拔（v1.8.1）


@dataclass(frozen=True)
class DeviceChange:
    """两次设备枚举之间的差集（热插拔的判定结果）。"""

    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    default_changed: bool = False

    @property
    def changed(self) -> bool:
        """是否有任何变化（调用方据此决定要不要刷界面）。"""
        return bool(self.added or self.removed or self.default_changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": list(self.added),
            "removed": list(self.removed),
            "default_changed": bool(self.default_changed),
            "changed": self.changed,
        }


def _entry_name(entry: Any) -> str:
    """从 ``DeviceInfo`` **或** 桥接层投影的字典里取设备名。"""
    if isinstance(entry, DeviceInfo):
        return entry.name
    if isinstance(entry, dict):
        return str(entry.get("name") or "")
    return str(getattr(entry, "name", "") or "")


def _entry_is_default(entry: Any) -> bool:
    """从 ``DeviceInfo`` 或字典里取「是否系统默认设备」。"""
    if isinstance(entry, DeviceInfo):
        return bool(entry.is_default)
    if isinstance(entry, dict):
        return bool(entry.get("is_default", False))
    return bool(getattr(entry, "is_default", False))


def _enumerate_outputs(backend: str = "") -> list[DeviceInfo]:
    """``DeviceWatcher`` 的默认枚举器：**纯只读**的设备表查询。"""
    return list_output_devices(backend or None)


class DeviceWatcher:
    """设备表变化探测器（PRD P0-1：热插拔自动刷新）。

    三条不可越过的边界
    ------------------
    1. **不 import Qt**：``player`` 层是纯 Python（架构 2.3），定时器由
       ``main_window`` 持有的 ``QTimer`` 驱动，本类只负责"两次枚举做差"。
    2. **只枚举、不试开**：探测只调 ``list_output_devices()``（内部就是
       ``sd.query_devices()`` 的只读投影），绝不 ``start()`` 流、也不
       ``check_output_settings()`` —— 每 3 s 真开一次流会打断正在播的音乐。
    3. **比对键是设备名集合，不是索引**：启用 ASIO 后 5 个 hostapi 并存、
       PortAudio 的全局索引整体后移；按索引比会得到"每次都变了"的假阳性，
       于是每 3 s 刷一次界面（真的抖动）。名字也可能重复，但同名设备的
       增删在用户视角就是同一台，frozenset 正好表达这个语义。

    Args:
        backend: 后端键（``wasapi`` / ``asio`` …）；空串表示不限后端。
        enumerator: 注入的枚举器 ``(backend) -> list[DeviceInfo] | list[dict]``。
            ``PlayerBridge.device_watch()`` 返回的是字典列表（ui 层不允许
            import player），所以这里同时接受 ``DeviceInfo`` 与字典 —— 比在
            UI 层再造一层类型投影更不容易出错，测试也能直接喂假数据。
    """

    def __init__(
        self,
        backend: str = "",
        *,
        enumerator: Optional[Callable[[str], list[Any]]] = None,
    ) -> None:
        self._backend = str(backend or "")
        self._enumerate: Callable[[str], list[Any]] = enumerator or _enumerate_outputs
        self._names: frozenset[str] = frozenset()
        self._default = ""
        self._primed = False

    # ------------------------------------------------------------ 只读

    def names(self) -> frozenset[str]:
        """上一次轮询看到的设备名集合（基线）。"""
        return self._names

    def backend(self) -> str:
        return self._backend

    def primed(self) -> bool:
        """是否已经建立过基线（尚未建立时第一次 ``poll_once`` 不报变化）。"""
        return self._primed

    # ------------------------------------------------------------ 轮询

    def reset(self, backend: str = "") -> None:
        """换后端/重建基线：下一次 ``poll_once()`` 只建立基线，不报变化。

        为什么首次不报：换后端本身已经由 ``main_window`` 刷了一轮设备表，
        这里再报一次只会触发一次多余的"设备表变化"日志与重复刷新。
        """
        self._backend = str(backend or "")
        self._names = frozenset()
        self._default = ""
        self._primed = False

    def poll_once(self) -> DeviceChange:
        """枚举一次并与基线做差；返回变化（无变化时 ``changed`` 为 ``False``）。"""
        try:
            entries = list(self._enumerate(self._backend) or [])
        except Exception as exc:  # noqa: BLE001 - 枚举失败不该把定时器打死
            log.warning("设备表轮询失败，保留上次基线：%s", exc)
            return DeviceChange()
        names = frozenset(name for name in (_entry_name(e) for e in entries) if name)
        default = ""
        for entry in entries:
            name = _entry_name(entry)
            if name and _entry_is_default(entry):
                default = name
                break
        if not self._primed:
            self._names, self._default, self._primed = names, default, True
            return DeviceChange()
        added = tuple(sorted(names - self._names))
        removed = tuple(sorted(self._names - names))
        # 默认设备"变成空"（系统里没默认设备了）不算变化：那多半是枚举失败的
        # 中间态，报出来会让状态栏闪一下又回去。
        default_changed = bool(default) and default != self._default
        self._names = names
        if default:
            self._default = default
        change = DeviceChange(
            added=added, removed=removed, default_changed=default_changed
        )
        if change.changed:
            log.info(
                "设备表变化：+%s -%s%s",
                "/".join(added) or "无",
                "/".join(removed) or "无",
                " · 系统默认设备已变" if default_changed else "",
            )
        return change


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
    #: 本次计划走的是 ASIO 驱动独占（v1.8.1）。ASIO **不参与**四级阶梯，
    #: 这个标记让引擎/信号路径不必再靠"后端名猜测"来判断独占语义。
    asio: bool = False

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
            "asio": bool(self.asio),
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
    # ASIO 的"独占"是驱动天然行为，没有 WasapiSettings —— 不能因为 extra 为 None
    # 就判死。v1.8.1 踩过这个坑：ASIO 分支把独占计划排出来了，探测层却先把每
    # 一个候选率否掉，最终报「ASIO 只支持驱动声明的采样率」，把用户指去驱动面板
    # 白折腾一圈。判据只能是"这台设备是不是 ASIO"，而不是"有没有 extra_settings"。
    if mode_value.is_exclusive and extra is None and info.backend_key != BACKEND_ASIO:
        return False  # 非 WASAPI / 非 ASIO 设备没有独占
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


def describe_no_option(
    device: DeviceInfo,
    candidates: tuple[int, ...],
    channels: int,
    mode: OutputMode,
    skipped: set[int] | frozenset[int] = (),
    *,
    shared_available: bool = False,
) -> str:
    """「所有候选都打不开」的中文说明——**必须列出全部维度**。

    TC-EX-13 的教训：只报采样率会让 6 声道失败的用户去改采样率，改一辈子也改不好。
    归因错维度比不报错更糟（和 v1.6 那个 ``-9997`` 双义是同一类问题）。

    **源声道数少于设备声道数**是另一类归因错误（v1.8 实测）：
    某些环绕设备（如「扬声器 (Strix 7.1)」8 声道）在 WASAPI 下只接受**设备原生
    声道数**，给它 2 声道会直接被拒（共享/独占都一样）；而同一个物理设备在
    MME / DirectSound 下 2 声道完全正常。这种时候提「改用共享」或「下混 2.0」
    都是白费力气 —— 必须指向后端。

    Args:
        shared_available: 共享级是否**真的**有候选。只有它为真时才敢建议
            「改用共享输出」，否则那句话会把用户推向下一个死胡同。
    """
    dims: list[str] = []
    if candidates:
        dims.append("采样率 " + "/".join(str(rate) for rate in candidates) + " Hz")
    if channels:
        dims.append(f"{channels} 声道")
    dims.append("独占模式" if mode.is_exclusive else "共享模式")
    text = f"设备 {device.label} 在上述候选下都打不开：" + "；".join(dims)

    native = int(getattr(device, "channels", 0) or 0)
    if channels and native and channels < native:
        host = str(getattr(device, "hostapi_name", "") or "").replace("Windows ", "")
        text += (
            f"。该设备{('（' + host + '）') if host else ''}只接受 {native} 声道，"
            f"当前源是 {channels} 声道——请把输出后端改为 MME 或 DirectSound，"
            f"或换一台立体声设备"
        )
    elif channels > 2:
        text += f"。该设备可能不支持 {channels} 声道——可在设置里开启「多声道下混为 2.0」后重试"

    if mode.is_exclusive and shared_available:
        text += "。也可以改用共享输出"
    elif mode.is_exclusive and not shared_available:
        text += "。共享模式同样开不了，换个后端或设备才能出声"
    if skipped:
        text += f"（本次已跳过层级 {sorted(int(level) for level in skipped)}）"
    return text


#: ASIO 驱动实际声明的倍率阶梯（由高到低，仅用于**向下**回退）。
#:
#: 为什么不能复用 ``RATE_FALLBACK_FULL``：那条链只有 88.2k/96k/48k/44.1k，
#: 缺了 705.6k / 352.8k / 176.4k —— 而这三个正是 DSD/DoP 的载体率。少了它们，
#: 705.6k 一旦被驱动拒绝就直落 88.2k，等于把 352.8k 这级白白跳过（DSD 明明
#: 还能按 352.8k 播，却先被重采样成 1/8）。ASIO 的唯一可退维度就是采样率，
#: 所以这条链必须把驱动常用的倍率写全。
ASIO_RATE_LADDER = (1411200, 705600, 352800, 176400, 96000, 88200, 48000, 44100, 32000)


def _asio_candidate_rates(device: DeviceInfo, want_rate: int) -> tuple[int, ...]:
    """ASIO 的回退候选：源率 → 驱动倍率阶梯（只向下）→ 设备默认率。

    只收 ``<= want_rate`` 的候选：往上试 1.4112M 只会把缓冲区撑大、把 CPU 抬上去，
    对"这次起播能不能出声"毫无帮助。
    """
    want = int(want_rate) if int(want_rate) > 0 else 0
    trials: list[int] = []
    if want > 0:
        trials.append(want)
    for rate in ASIO_RATE_LADDER:
        if want <= 0 or int(rate) <= want:
            trials.append(int(rate))
    default_rate = int(getattr(device, "rate", 0) or 0)
    if default_rate > 0 and (want <= 0 or default_rate <= want):
        trials.append(default_rate)
    seen: set[int] = set()
    ordered: list[int] = []
    for rate in trials:
        if rate in seen:
            continue
        seen.add(rate)
        ordered.append(rate)
    return tuple(ordered)


def _plan_asio_output(
    device: DeviceInfo,
    want_rate: int,
    channels: int,
    requested_mode: OutputMode,
    skipped: set[int],
    check,
) -> OutputPlan:
    """ASIO 的单级计划：只按候选率降级，模式恒为 ``EXCLUSIVE``。

    为什么不能套共享/独占四级阶梯（设计 §1.4）
    ------------------------------------------
    * ASIO 驱动天然独占 —— 一个驱动同一时刻只服务一个客户端，"退回共享"这个
      动作对 ASIO 不存在（它的"共享"等于换一个后端）。所以既没有第 4 级，
      也没有"严格 → 宽松"第 2 级（``WasapiSettings`` 对 ASIO 毫无意义，
      ``extra_settings_for`` 已对非 WASAPI 返回 ``None``）；
    * 唯一能退的维度是**采样率**，而 ASIO 驱动只接受它自己声明的采样率
      （本机实测 44100/48000/88200/96000/176400/352800 可用，705600/1411200
      被驱动直接拒绝）。全失败时必须 ``RateUnsupportedError`` 并写明原因，
      绝不许静默换成共享或别的后端。

    Args:
        skipped: 本次起播里已经失败过的层级（1/3）。ASIO 只有这两级，两级都
            失败就抛错，避免在同一组参数上原地重试。
    """
    # 「严格独占」是 WASAPI 四级阶梯里的概念；ASIO 只有一条独占通路，
    # 没有"严格 → 宽松"这一级（本函数 docstring 的边界）。把 STRICT 原样
    # 带进 requested_mode 会让 OutputPlan.mode_fallback 判成"模式降级"，
    # 引擎进而对一条本来开得出来的流报「独占无法按原参数打开」—— 界面
    # 就在对用户说谎。按"请求即独占"归一：ASIO 达成的就是 EXCLUSIVE 本身。
    if requested_mode is OutputMode.EXCLUSIVE_STRICT:
        requested_mode = OutputMode.EXCLUSIVE
    candidates = _asio_candidate_rates(device, want_rate)
    for rate in candidates:
        if not check(device, rate, channels, OutputMode.EXCLUSIVE):
            continue
        fallback = int(rate) != int(want_rate)
        level = 3 if fallback else 1
        if level in skipped:
            continue
        notes: list[str] = []
        if not requested_mode.is_exclusive:
            notes.append("ASIO 驱动没有共享模式，已按驱动独占打开")
        if fallback:
            notes.append(f"ASIO 驱动不接受 {want_rate} Hz，改用驱动声明的 {rate} Hz")
        return OutputPlan(
            int(rate),
            OutputMode.EXCLUSIVE,
            level,
            fallback,
            int(want_rate),
            requested_mode,
            "；".join(notes),
            (int(rate),),
            True,
        )
    raise RateUnsupportedError(
        f"设备 {device.label} 在 ASIO 下打不开任何候选采样率（"
        + "/".join(str(rate) for rate in candidates)
        + " Hz）：ASIO 只支持驱动声明的采样率。请在驱动控制面板里启用需要的"
        "采样率后重试，或改用 WASAPI / MME 后端。",
        tried=candidates,
    )


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
            raise RateUnsupportedError(
                "系统里没有可用的输出设备（或 PortAudio 未加载）：无法开始播放"
            ) from exc
    requested_mode = normalize_output_mode(mode)
    check = probe or _check_rate
    ch = int(channels) if int(channels) > 0 else 0
    want_rate = int(want) if int(want) > 0 else int(device.rate or SR_44K)
    candidates = candidate_rates(device, want_rate, extended=extended)
    skipped = {int(level) for level in (skip_levels or ())}

    # ---- ASIO：提前返回单级计划（见 _plan_asio_output 的长注释）----
    # 判据用设备自己的 hostapi，而不是 settings 里的后端键：settings 可能还写着
    # "wasapi"（用户刚换设备没重存），真正决定开流语义的是**这台设备**。
    if device.backend_key == BACKEND_ASIO:
        return _plan_asio_output(device, want_rate, ch, requested_mode, skipped, check)

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
    # 共享到底有没有候选：下面那句「也可以改用共享输出」只有它为真时才成立。
    # 实测反例：8 声道环绕设备在 WASAPI 下 2 声道连共享都开不了，旧文案却一直
    # 建议「改用共享」，把用户从一个死胡同推向另一个。
    shared_ok = False
    for rate in candidates:
        if check(device, rate, ch, OutputMode.SHARED):
            shared_ok = True
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
        describe_no_option(
            device, candidates, ch, requested_mode, skipped, shared_available=shared_ok
        ),
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
    if str(key or "").strip().lower() == BACKEND_ASIO and _ASIO_LOAD_FAILED:
        # ASIO 构建没加载成功：普通构建里永远不会有 ASIO hostapi，
        # 直接给结论，不必再枚举一次。
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
    # 同 supports_rate：ASIO 的独占不需要 extra_settings，别在这里把「测试此设备」
    # 按钮弄成永远失败（用户看到的是"设备不支持独占"，而事实相反）。
    if mode_value.is_exclusive and extra is None and info.backend_key != BACKEND_ASIO:
        return False, "该后端/设备不支持独占（仅 WASAPI / ASIO 支持）", 0
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
    global _SD, _ASIO_LOAD_FAILED
    _SD = None
    _ASIO_LOAD_FAILED = False


__all__ = [
    "DeviceError",
    "NoOutputDeviceError",
    "DeviceBusyError",
    "DeviceLostError",
    "RateUnsupportedError",
    "DeviceInfo",
    "DeviceChange",
    "DeviceWatcher",
    "sounddevice",
    "asio_unavailable_reason",
    "asio_switch_disabled",
    "set_playing_query",
    "playback_in_progress",
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
