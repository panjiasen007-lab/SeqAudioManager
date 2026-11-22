"""设备热插拔探测器（v1.8.1 P0-1）自测。

三个必须钉死的性质：

1. **只报一次**：同一份设备表连查两次，第二次必须没有变化 —— 否则 3 s 一次
   的轮询会把界面刷成"一直在变"（用户看到下拉菜单每秒重建）。
2. **比对键是设备名集合**：启用 ASIO 后 PortAudio 的全局索引整体后移，按索引
   比会得到"每次都变了"的假阳性。用例里专门换了索引但名字不变来钉这条。
3. **只枚举、不试开**：探测路径不许碰 ``sounddevice()``（那会 dlopen + 开流）。

运行：``python -m unittest tests.test_device_watch -v``（离屏；不依赖真实声卡）
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402
from seqaudio.ui.settings_panel import SettingsPanel  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _entries(names, default: str = "", start_index: int = 0) -> list[dict[str, Any]]:
    """伪造桥接层投影出来的设备表（``list_output_devices`` 的字典形态）。"""
    return [
        {
            "name": name,
            "is_default": bool(default) and name == default,
            "index": start_index + offset,
            "label": f"MME · {name}",
            "backend": "mme",
        }
        for offset, name in enumerate(names)
    ]


class _ScriptedEnumerator:
    """按脚本依次返回设备表的假枚举器（记录收到的 backend 与调用次数）。"""

    def __init__(self, *frames: list[dict[str, Any]]) -> None:
        self._frames = list(frames)
        self.calls: list[str] = []

    def __call__(self, backend: str) -> list[dict[str, Any]]:
        self.calls.append(str(backend))
        if not self._frames:
            return []
        return self._frames.pop(0) if len(self._frames) > 1 else self._frames[0]


class TestDeviceWatcher(unittest.TestCase):
    """``player.devices.DeviceWatcher`` 的纯逻辑（不碰 Qt、不碰声卡）。"""

    def test_first_poll_establishes_baseline_without_reporting(self) -> None:
        states = _ScriptedEnumerator(_entries(["扬声器", "耳机"], default="扬声器"))
        watcher = dev_mod.DeviceWatcher("mme", enumerator=states)
        self.assertFalse(watcher.primed())

        change = watcher.poll_once()
        self.assertFalse(change.changed, "首查只建立基线，不该报变化")
        self.assertTrue(watcher.primed())
        self.assertEqual(watcher.names(), frozenset({"扬声器", "耳机"}))
        self.assertEqual(states.calls, ["mme"], "枚举器必须收到当前后端键")

    def test_identical_poll_reports_once_then_silent(self) -> None:
        """同一份设备表连查两次：第二次必须静默（PRD「只发 1 次刷新」）。"""
        states = _ScriptedEnumerator(
            _entries(["扬声器"]),
            _entries(["扬声器", "USB DAC"]),
            _entries(["扬声器", "USB DAC"]),
        )
        watcher = dev_mod.DeviceWatcher("mme", enumerator=states)

        self.assertFalse(watcher.poll_once().changed)          # 基线
        added = watcher.poll_once()
        self.assertEqual(added.added, ("USB DAC",))
        self.assertTrue(added.changed)
        self.assertFalse(watcher.poll_once().changed, "同一状态不许重复报")

    def test_removed_device(self) -> None:
        states = _ScriptedEnumerator(
            _entries(["扬声器", "USB DAC"]),
            _entries(["扬声器"]),
        )
        watcher = dev_mod.DeviceWatcher("mme", enumerator=states)
        watcher.poll_once()
        change = watcher.poll_once()
        self.assertEqual(change.removed, ("USB DAC",))
        self.assertEqual(change.added, ())
        self.assertTrue(change.changed)

    def test_add_and_remove_in_one_poll(self) -> None:
        states = _ScriptedEnumerator(
            _entries(["扬声器", "老解码器"]),
            _entries(["扬声器", "新解码器"]),
        )
        watcher = dev_mod.DeviceWatcher("mme", enumerator=states)
        watcher.poll_once()
        change = watcher.poll_once()
        self.assertEqual(change.added, ("新解码器",))
        self.assertEqual(change.removed, ("老解码器",))
        self.assertTrue(change.to_dict()["changed"])

    def test_index_shift_is_not_a_change(self) -> None:
        """启用 ASIO 后索引整体后移：名字不变就不能报变化。"""
        states = _ScriptedEnumerator(
            _entries(["扬声器", "耳机"], start_index=5),
            _entries(["扬声器", "耳机"], start_index=23),
        )
        watcher = dev_mod.DeviceWatcher("mme", enumerator=states)
        watcher.poll_once()
        self.assertFalse(watcher.poll_once().changed, "索引变化不是设备表变化")

    def test_default_device_change_is_reported(self) -> None:
        states = _ScriptedEnumerator(
            _entries(["扬声器", "耳机"], default="扬声器"),
            _entries(["扬声器", "耳机"], default="耳机"),
        )
        watcher = dev_mod.DeviceWatcher("mme", enumerator=states)
        watcher.poll_once()
        change = watcher.poll_once()
        self.assertTrue(change.default_changed)
        self.assertTrue(change.changed)
        self.assertEqual(change.added, ())
        self.assertEqual(change.removed, ())

    def test_lost_default_flag_does_not_flicker(self) -> None:
        """默认设备标记短暂消失（枚举中间态）不该被当成"默认设备变了"。"""
        states = _ScriptedEnumerator(
            _entries(["扬声器"], default="扬声器"),
            _entries(["扬声器"]),                       # 这一拍没有默认标记
            _entries(["扬声器"], default="扬声器"),
        )
        watcher = dev_mod.DeviceWatcher("mme", enumerator=states)
        watcher.poll_once()
        self.assertFalse(watcher.poll_once().changed)
        self.assertFalse(watcher.poll_once().changed)

    def test_reset_rebuilds_baseline(self) -> None:
        states = _ScriptedEnumerator(
            _entries(["扬声器"]),
            _entries(["ASIO 驱动"]),
        )
        watcher = dev_mod.DeviceWatcher("mme", enumerator=states)
        watcher.poll_once()
        watcher.reset("asio")
        self.assertEqual(watcher.backend(), "asio")
        self.assertFalse(watcher.primed())
        self.assertFalse(watcher.poll_once().changed, "换后端后首查不报变化")
        self.assertEqual(watcher.names(), frozenset({"ASIO 驱动"}))
        self.assertEqual(states.calls[-1], "asio")

    def test_enumerator_failure_keeps_baseline(self) -> None:
        """枚举抛异常时保留上次基线（否则一次抖动会被当成"设备全被拔了"）。"""

        def boom(_backend: str):
            raise RuntimeError("PortAudio 未就绪")

        watcher = dev_mod.DeviceWatcher("mme", enumerator=lambda b: _entries(["扬声器"]))
        watcher.poll_once()
        watcher._enumerate = boom  # noqa: SLF001 - 刻意注入故障
        change = watcher.poll_once()
        self.assertFalse(change.changed)
        self.assertEqual(watcher.names(), frozenset({"扬声器"}))

    def test_accepts_device_info_objects(self) -> None:
        """默认枚举器给的是 ``DeviceInfo``，注入的给字典 —— 两种都要认。"""
        frames = [
            [
                dev_mod.DeviceInfo(
                    index=3, name="扬声器", channels=2, default_samplerate=48000.0,
                    is_default=True, hostapi_index=1, hostapi_name="MME",
                )
            ],
            [
                dev_mod.DeviceInfo(
                    index=9, name="扬声器", channels=2, default_samplerate=48000.0,
                    is_default=True, hostapi_index=3, hostapi_name="MME",
                ),
                dev_mod.DeviceInfo(
                    index=11, name="耳机", channels=2, default_samplerate=48000.0,
                    hostapi_index=3, hostapi_name="MME",
                ),
            ],
        ]
        state = {"index": 0}

        def enumerator(_backend: str):
            return frames[min(state["index"], len(frames) - 1)]

        watcher = dev_mod.DeviceWatcher("mme", enumerator=enumerator)
        watcher.poll_once()
        state["index"] = 1
        change = watcher.poll_once()
        self.assertEqual(change.added, ("耳机",))
        self.assertFalse(change.default_changed, "默认设备还是「扬声器」")

    def test_probe_never_touches_sounddevice(self) -> None:
        """探测只走注入的枚举器：绝不允许在这里 dlopen PortAudio 或开流。"""
        original = dev_mod.sounddevice
        dev_mod.sounddevice = lambda: (_ for _ in ()).throw(
            AssertionError("热插拔探测不许碰 sounddevice()")
        )
        try:
            watcher = dev_mod.DeviceWatcher(
                "wasapi", enumerator=_ScriptedEnumerator(_entries(["扬声器"]))
            )
            watcher.poll_once()
            watcher.poll_once()
        finally:
            dev_mod.sounddevice = original


class _FakeBridge:
    """最小桥接替身：只提供被刷新的那几个方法。"""

    def __init__(self, entries_by_backend: dict[str, list[dict[str, Any]]]) -> None:
        self._entries = entries_by_backend
        self.list_calls: list[Any] = []
        self.refresh_calls = 0
        self.asio_calls = 0
        #: DoP 能力查询：默认"能用"，用例可改成 ``(False, 原因)``
        self.dop_calls: list[int] = []
        self.dop_result: tuple[bool, str] = (True, "")
        self.snapshot: dict[str, Any] = {"signal_path": [], "counters": {}}

    def list_output_devices(self, backend=None):
        self.list_calls.append(backend)
        return list(self._entries.get(str(backend or ""), []))

    def device_watch(self, backend=None):
        return self.list_output_devices(backend)

    def refresh_signal(self) -> None:
        self.refresh_calls += 1

    def asio_present(self, refresh: bool = False) -> bool:
        self.asio_calls += 1
        return False

    def dop_capability(self, channels: int = 2) -> tuple[bool, str]:
        self.dop_calls.append(int(channels))
        return self.dop_result

    def poll_now(self) -> dict[str, Any]:
        return dict(self.snapshot)


class _FakeSignals:
    """信号面板替身：只记 update_from 被喂了几次快照。"""

    def __init__(self) -> None:
        self.updates: list[Any] = []

    def update_from(self, snapshot) -> None:
        self.updates.append(snapshot)


class _FakeWindow:
    """只混入被测方法，不构造整个 ``MainWindow``（离屏反复构造会卡样式）。"""

    _active_backend = MainWindow._active_backend
    _refresh_signal_panel = MainWindow._refresh_signal_panel
    # v1.8.1：刷新入口同时驱动 DSD 徽标/音量滑块；本替身无 player_bar/dsd_label，
    # _update_dsd_ui 内部按缺失跳过。
    _update_dsd_ui = MainWindow._update_dsd_ui
    _refresh_device_status = MainWindow._refresh_device_status
    _refresh_asio_state = MainWindow._refresh_asio_state
    _refresh_dop_state = MainWindow._refresh_dop_state
    _current_source_channels = MainWindow._current_source_channels
    _asio_present = MainWindow._asio_present
    _poll_device_change = MainWindow._poll_device_change
    _on_device_lost = MainWindow._on_device_lost

    def __init__(self, settings: Settings, panel: SettingsPanel, bridge: _FakeBridge,
                 watcher: dev_mod.DeviceWatcher) -> None:
        self.settings = settings
        self.settings_panel = panel
        self.player_bridge = bridge
        self.player_signals = _FakeSignals()
        self._device_watcher = watcher
        self._device_lost_notified: set[str] = set()
        self.status_hint = QLabel("")
        self.device_label = QLabel("")
        self.logs: list[tuple[str, str]] = []

    def _append_log(self, level: str, message: str) -> None:
        self.logs.append((level, str(message)))


class TestMainWindowDevicePolling(unittest.TestCase):
    """主窗口侧：变化时三处一起刷新、无变化时不打扰、选中项回落但不丢设置。"""

    def _build(self, first, second) -> _FakeWindow:
        settings = Settings(offline_mode=True)
        settings.playback_output_backend = "mme"
        settings.playback_device = "USB DAC"
        panel = SettingsPanel(settings)
        panel.set_output_devices(first, scoped=True)
        self.addCleanup(panel.deleteLater)
        bridge = _FakeBridge({"mme": second})
        watcher = dev_mod.DeviceWatcher(
            "mme", enumerator=_ScriptedEnumerator(first, second, second)
        )
        window = _FakeWindow(settings, panel, bridge, watcher)
        window._poll_device_change()  # 首查 = 建立基线（不报变化）
        return window

    def test_change_refreshes_dropdown_signal_and_status(self) -> None:
        first = _entries(["USB DAC", "扬声器"], default="扬声器")
        second = _entries(["扬声器"], default="扬声器")   # USB DAC 被拔
        window = self._build(first, second)
        self.assertEqual(window.logs, [], "首查不该刷界面")

        window._poll_device_change()

        # 1) 下拉已重建，且选中项回落到第 0 项「系统默认设备」
        combo = window.settings_panel.device_combo
        self.assertEqual(combo.count(), 2)
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(combo.currentData(), "")
        self.assertNotIn("USB DAC", [combo.itemText(i) for i in range(combo.count())])
        # 2) settings.playback_device **保留旧值**（插回来就不用重选）
        self.assertEqual(window.settings.playback_device, "USB DAC")
        # 3) 信号路径被重算并立刻取到新快照
        self.assertEqual(window.player_bridge.refresh_calls, 1)
        self.assertEqual(len(window.player_signals.updates), 1)
        # 4) 状态栏跟下拉同源（三处一致），配置值只在 tooltip 里留痕
        self.assertEqual(window.status_hint.text(), "")
        self.assertIn("系统默认设备", window.device_label.text())
        self.assertIn("USB DAC", window.device_label.toolTip())
        self.assertTrue(
            any("设备表已变化" in message for _, message in window.logs),
            f"应记录一次设备表变化：{window.logs}",
        )

    def test_no_change_does_not_touch_panel(self) -> None:
        first = _entries(["USB DAC", "扬声器"], default="扬声器")
        window = self._build(first, first)
        calls: list[Any] = []
        original = window.settings_panel.set_output_devices
        window.settings_panel.set_output_devices = lambda *a, **k: calls.append(a)

        window._poll_device_change()
        window._poll_device_change()
        window.settings_panel.set_output_devices = original
        self.assertEqual(calls, [], "设备表没变就不许重建下拉（会打断用户选择）")
        self.assertEqual(window.player_bridge.refresh_calls, 0)
        self.assertEqual(window.logs, [])

    def test_device_lost_notice_only_once_and_falls_back(self) -> None:
        first = _entries(["USB DAC", "扬声器"], default="扬声器")
        window = self._build(first, first)
        self.assertEqual(window.status_hint.text(), "")

        window._on_device_lost("USB DAC")
        notice = window.status_hint.text()
        self.assertIn("已断开", notice)
        # 保留旧值：拔掉往往只是几秒钟的事，清空等于把用户的选择一起丢掉
        self.assertEqual(window.settings.playback_device, "USB DAC")
        combo = window.settings_panel.device_combo
        self.assertEqual(combo.currentIndex(), 0)
        self.assertEqual(
            sum(1 for level, _ in window.logs if level == "WARNING"), 1
        )

        # 同一台设备再报一次：只提示一次（PRD P0-1 ②）
        window._on_device_lost("USB DAC")
        self.assertEqual(
            sum(1 for level, _ in window.logs if level == "WARNING"), 1,
            "同一设备只提示 1 次",
        )

    def test_replugged_device_can_notify_again(self) -> None:
        first = _entries(["扬声器"], default="扬声器")
        second = _entries(["扬声器", "USB DAC"], default="扬声器")
        window = self._build(first, second)
        window._on_device_lost("USB DAC")
        self.assertIn("USB DAC", window._device_lost_notified)

        window._poll_device_change()   # 设备插回来了
        self.assertNotIn(
            "USB DAC", window._device_lost_notified,
            "设备重新出现后，下次被拔必须能再提示一次",
        )


class TestDopSwitchWiring(unittest.TestCase):
    """DSD 开关必须真的被点亮/灰掉（v1.8.1 收口：`set_dop_available()` 曾无调用者）。

    这条线的价值不在"调用一次函数"，而在于：``dop_check`` 初始就是
    ``setEnabled(False)``，没人调就等于发出去的版本里开关永久灰着 —— T04 的
    用户价值（可开关 + 可观察）直接归零。所以用例断言的是**勾选框的可用性**，
    不是"函数被调过"。
    """

    def _build(self, first, second, result=(True, "")) -> _FakeWindow:
        settings = Settings(offline_mode=True)
        settings.playback_output_backend = "mme"
        settings.playback_device = "USB DAC"
        panel = SettingsPanel(settings)
        panel.set_output_devices(first, scoped=True)
        self.addCleanup(panel.deleteLater)
        bridge = _FakeBridge({"mme": second})
        bridge.dop_result = result
        watcher = dev_mod.DeviceWatcher(
            "mme", enumerator=_ScriptedEnumerator(first, second, second)
        )
        window = _FakeWindow(settings, panel, bridge, watcher)
        window._poll_device_change()   # 首查 = 建立基线（不报变化）
        return window

    def test_device_change_enables_the_switch(self) -> None:
        first = _entries(["USB DAC", "扬声器"], default="扬声器")
        second = _entries(["USB DAC"], default="USB DAC")
        window = self._build(first, second)
        self.assertFalse(window.settings_panel.dop_check.isEnabled(), "初始必须灰着")

        window._poll_device_change()   # 设备表变了 → 重新问一次 DoP 能力

        self.assertTrue(window.settings_panel.dop_check.isEnabled(), "能力可用时开关要点亮")
        self.assertNotIn("回退", window.settings_panel.dop_check.toolTip())
        self.assertTrue(window.player_bridge.dop_calls, "应当真的问过引擎")

    def test_unavailable_reason_lands_in_the_tooltip(self) -> None:
        first = _entries(["USB DAC"], default="USB DAC")
        second = _entries(["USB DAC", "扬声器"], default="USB DAC")
        reason = "设备打不开 176.4 kHz 载体（DoP 需要 ≥176.4 kHz）"
        window = self._build(first, second, result=(False, reason))

        window._poll_device_change()

        self.assertFalse(window.settings_panel.dop_check.isEnabled())
        self.assertIn("176.4", window.settings_panel.dop_check.toolTip(),
                      "置灰必须说明原因，不能让用户对着灰框猜")

    def test_device_lost_greys_the_switch(self) -> None:
        first = _entries(["USB DAC"], default="USB DAC")
        window = self._build(first, first, result=(True, ""))
        window._on_device_lost("USB DAC")          # 能力还在的那一次
        self.assertTrue(window.settings_panel.dop_check.isEnabled())

        window.player_bridge.dop_result = (False, "没有可用的输出设备")
        window._device_lost_notified.clear()
        window._on_device_lost("扬声器")           # 再拔一次：能力没了
        self.assertFalse(window.settings_panel.dop_check.isEnabled())

    def test_probe_channels_follow_the_source(self) -> None:
        """声道数按当前源猜（面板渲染时没有文件头），并跟随「下混为 2.0」。"""
        first = _entries(["USB DAC"], default="USB DAC")
        window = self._build(first, first)
        window.player_bridge.snapshot = {"channels": 6, "signal_path": [], "counters": {}}
        window._refresh_dop_state()
        self.assertEqual(window.player_bridge.dop_calls[-1], 6)

        window.settings.playback_downmix_stereo = True
        window._refresh_dop_state()
        self.assertEqual(window.player_bridge.dop_calls[-1], 2, "下混后按 2 声道问")

class TestDopCapabilitySignal(unittest.TestCase):
    """面板必须在「载入 / 换后端 / 换设备」三个时机各问一次 DoP 能力。

    面板自己不能问（它没有 player，也不该有 —— UI 层拿到引擎就会有人顺手开流），
    所以它只发 ``dopCapabilityRefreshRequested``，由主窗口用
    ``bridge.dop_capability()`` 回答。这里注入一个**假 provider**，证明
    "信号 → set_dop_available → 勾选框 enabled/tooltip" 这条链路真的通，
    全程不碰声卡。
    """

    def setUp(self) -> None:
        self.settings = Settings(offline_mode=True)
        self.panel = SettingsPanel(self.settings)
        self.addCleanup(self.panel.deleteLater)
        self.shots: list[int] = []
        # 假 provider：返回值由用例控制，替代 bridge.dop_capability()
        self.result: tuple[bool, str] = (True, "")
        self.panel.dopCapabilityRefreshRequested.connect(self._answer)

    def _answer(self) -> None:
        self.shots.append(1)
        self.panel.set_dop_available(*self.result)

    def _devices(self) -> None:
        self.panel.set_output_devices(
            _entries(["USB DAC", "扬声器"], default="扬声器"), scoped=True
        )

    def test_load_from_asks_once(self) -> None:
        self.assertFalse(self.panel.dop_check.isEnabled(), "初始必须灰着")
        self.panel.load_from(self.settings)
        self.assertEqual(len(self.shots), 1, "载入后必须问一次")
        self.assertTrue(self.panel.dop_check.isEnabled())

    def test_backend_change_asks_once(self) -> None:
        self.panel.load_from(self.settings)
        self.shots.clear()
        index = self.panel.backend_combo.findData("directsound")
        self.panel.backend_combo.setCurrentIndex(index)
        self.assertEqual(len(self.shots), 1, "换后端后必须问一次")

    def test_device_change_asks_once(self) -> None:
        self._devices()
        self.panel.load_from(self.settings)
        self.shots.clear()
        self.panel.device_combo.setCurrentIndex(1)
        self.assertEqual(len(self.shots), 1, "换设备后必须问一次")

    def test_switch_follows_the_answer(self) -> None:
        self.panel.load_from(self.settings)
        self.assertTrue(self.panel.dop_check.isEnabled(), "能力可用时点亮")
        self.assertNotIn("回退", self.panel.dop_check.toolTip())

        self.result = (False, "设备打不开 176.4 kHz 载体（DoP 需要 ≥176.4 kHz）")
        self.panel.backend_combo.setCurrentIndex(
            self.panel.backend_combo.findData("wasapi")
        )
        self.assertFalse(self.panel.dop_check.isEnabled(), "能力没了必须置灰")
        self.assertIn("176.4", self.panel.dop_check.toolTip(), "原因要贴进 tooltip")
        # "不要置灰整张卡"：开关本身仍在原位可见，只是不可勾 + 带原因；
        # 能力矩阵那块是否显示由后端/模式的老逻辑决定，不归这条管。
        self.assertFalse(self.panel.dop_check.isHidden(), "开关本身不许被藏起来")


if __name__ == "__main__":
    unittest.main(verbosity=2)
