"""信号路径「全场景刷新」（v1.8.1 P0-2）自测。

两类断言，缺一不可：

**A. 每一级 detail 真的跟着设置/曲目变了**（9 个场景，逐级断言文字）
   切歌、停止、音量模式、EQ、后端、采样率回退、独占降级、整数通路/位完美、
   DSD 目标率、换设备 —— 覆盖 PRD P0-2 列出的全部触发点。

**B. 面板读的是"现在"而不是"上一次 100 ms 轮询"**
   这是 P0-2 的真正根因：``_on_player_signal`` / ``_on_player_track`` 读的是
   ``last_snapshot()``（上一拍轮询的产物）。用例里让 ``last_snapshot()`` 返回
   上一曲、``poll_now()`` 返回新曲 —— 面板必须显示新曲，且不许残留旧曲字样。

运行：``python -m unittest tests.test_signal_refresh -v``（离屏；不碰声卡）
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

from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.player.signal_path import (  # noqa: E402
    TEXT_INT_PIPELINE_UNAVAILABLE,
    SignalPath,
)
from seqaudio.ui.main_window import MainWindow  # noqa: E402
from seqaudio.ui.player_signals import PlayerSignalsPanel  # noqa: E402

_APP = QApplication.instance() or QApplication([])

#: 一条「MME 共享、44.1k 原样、DSP 全旁路」的基线路径（对应默认设置）
_BASE = dict(
    source="曲目 A.flac",
    codec="flac",
    decoder_version="ffmpeg",
    sample_fmt="s16",
    channels=2,
    sample_rate=44100,
    rate_want=44100,
    rate_actual=44100,
    ramp_ms=5,
    gapless=True,
    backend="mme",
    device="扬声器 (Realtek Audio)",
    device_dtype="int16",
    latency_ms=186.0,
    int_pipeline=True,
)


def _path(**overrides: Any) -> SignalPath:
    """按基线造一条信号路径（未给出的键一律走默认值）。"""
    kwargs = dict(_BASE)
    kwargs.update(overrides)
    path = SignalPath()
    path.configure(**kwargs)
    return path


def _snapshot(path: SignalPath, rate: int = 0) -> dict[str, Any]:
    """模拟 ``engine.snapshot()`` 里信号面板会读的那几个键。"""
    return {
        "signal_path": path.snapshot(),
        "signal_summary": path.summary(),
        "counters": path.counters.to_dict(),
        "rate": int(rate),
    }


class _PanelCase(unittest.TestCase):
    """面板级断言基类：每个用例一个干净的 ``PlayerSignalsPanel``。"""

    def setUp(self) -> None:
        self.panel = PlayerSignalsPanel()
        self.addCleanup(self.panel.deleteLater)

    def apply(self, path: SignalPath, rate: int = 0) -> None:
        self.panel.update_from(_snapshot(path, rate))


class TestStageDetails(_PanelCase):
    """A 组：每个触发点都要在对应级的 detail 上留下**新**事实。"""

    def test_track_change_leaves_no_residue(self) -> None:
        """切歌：source/decode/format 全部换成新曲，且绝不残留上一曲。"""
        self.apply(_path(), rate=44100)
        self.assertEqual(self.panel.row_detail("source"), "曲目 A.flac")
        self.assertIn("flac", self.panel.row_detail("decode"))

        self.apply(
            _path(
                source="曲目 B.dsf",
                codec="dsd_lsbf",
                is_dsd=True,
                sample_fmt="s32",
                sample_rate=2822400,
                rate_want=2822400,
                rate_actual=176400,
                int_pipeline=False,
            ),
            rate=176400,
        )
        self.assertEqual(self.panel.row_detail("source"), "曲目 B.dsf")
        self.assertIn("dsd_lsbf", self.panel.row_detail("decode"))
        self.assertIn("2822400 Hz", self.panel.row_detail("format"))
        for key in ("source", "decode", "format", "device"):
            self.assertNotIn(
                "A.flac", self.panel.row_detail(key),
                f"{key} 级残留了上一曲的信息：{self.panel.row_detail(key)}",
            )
            self.assertNotIn(
                "44100 Hz", self.panel.row_detail(key),
                f"{key} 级残留了上一曲的采样率：{self.panel.row_detail(key)}",
            )

    def test_stop_clears_every_stage(self) -> None:
        """停止：整条链路回到占位，不留任何一级旧文本。"""
        self.apply(_path(), rate=44100)
        self.panel.update_from({"signal_path": SignalPath.blank().snapshot()})
        for key in ("source", "decode", "format", "gain", "eq", "device"):
            self.assertEqual(self.panel.row_detail(key), "—", f"{key} 级未清空")

    def test_volume_mode_switch_changes_gain(self) -> None:
        """``playback_volume_mode`` 旁路 ↔ 软件音量：gain 级从旁路变浮点乘法。"""
        self.apply(_path(gain_db=0.0, gain_active=False), rate=44100)
        self.assertEqual(self.panel.row_status("gain"), "已旁路")
        self.assertIn("0 dB 旁路", self.panel.row_detail("gain"))

        self.apply(_path(gain_db=-6.0, gain_active=True), rate=44100)
        self.assertEqual(self.panel.row_status("gain"), "已改变")
        self.assertIn("-6.0 dB", self.panel.row_detail("gain"))

    def test_eq_switch_changes_eq_stage(self) -> None:
        """DSP/EQ 开关：eq 级在 旁路 / 全平直 / 31 段 之间切换。"""
        self.apply(_path(eq_enabled=False), rate=44100)
        self.assertEqual(self.panel.row_status("eq"), "已旁路")

        self.apply(_path(eq_enabled=True, eq_active=False, eq_bands=31), rate=44100)
        self.assertEqual(self.panel.row_status("eq"), "原样通过")
        self.assertIn("全平直", self.panel.row_detail("eq"))

        self.apply(_path(eq_enabled=True, eq_active=True, eq_bands=31), rate=44100)
        self.assertEqual(self.panel.row_status("eq"), "已改变")
        self.assertIn("31 段", self.panel.row_detail("eq"))

    def test_backend_switch_changes_output_stage(self) -> None:
        """后端切换：output 级的后端名与独占状态必须跟着变。"""
        self.apply(_path(backend="mme", exclusive_actual=False), rate=44100)
        self.assertIn("MME", self.panel.row_detail("output"))

        self.apply(
            _path(backend="wasapi", exclusive_actual=True, mode_label="loose"),
            rate=44100,
        )
        detail = self.panel.row_detail("output")
        self.assertIn("WASAPI", detail)
        self.assertIn("独占", detail)
        self.assertNotIn("MME", detail, "换后端后不许残留旧后端名")

    def test_rate_fallback_marks_resample_and_device(self) -> None:
        """采样率跟随失败：resample 级 FALLBACK + device 级写明源率。"""
        self.apply(_path(rate_want=44100, rate_actual=48000), rate=48000)
        self.assertEqual(self.panel.row_status("resample"), "已回退")
        self.assertIn("44100", self.panel.row_detail("resample"))
        self.assertIn("48000", self.panel.row_detail("resample"))
        self.assertIn("源 44100 Hz", self.panel.row_detail("device"))
        self.assertEqual(self.panel.counter_text("device_rate"), "48000 Hz")

    def test_exclusive_downgrade_marks_output_fallback(self) -> None:
        """独占 ↔ 共享与回退：请求独占却落在共享时 output 级必须标「已回退」。"""
        self.apply(
            _path(
                backend="wasapi",
                exclusive_actual=False,
                mode_label="shared",
                mode_note="该设备不支持独占，已退回共享",
            ),
            rate=44100,
        )
        self.assertEqual(self.panel.row_status("output"), "已回退")
        self.assertIn("共享", self.panel.row_detail("output"))
        self.assertIn("退回共享", self.panel.row_detail("output"))

    def test_int_pipeline_and_bit_perfect_marks_format(self) -> None:
        """整数通路/位完美：format 级在「原样通过」与「已改变 + 不可用说明」间切换。"""
        self.apply(_path(int_pipeline=True), rate=44100)
        self.assertEqual(self.panel.row_status("format"), "原样通过")

        self.apply(
            _path(int_pipeline=False, format_note=TEXT_INT_PIPELINE_UNAVAILABLE),
            rate=44100,
        )
        self.assertEqual(self.panel.row_status("format"), "已改变")
        self.assertIn("整数通路不可用", self.panel.row_detail("format"))

    def test_dsd_pcm_rate_change_changes_decode(self) -> None:
        """``playback_dsd_pcm_rate`` 改动：decode 级的 DSD→PCM 目标率跟着改。"""
        dsd = dict(
            source="曲目 D.dsf",
            codec="dsd_msbf",
            is_dsd=True,
            sample_fmt="s32",
            sample_rate=2822400,
            rate_want=2822400,
        )
        self.apply(_path(rate_actual=176400, **dsd), rate=176400)
        self.assertIn("DSD→176400 Hz", self.panel.row_detail("decode"))

        self.apply(_path(rate_actual=352800, **dsd), rate=352800)
        detail = self.panel.row_detail("decode")
        self.assertIn("DSD→352800 Hz", detail)
        self.assertNotIn("176400", detail, "改目标率后不许残留旧值")

    def test_device_switch_changes_device_stage(self) -> None:
        """换设备：设备名在 output 级、格式/延迟在 device 级，两处都要换干净。"""
        self.apply(_path(), rate=44100)
        self.assertIn("扬声器", self.panel.row_detail("output"))
        self.assertIn("186 ms", self.panel.row_detail("device"))

        self.apply(
            _path(
                device="TempoTecUSBAudio ASIO Driver",
                backend="asio",
                exclusive_actual=True,
                mode_label="loose",
                device_dtype="int32",
                latency_ms=304.0,
            ),
            rate=44100,
        )
        output = self.panel.row_detail("output")
        detail = self.panel.row_detail("device")
        self.assertIn("TempoTec", output)
        self.assertIn("304 ms", detail)
        self.assertIn("int32", detail)
        self.assertNotIn("扬声器", output)
        self.assertNotIn("186 ms", detail)

    def test_counters_follow_snapshot(self) -> None:
        """计数器（欠载/丢弃帧）也跟着快照走，不是只刷文字。"""
        path = _path()
        path.counters.underruns = 3
        path.counters.dropped_frames = 7
        self.apply(path, rate=44100)
        self.assertEqual(self.panel.counter_text("underruns"), "3")
        self.assertEqual(self.panel.counter_text("dropped_frames"), "7")

    def test_identical_snapshot_keeps_text_stable(self) -> None:
        """同文本不重复刷新（防闪烁）：两次同样的快照 → 面板文字一字不差。"""
        path = _path()
        self.apply(path, rate=44100)
        first = {
            key: (self.panel.row_status(key), self.panel.row_detail(key))
            for key in ("source", "decode", "format", "gain", "eq", "output", "device")
        }
        self.apply(path, rate=44100)
        second = {
            key: (self.panel.row_status(key), self.panel.row_detail(key))
            for key in first
        }
        self.assertEqual(first, second)


class _StaleBridge:
    """桥接替身：``last_snapshot()`` 故意返回**上一曲**，``poll_now()`` 返回新曲。

    这样能精确复现 P0-2 的现场：面板如果读 last_snapshot，就会一直显示上一曲。
    """

    def __init__(self, stale: dict[str, Any], fresh: dict[str, Any]) -> None:
        self._stale = stale
        self._fresh = fresh
        self.polls = 0

    def last_snapshot(self) -> dict[str, Any]:
        return dict(self._stale)

    def poll_now(self) -> dict[str, Any]:
        self.polls += 1
        return dict(self._fresh)


class _RefreshWindow:
    """只混入 ``_refresh_signal_panel``，不构造整个 MainWindow。"""

    _refresh_signal_panel = MainWindow._refresh_signal_panel
    # v1.8.1：刷新入口同时驱动 DSD 徽标/音量滑块，混入同名方法保持行为一致；
    # 本替身没有 player_bar / dsd_label，_update_dsd_ui 内部按缺失跳过。
    _update_dsd_ui = MainWindow._update_dsd_ui

    def __init__(self, panel: PlayerSignalsPanel, bridge: _StaleBridge) -> None:
        self.player_signals = panel
        self.player_bridge = bridge


class TestFreshSnapshotRefresh(unittest.TestCase):
    """B 组：刷新入口必须取"现在"的快照（P0-2 的根因修复）。"""

    def setUp(self) -> None:
        self.panel = PlayerSignalsPanel()
        self.addCleanup(self.panel.deleteLater)
        self.stale = _snapshot(_path(), rate=44100)
        self.fresh = _snapshot(
            _path(
                source="曲目 B.flac",
                sample_rate=96000,
                rate_want=96000,
                rate_actual=96000,
            ),
            rate=96000,
        )
        self.bridge = _StaleBridge(self.stale, self.fresh)
        self.window = _RefreshWindow(self.panel, self.bridge)

    def test_refresh_uses_poll_now_instead_of_last_snapshot(self) -> None:
        MainWindow._refresh_signal_panel(self.window)
        self.assertEqual(self.bridge.polls, 1, "刷新必须先 poll 一次")
        self.assertEqual(self.panel.row_detail("source"), "曲目 B.flac")
        self.assertIn("96000 Hz", self.panel.row_detail("format"))
        self.assertEqual(self.panel.counter_text("device_rate"), "96000 Hz")
        self.assertNotIn("曲目 A", self.panel.row_detail("source"))

    def test_last_snapshot_alone_would_have_been_wrong(self) -> None:
        """反证：只喂 last_snapshot 就是上一曲 —— 这正是要修的行为。"""
        self.panel.update_from(self.stale)
        self.assertEqual(self.panel.row_detail("source"), "曲目 A.flac")
        MainWindow._refresh_signal_panel(self.window)
        self.assertEqual(self.panel.row_detail("source"), "曲目 B.flac")

    def test_missing_bridge_is_safe(self) -> None:
        """bridge 还没建好时（构造期信号）刷新不能抛异常。"""
        window = _RefreshWindow(self.panel, None)  # type: ignore[arg-type]
        MainWindow._refresh_signal_panel(window)   # 不抛即通过
        self.assertEqual(self.panel.row_detail("source"), "—")


if __name__ == "__main__":
    unittest.main(verbosity=2)
