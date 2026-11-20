"""真开流用例的**有界重试**：只针对「设备刚被上一个用例放手、PortAudio 还没准备好」。

背景
----
本机只有一块物理输出设备。`test_player_output` / `test_player_exclusive` 里
真开流的用例 `stop()` 之后，PortAudio / 驱动侧释放设备有几十~几百毫秒滞后；
全量串行运行时，紧跟着的真开流用例会吃到::

    PortAudioError: Error opening OutputStream: Invalid device [PaErrorCode -9996]

单跑复现不了（单跑时设备空闲），所以它不是产品缺陷，而是测试之间的资源互斥。

原则（主理人明确要求）
----------------------
* **只重试 -9996**（含 ``Invalid device``）——其它错误码重试也不会变好，直接失败，
  不能把真 bug 吞成"重试两次就过"；
* 最多 **2 次**重试、每次退避 **200 ms**（有界，不做无限等待）；
* **每次重试都打日志**（WARNING，含"第 N 次重试"），不允许静默吞掉。
"""

from __future__ import annotations

import logging
import time

LOGGER = logging.getLogger("tests.device_retry")

RETRYABLE_CODE = -9996
MAX_RETRIES = 2
BACKOFF_SEC = 0.2
_BUSY_MARKERS = ("-9996", "Invalid device")


class DeviceBusyHint(RuntimeError):
    """给"只返回 bool"的上层 API 用（如 ``PlayerEngine.play()``）。

    这类 API 把 ``-9996`` 吞成 ``False``，用例只能从 ``snapshot()["error"]``
    里读到原文；读到设备忙时抛这个，交给统一的 ``with_device_retry`` 处理。
    """


def is_device_busy(exc: BaseException) -> bool:
    """判断异常是否属于「设备释放滞后」这一类（只认 -9996）。"""
    if isinstance(exc, DeviceBusyHint):
        return True
    if getattr(exc, "err", None) == RETRYABLE_CODE:
        return True
    text = f"{exc}"
    return any(marker in text for marker in _BUSY_MARKERS)


def with_device_retry(action, *, what: str, max_retries: int = MAX_RETRIES,
                      backoff: float = BACKOFF_SEC, sleep=time.sleep):
    """执行 ``action()``；只在设备忙时退避重试，用尽仍失败则抛最后一次异常。

    Args:
        action: 无参可调用体；成功返回值原样透传。
        what: 日志里的动作描述，例如 ``"WASAPI 独占 44.1k 开流"``。
        max_retries: 重试次数上限（默认 2 → 最多尝试 3 次）。
        backoff: 每次重试前的固定退避秒数（默认 0.2 s）。
        sleep: 便于测试替换的休眠函数。

    Returns:
        ``action()`` 的返回值。
    """
    total_attempts = max_retries + 1
    for attempt in range(1, total_attempts + 1):
        try:
            return action()
        except Exception as exc:  # noqa: BLE001 - 只按错误码分流，其余原样抛
            if attempt >= total_attempts or not is_device_busy(exc):
                raise
            LOGGER.warning(
                "%s：开流失败（%s），第 %d 次重试（%.0f ms 后）",
                what, exc, attempt, backoff * 1000,
            )
            sleep(backoff)
    raise AssertionError("unreachable: with_device_retry 循环应当已返回或抛出")
