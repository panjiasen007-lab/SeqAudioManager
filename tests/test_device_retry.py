"""``tests/device_retry.py`` 的单元自测：有界重试必须"有界、只认 -9996、每次打日志"。

为什么单测这个 helper：它被用来消掉全量跑里的设备争用 flake，如果不加约束地
重试，就会把真 bug（例如设备真的不可用）吞成"重试两次就过"。所以这里钉死三件事：

1. **只重试 -9996 / Invalid device**，其它 PortAudio 错误码一次都不重试；
2. **最多 2 次重试**（一共 3 次尝试），不是无限重试；
3. **每次重试都写 WARNING 日志**（含"第 N 次重试"），不允许静默。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from device_retry import (  # noqa: E402
    BACKOFF_SEC,
    DeviceBusyHint,
    is_device_busy,
    with_device_retry,
)


class FakePortAudioError(Exception):
    """模拟 ``sounddevice.PortAudioError``：带 ``err`` 错误码。"""

    def __init__(self, code: int):
        super().__init__(f"Error opening OutputStream: code [{code}]")
        self.err = code


class DeviceRetryTest(unittest.TestCase):
    def test_retries_then_succeeds(self) -> None:
        """前两次 -9996、第三次成功：一共尝试 3 次，退避 2 次。"""
        calls = {"n": 0}
        slept: list[float] = []

        def action():
            calls["n"] += 1
            if calls["n"] < 3:
                raise FakePortAudioError(-9996)
            return "ok"

        result = with_device_retry(action, what="开流", sleep=slept.append)

        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 3, "应有 3 次尝试（1 次原始 + 2 次重试）")
        self.assertEqual(slept, [BACKOFF_SEC, BACKOFF_SEC], "每次退避应为 200 ms")

    def test_gives_up_after_two_retries(self) -> None:
        """一直 -9996：最多 3 次尝试后抛出，绝不无限重试。"""
        calls = {"n": 0}
        slept: list[float] = []

        def action():
            calls["n"] += 1
            raise FakePortAudioError(-9996)

        with self.assertRaises(FakePortAudioError):
            with_device_retry(action, what="开流", sleep=slept.append)

        self.assertEqual(calls["n"], 3, f"越界重试：实际尝试 {calls['n']} 次")
        self.assertEqual(len(slept), 2, "退避次数应等于重试次数")

    def test_does_not_retry_other_error_codes(self) -> None:
        """其它错误码（如 -9997 采样率不支持）必须立刻失败，一次都不重试。"""
        for code in (-9997, -9985, -9999):
            with self.subTest(code=code):
                calls = {"n": 0}
                slept: list[float] = []

                def action():
                    calls["n"] += 1
                    raise FakePortAudioError(code)

                with self.assertRaises(FakePortAudioError):
                    with_device_retry(action, what="开流", sleep=slept.append)

                self.assertEqual(calls["n"], 1, f"错误码 {code} 不应重试")
                self.assertEqual(slept, [], f"错误码 {code} 不应退避")

    def test_logs_every_retry(self) -> None:
        """每次重试都必须有 WARNING 日志，且写明"第 N 次重试"。"""
        calls = {"n": 0}

        def action():
            calls["n"] += 1
            if calls["n"] < 3:
                raise FakePortAudioError(-9996)
            return True

        with self.assertLogs("tests.device_retry", level="WARNING") as logs:
            with_device_retry(action, what="WASAPI 独占开流", sleep=lambda _s: None)

        joined = "\n".join(logs.output)
        self.assertIn("第 1 次重试", joined, f"缺少第 1 次重试日志：{joined}")
        self.assertIn("第 2 次重试", joined, f"缺少第 2 次重试日志：{joined}")
        self.assertIn("WASAPI 独占开流", joined, "日志里应能看出是哪个动作重试了")

    def test_device_busy_hint_is_retryable(self) -> None:
        """``engine.play()`` 只回 bool：用 DeviceBusyHint 表达 -9996，也应重试。"""
        calls = {"n": 0}

        def action():
            calls["n"] += 1
            if calls["n"] == 1:
                raise DeviceBusyHint("该设备不支持独占模式…[-9996]")
            return "playing"

        self.assertEqual(
            with_device_retry(action, what="引擎起播", sleep=lambda _s: None), "playing"
        )
        self.assertEqual(calls["n"], 2, "DeviceBusyHint 应触发一次重试")

    def test_is_device_busy_classification(self) -> None:
        """错误分类：-9996 / Invalid device / DeviceBusyHint 为真，其余为假。"""
        self.assertTrue(is_device_busy(FakePortAudioError(-9996)))
        self.assertTrue(is_device_busy(DeviceBusyHint("x")))
        self.assertTrue(is_device_busy(RuntimeError("Invalid device")))
        self.assertFalse(is_device_busy(FakePortAudioError(-9997)))
        self.assertFalse(is_device_busy(RuntimeError("文件不存在")))
        self.assertFalse(is_device_busy(ValueError("unrelated")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
