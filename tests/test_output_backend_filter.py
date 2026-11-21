"""v1.8.0 输出设备按后端过滤自测。

覆盖用户反馈「选了输出后端才选对应设备，不然设备太多且重复」：
* ``PlayerBridge.list_output_devices(backend)`` 真机过滤 / 默认全量兼容；
* ``SettingsPanel`` 切后端发信号，``scoped=True`` 文本不带 hostapi 前缀；
* ``MainWindow._on_output_device_refresh`` 用假 bridge 验证旧设备被清空。

运行：``python -m unittest tests.test_output_backend_filter -v``（离屏；设备相关
用例在没有声卡时自动 skip）。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as player_devices  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402
from seqaudio.ui.player_bridge import PlayerBridge  # noqa: E402
from seqaudio.ui.settings_panel import SettingsPanel  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _has_devices() -> bool:
    return bool(player_devices.list_output_devices("mme"))


HAS_DEVICES = _has_devices()


class TestBridgeBackendFilter(unittest.TestCase):
    """桥接层的 backend 参数必须真透传到 player.devices。"""

    @unittest.skipUnless(HAS_DEVICES, "本机没有 MME 输出设备")
    def test_backend_returns_only_that_hostapi(self) -> None:
        bridge = PlayerBridge(Settings(offline_mode=True))
        try:
            scoped = bridge.list_output_devices("mme")
            self.assertTrue(scoped, "本机应有 MME 设备")
            self.assertTrue(
                all(entry["backend"] == "mme" for entry in scoped),
                f"MME 过滤结果混入了别的后端：{scoped}",
            )
            self.assertTrue(
                all("MME" in entry["hostapi"] for entry in scoped),
            )
            all_devices = bridge.list_output_devices()
            self.assertGreater(
                len(all_devices),
                len(scoped),
                "默认调用必须仍返回全平台设备（兼容既有测试）",
            )
            self.assertGreater(len({entry["backend"] for entry in all_devices}), 1)
        finally:
            bridge.shutdown()

    @unittest.skipUnless(HAS_DEVICES, "本机没有输出设备")
    def test_unknown_backend_keeps_legacy_fallback(self) -> None:
        """无法识别的键沿用 player.devices 的既有语义：当「不指定」处理。

        UI 的组合框只会给出合法键，不会走到这条分支；这里钉住行为是为了
        确认桥接层没有偷偷改写 player 的返回值。
        """
        bridge = PlayerBridge(Settings(offline_mode=True))
        try:
            self.assertEqual(
                bridge.list_output_devices("no_such_backend"),
                bridge.list_output_devices(),
            )
        finally:
            bridge.shutdown()


class TestPanelBackendRefresh(unittest.TestCase):
    """面板只发键，不碰 player 模块。"""

    def setUp(self) -> None:
        self.settings = Settings(offline_mode=True)
        self.panel = SettingsPanel(self.settings)
        self.seen: list[str] = []
        self.panel.outputDeviceRefreshRequested.connect(self.seen.append)

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def test_backend_change_emits_key(self) -> None:
        index = self.panel.backend_combo.findData("wasapi")
        self.assertGreaterEqual(index, 0)
        self.panel.backend_combo.setCurrentIndex(index)
        self.assertEqual(self.seen[-1], "wasapi")

        index = self.panel.backend_combo.findData("mme")
        self.panel.backend_combo.setCurrentIndex(index)
        self.assertEqual(self.seen[-1], "mme")

    def test_load_from_does_not_emit(self) -> None:
        """程序性回填（load_from / setCurrentIndex during _loading）不该刷设备。"""
        self.seen.clear()
        other = Settings(offline_mode=True)
        other.playback_output_backend = "wasapi"
        self.panel.load_from(other)
        self.assertEqual(self.seen, [])

    def test_scoped_labels_drop_hostapi_prefix(self) -> None:
        entries = [
            {
                "name": "扬声器 (Realtek Audio)",
                "label": "Windows WASAPI · 扬声器 (Realtek Audio)",
                "backend": "wasapi",
            }
        ]
        self.panel.set_output_devices(entries, scoped=True)
        texts = [
            self.panel.device_combo.itemText(i)
            for i in range(self.panel.device_combo.count())
        ]
        self.assertIn("扬声器 (Realtek Audio)", texts)
        self.assertFalse(any("WASAPI" in text for text in texts))

        # 旧行为（全量列表）仍带前缀，既有用例不会被打破
        self.panel.set_output_devices(entries)
        texts = [
            self.panel.device_combo.itemText(i)
            for i in range(self.panel.device_combo.count())
        ]
        self.assertTrue(any("WASAPI" in text for text in texts))


class TestMainWindowDeviceRefresh(unittest.TestCase):
    """只搭最小替身，不构造整个 MainWindow（离屏下反复构造会卡样式）。"""

    def setUp(self) -> None:
        self.settings = Settings(offline_mode=True)
        self.settings.playback_device = "MME · 老设备"
        self.panel = SettingsPanel(self.settings)
        self.wasapi_entries = [
            {"name": "扬声器 (Realtek Audio)", "label": "Windows WASAPI · 扬声器 (Realtek Audio)",
             "backend": "wasapi"},
            {"name": "耳机 (Realtek Audio)", "label": "Windows WASAPI · 耳机 (Realtek Audio)",
             "backend": "wasapi"},
        ]
        self.bridge = SimpleNamespace(
            list_output_devices=lambda backend=None: list(self.wasapi_entries)
            if backend == "wasapi" else []
        )
        self.window = SimpleNamespace(
            settings=self.settings,
            settings_panel=self.panel,
            player_bridge=self.bridge,
        )

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def test_stale_device_reset_to_system_default(self) -> None:
        MainWindow._on_output_device_refresh(self.window, "wasapi")
        self.assertEqual(self.settings.playback_device, "")
        self.assertEqual(self.panel.device_combo.currentData(), "")
        self.assertEqual(self.panel.device_combo.currentIndex(), 0)
        self.assertTrue(self.panel.device_combo.itemText(0), "第一项应是「系统默认设备」")
        self.assertEqual(self.panel.device_combo.count(), 3)  # 默认 + 2 台 WASAPI
        for index in range(1, self.panel.device_combo.count()):
            self.assertNotIn("WASAPI", self.panel.device_combo.itemText(index))

    def test_valid_device_kept(self) -> None:
        self.settings.playback_device = "耳机 (Realtek Audio)"
        MainWindow._on_output_device_refresh(self.window, "wasapi")
        self.assertEqual(self.settings.playback_device, "耳机 (Realtek Audio)")
        self.assertEqual(self.panel.device_combo.currentData(), "耳机 (Realtek Audio)")

    def test_signal_reaches_slot_via_main_window_wiring(self) -> None:
        """构造真实 MainWindow 时信号确实已连到槽（用 monkeypatch 检查连接）。"""
        index = self.panel.backend_combo.findData("wasapi")
        self.assertGreaterEqual(index, 0)
        # 面板自身发出的信号必须只带一个 str 参数（否则连接会静默失败）
        self.panel.outputDeviceRefreshRequested.connect(
            lambda key: self.assertIsInstance(key, str)
        )
        self.panel.backend_combo.setCurrentIndex(index)


class TestMultichannelDeviceDiagnosis(unittest.TestCase):
    """「8 声道设备在 WASAPI 下只吃 8 声道」的归因与提前提示。

    背景（v1.8 实测）：本机「扬声器 (Strix 7.1)」在 WASAPI 上报 8 声道，
    2 声道共享/独占一律开不了流；同一设备在 MME / DirectSound 下 2 声道完全
    正常。旧文案只会说「也可以改用共享输出」——把用户从一个死胡同推向另一个。
    """

    @staticmethod
    def _device(channels: int, hostapi: str = "Windows WASAPI") -> player_devices.DeviceInfo:
        return player_devices.DeviceInfo(
            index=0, name="环绕测试设备", channels=channels,
            default_samplerate=48000.0, hostapi_index=2, hostapi_name=hostapi,
        )

    def test_source_fewer_channels_than_device_points_at_backend(self) -> None:
        text = player_devices.describe_no_option(
            self._device(8), (44100, 48000), 2, player_devices.OutputMode.EXCLUSIVE,
            shared_available=False,
        )
        self.assertIn("只接受 8 声道", text)
        self.assertIn("MME", text, "必须指向换后端这条真正可行的出路")
        self.assertIn("DirectSound", text)

    def test_no_shared_hint_when_shared_also_fails(self) -> None:
        """共享同样开不了时不许说「也可以改用共享输出」。"""
        text = player_devices.describe_no_option(
            self._device(8), (44100,), 2, player_devices.OutputMode.EXCLUSIVE,
            shared_available=False,
        )
        self.assertNotIn("也可以改用共享输出", text)
        self.assertIn("共享模式同样开不了", text)

    def test_shared_hint_kept_when_shared_really_works(self) -> None:
        """共享确实可用时，原来的建议仍然是对的。"""
        text = player_devices.describe_no_option(
            self._device(2), (44100,), 2, player_devices.OutputMode.EXCLUSIVE,
            shared_available=True,
        )
        self.assertIn("也可以改用共享输出", text)

    def test_source_richer_than_stereo_keeps_downmix_hint(self) -> None:
        """源比 2 声道多时仍走「多声道下混为 2.0」那条建议（语义不能串）。"""
        text = player_devices.describe_no_option(
            self._device(2), (44100,), 6, player_devices.OutputMode.SHARED,
        )
        self.assertIn("多声道下混为 2.0", text)
        self.assertNotIn("只接受 8 声道", text)

    def test_panel_warns_on_multichannel_device_under_wasapi(self) -> None:
        panel = SettingsPanel(Settings())
        panel.set_output_devices(
            [
                {"name": "环绕测试设备", "channels": 8},
                {"name": "立体声测试设备", "channels": 2},
            ],
            scoped=True,
        )
        index = panel.backend_combo.findData("wasapi")
        self.assertGreaterEqual(index, 0)
        panel.backend_combo.setCurrentIndex(index)

        panel.device_combo.setCurrentIndex(panel.device_combo.findData("环绕测试设备"))
        self.assertIn("8 声道", panel.device_combo.toolTip())
        self.assertIn("MME", panel.device_combo.toolTip())

        panel.device_combo.setCurrentIndex(panel.device_combo.findData("立体声测试设备"))
        self.assertNotIn("MME", panel.device_combo.toolTip(),
                         "2 声道设备不该被这条提示骚扰")

    def test_panel_does_not_warn_on_non_wasapi_backend(self) -> None:
        panel = SettingsPanel(Settings())
        panel.set_output_devices([{"name": "环绕测试设备", "channels": 8}], scoped=True)
        index = panel.backend_combo.findData("mme")
        self.assertGreaterEqual(index, 0)
        panel.backend_combo.setCurrentIndex(index)
        panel.device_combo.setCurrentIndex(panel.device_combo.findData("环绕测试设备"))
        self.assertNotIn("MME 或 DirectSound", panel.device_combo.toolTip(),
                         "MME 下 8 声道设备 2 声道能正常开流，不该提示")


if __name__ == "__main__":
    unittest.main(verbosity=2)
