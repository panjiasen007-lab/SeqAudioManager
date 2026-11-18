"""播放能力包出口。

边界（架构文档 2.3 / 1.1）
--------------------------
``seqaudio.player`` 及其所有子模块**禁止 import PySide6**：内核要能在没有 Qt
的环境里跑单测，也避免音频线程误碰界面对象。界面层（``ui/player_*.py``）单向
依赖本包，反过来绝不允许。

为什么 T03 的名字用惰性导出
---------------------------
架构文档规定本包对外只暴露稳定接口（``PlayerEngine`` / ``PlayQueue`` /
``SignalPath`` / ``PlaybackSession`` 等），但这些类由任务 T03 落在
``engine.py`` / ``queue.py`` / ``signal_path.py`` / ``session.py`` 里，此刻
尚未创建。若在这里写死 ``from .engine import PlayerEngine``，T01/T02 阶段连
``import seqaudio.player`` 都会失败，T02 的测试与冒烟脚本跟着全废。

所以用模块级 ``__getattr__``：名字被真正访问时才去导入对应子模块。T03 建好
文件后这些名字自动可用，无需回来改本文件；在此之前访问会得到一条明确的
``AttributeError``（说明它属于哪个任务），而不是一个看不懂的
``ModuleNotFoundError``。
"""

from __future__ import annotations

import importlib
from typing import Any

from .constants import (
    EVENT_ERROR,
    EVENT_GAP,
    EVENT_POSITION,
    EVENT_QUEUE,
    EVENT_SIGNAL,
    EVENT_STATE,
    EVENT_TRACK,
    STAGE_KEYS,
    PlayMode,
    PlayerState,
    StageStatus,
)
from .decoder import DecodeError, DecodeSpec, FfmpegDecoder, probe_codec
from .devices import (
    DeviceBusyError,
    DeviceError,
    DeviceInfo,
    DeviceLostError,
    NoOutputDeviceError,
    RateUnsupportedError,
    list_output_devices,
    pick_default_device,
    pick_rate,
    supports_rate,
)
from .output import AudioOutput, OutputConfig
from .ringbuffer import PcmRingBuffer

#: 由任务 T03 实现的名字 -> (子模块, 属性名)
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "PlayerEngine": (".engine", "PlayerEngine"),
    "PlayerSnapshot": (".engine", "PlayerSnapshot"),
    "PlayerError": (".engine", "PlayerError"),
    "PlayQueue": (".queue", "PlayQueue"),
    "QueueItem": (".queue", "QueueItem"),
    "SignalPath": (".signal_path", "SignalPath"),
    "SignalStage": (".signal_path", "SignalStage"),
    "Counters": (".signal_path", "Counters"),
    "PlaybackSession": (".session", "PlaybackSession"),
    "GainStage": (".dsp", "GainStage"),
    "BiquadEq": (".dsp", "BiquadEq"),
}


def __getattr__(name: str) -> Any:
    """惰性导入 T03 的类（见模块 docstring）。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute = target
    try:
        module = importlib.import_module(module_name, __name__)
    except ModuleNotFoundError as exc:
        raise AttributeError(
            f"{name} 尚未实现：它属于任务 T03（{module_name.lstrip('.')}.py）"
        ) from exc
    value = getattr(module, attribute)
    globals()[name] = value  # 缓存，避免每次访问都走 import 机制
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    # T03 的稳定接口（惰性导入）
    "PlayerEngine",
    "PlayerSnapshot",
    "PlayerError",
    "PlayQueue",
    "QueueItem",
    "PlayMode",
    "PlayerState",
    "SignalPath",
    "SignalStage",
    "Counters",
    "PlaybackSession",
    "GainStage",
    "BiquadEq",
    # 枚举
    "StageStatus",
    # 事件名
    "EVENT_STATE",
    "EVENT_TRACK",
    "EVENT_POSITION",
    "EVENT_SIGNAL",
    "EVENT_ERROR",
    "EVENT_QUEUE",
    "EVENT_GAP",
    "STAGE_KEYS",
    # I/O 层（T02）
    "PcmRingBuffer",
    "FfmpegDecoder",
    "DecodeSpec",
    "DecodeError",
    "probe_codec",
    "AudioOutput",
    "OutputConfig",
    "DeviceInfo",
    "DeviceError",
    "DeviceBusyError",
    "DeviceLostError",
    "RateUnsupportedError",
    "NoOutputDeviceError",
    "list_output_devices",
    "pick_default_device",
    "pick_rate",
    "supports_rate",
]
