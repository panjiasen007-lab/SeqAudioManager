"""v1.7 设置面板独占相关自测（T04）。

运行：``python tests/test_player_exclusive_ui.py``（离屏，不需要显示器与声卡）。

覆盖设计 §9.2 的交互规则：
* 三值模式互斥，选严格自动勾整数通路并把该勾选置灰（附原因）；
* 后端非 WASAPI 时独占/严格置灰；
* 「独占失败时」三选一落到 ``playback_exclusive_fallback``；
* ``load_from`` / ``apply_to`` 往返一致，``playback_exclusive`` 与
  ``playback_output_mode`` 双向同步不打架；
* 能力矩阵在选中独占后展开，且与 ``devices`` 实测结果一致；
* v1.6 的 ``exclusive_check.setChecked(True)`` 仍等价于「宽松独占」。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as player_devices  # noqa: E402
from seqaudio.player.constants import OutputMode  # noqa: E402
from seqaudio.ui.settings_panel import SettingsPanel  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class _Panel(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings()
        self.panel = SettingsPanel(self.settings)
        # 设备下拉由主窗口/桥接灌入（面板本身不 import player），测试里自己灌一份
        wasapi = player_devices.list_output_devices("wasapi")
        self.panel.set_output_devices([item.to_dict() for item in wasapi])
        self.panel.load_from(self.settings)

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def _select_wasapi(self) -> None:
        index = self.panel.backend_combo.findData("wasapi")
        self.assertGreaterEqual(index, 0)
        self.panel.backend_combo.setCurrentIndex(index)


class TestThreeValueMode(_Panel):
    def test_radios_are_exclusive(self) -> None:
        self._select_wasapi()
        self.panel.mode_exclusive.setChecked(True)
        self.assertTrue(self.panel.mode_exclusive.isChecked())
        self.assertFalse(self.panel.mode_shared.isChecked())
        self.panel.mode_strict.setChecked(True)
        self.assertFalse(self.panel.mode_exclusive.isChecked(), "三选一必须互斥")
        self.assertIs(self.panel._current_output_mode(), OutputMode.EXCLUSIVE_STRICT)

    def test_strict_forces_int_pipeline(self) -> None:
        self._select_wasapi()
        self.panel.int_pipeline_check.setChecked(False)
        self.panel.mode_strict.setChecked(True)
        self.assertTrue(self.panel.int_pipeline_check.isChecked(), "严格必须自动勾整数通路")
        self.assertFalse(self.panel.int_pipeline_check.isEnabled(), "严格下整数通路应锁定")
        # 退回宽松后应恢复可编辑
        self.panel.mode_exclusive.setChecked(True)
        self.assertTrue(self.panel.int_pipeline_check.isEnabled())

    def test_non_wasapi_disables_exclusive(self) -> None:
        index = self.panel.backend_combo.findData("mme")
        self.assertGreaterEqual(index, 0)
        self.panel.backend_combo.setCurrentIndex(index)
        self.assertFalse(self.panel.mode_exclusive.isEnabled())
        self.assertFalse(self.panel.mode_strict.isEnabled())
        self.assertIs(self.panel._current_output_mode(), OutputMode.SHARED)

    def test_legacy_checkbox_shim(self) -> None:
        """v1.6 的用例/调用方 setChecked(True) 仍要等于"宽松独占"。"""
        self._select_wasapi()
        self.panel.mode_shared.setChecked(True)
        self.panel.exclusive_check.setChecked(True)
        self.assertIs(self.panel._current_output_mode(), OutputMode.EXCLUSIVE)

    def test_switching_backend_away_downgrades_mode(self) -> None:
        self._select_wasapi()
        self.panel.mode_strict.setChecked(True)
        index = self.panel.backend_combo.findData("mme")
        self.panel.backend_combo.setCurrentIndex(index)
        self.assertIs(
            self.panel._current_output_mode(),
            OutputMode.SHARED,
            "换成不支持独占的后端时必须退回共享",
        )


class TestRoundTrip(_Panel):
    def test_apply_writes_both_fields(self) -> None:
        self._select_wasapi()
        self.panel.mode_strict.setChecked(True)
        fallback_index = self.panel.fallback_combo.findData("ask")
        self.panel.fallback_combo.setCurrentIndex(fallback_index)
        self.panel.release_on_pause_check.setChecked(False)
        self.panel.apply_to(self.settings)

        self.assertEqual(self.settings.playback_output_mode, "exclusive_strict")
        self.assertTrue(self.settings.playback_exclusive, "布尔镜像必须同步")
        self.assertEqual(self.settings.playback_exclusive_fallback, "ask")
        self.assertFalse(self.settings.playback_release_on_pause)

    def test_load_restores(self) -> None:
        self.settings.playback_output_mode = "exclusive"
        self.settings.playback_exclusive = True
        self.settings.playback_exclusive_fallback = "never"
        self.settings.playback_release_on_pause = False
        self.panel.load_from(self.settings)
        self.assertIs(self.panel._current_output_mode(), OutputMode.EXCLUSIVE)
        self.assertEqual(self.panel.fallback_combo.currentData(), "never")
        self.assertFalse(self.panel.release_on_pause_check.isChecked())

    def test_old_bool_only_config_loads_as_loose(self) -> None:
        self.settings.playback_output_mode = "shared"
        self.settings.playback_exclusive = True
        self.panel.load_from(self.settings)
        self.assertIs(self.panel._current_output_mode(), OutputMode.EXCLUSIVE)

    def test_capability_box_visibility(self) -> None:
        self._select_wasapi()
        self.panel.mode_shared.setChecked(True)
        # 面板本身没 show()（离屏测试），所以用 isHidden() 判断而不是 isVisible()
        self.assertTrue(self.panel.capability_box.isHidden())
        self.panel.mode_exclusive.setChecked(True)
        self.assertFalse(self.panel.capability_box.isHidden())


class TestCapabilityTable(_Panel):
    """能力矩阵必须与设备探测结果一致（不骗人）。"""

    def test_matrix_matches_probe(self) -> None:
        wasapi = player_devices.list_output_devices("wasapi")
        if not wasapi:
            self.skipTest("没有 WASAPI 设备")
        device = wasapi[0]
        self._select_wasapi()
        self.panel.device_combo.setCurrentIndex(
            max(0, self.panel.device_combo.findData(device.name))
        )
        self.panel.mode_exclusive.setChecked(True)
        self.panel._refresh_capability()
        capability = player_devices.probe_cache().get(device)
        table = self.panel.capability_table
        self.assertGreaterEqual(table.rowCount(), 4)
        for row in range(table.rowCount()):
            rate = int(float(table.item(row, 0).text().split()[0]) * 1000)
            for column, mode in (
                (1, OutputMode.SHARED),
                (2, OutputMode.EXCLUSIVE),
                (3, OutputMode.EXCLUSIVE_STRICT),
            ):
                expected = "✓" if capability.supports(rate, mode) else "✗"
                self.assertEqual(
                    table.item(row, column).text(), expected,
                    f"{rate} Hz {mode.value} 的表格与探测结果不一致",
                )

    def test_asio_backend_greyed(self) -> None:
        index = self.panel.backend_combo.findData("asio")
        self.assertGreaterEqual(index, 0, "后端下拉必须有 ASIO 项（可见但不可用）")
        model_item = self.panel.backend_combo.model().item(index)
        if player_devices.backend_present("asio"):
            self.skipTest("本机 PortAudio 含 ASIO")
        self.assertFalse(model_item.isEnabled(), "当前构建不含 ASIO，必须置灰")


if __name__ == "__main__":
    unittest.main(verbosity=2)
