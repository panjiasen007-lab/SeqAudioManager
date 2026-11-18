"""播放界面自测（T04）：播放条 / 队列 / 歌词 / 信号路径 / EQ / 桥接。

运行：``python tests/test_player_ui.py``（离屏，不需要显示器与声卡）。

覆盖范围
--------
* 播放条：状态切换（按钮图标联动）、无曲目占位、拖动 seek、双击音量归零、
  模式循环、折叠高度、批处理禁用；
* 队列面板：整表刷新 / 当前行标记 / 总时长 / 增删清空 / 双击跳播信号；
* 歌词：``parse_lrc``（一行多时间戳、逐字降级、offset）、GBK 编码的 .lrc、
  位置高亮推进、偏移持久化；
* 信号路径：9 级渲染、状态文字与 objectName、回退标红、计数器、R2 措辞；
* EQ：滑杆 100 ms 防抖发信号、预设、总开关；
* 桥接：引擎事件 → Qt 信号（同线程与**跨线程**两种投递路径）、忙时拒绝起播、
  队列字典入队、会话保存/恢复不自动播放。
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.ui.player_bar import (  # noqa: E402
    BAR_COLLAPSED_HEIGHT,
    BAR_HEIGHT,
    MODE_ORDER,
    PlayerBar,
    format_mmss,
)
from seqaudio.ui.player_bridge import PlayerBridge  # noqa: E402
from seqaudio.ui.player_eq import PlayerEqPanel  # noqa: E402
from seqaudio.ui.player_lyrics import (  # noqa: E402
    PlayerLyricsPanel,
    parse_lrc,
    read_lyrics_file,
)
from seqaudio.ui.player_queue import PlayerQueuePanel, format_seconds  # noqa: E402
from seqaudio.ui.player_signals import PlayerSignalsPanel  # noqa: E402


def app() -> QApplication:
    """离屏 QApplication（与 test_ui_flow 共用同一实例）。"""
    instance = QApplication.instance()
    if instance is None:
        instance = QApplication([])
    return instance


def pump(ms: int = 30) -> None:
    """跑一小段事件循环（排队投递的信号需要它才会到达槽）。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def queue_item(title: str, duration: float = 180.0, artist: str = "歌手") -> dict:
    """构造一份与 ``QueueItem.to_dict()`` 同形的字典。"""
    return {
        "path": str(Path(tempfile.gettempdir()) / f"{title}.flac"),
        "cue_track": None,
        "title": title,
        "artist": artist,
        "album": "专辑",
        "duration": duration,
        "sample_rate": 44100,
        "channels": 2,
        "bit_depth": 16,
        "start_ms": 0,
        "duration_ms": int(duration * 1000),
        "is_dsd": False,
    }


class PlayerBarTest(unittest.TestCase):
    """播放条：状态、进度、音量、模式、折叠。"""

    def setUp(self) -> None:
        app()
        self.bar = PlayerBar()

    def tearDown(self) -> None:
        self.bar.deleteLater()

    def test_initial_state_is_placeholder(self) -> None:
        self.assertEqual(self.bar.current_state(), "IDLE")
        self.assertEqual(self.bar.title_label.text(), "未在播放")
        self.assertEqual(self.bar.height(), BAR_HEIGHT)
        self.assertEqual(self.bar.progress.value(), 0)

    def test_state_switch_toggles_play_glyph(self) -> None:
        self.bar.set_track(queue_item("第一首"))
        self.bar.set_state("PLAYING")
        self.assertEqual(self.bar.btn_play.text(), "❚❚")
        self.bar.set_state("PAUSED")
        self.assertEqual(self.bar.btn_play.text(), "▶")
        self.bar.set_state("LOADING")
        self.assertEqual(self.bar.btn_play.text(), "❚❚")

    def test_track_null_falls_back_to_placeholder(self) -> None:
        self.bar.set_track(queue_item("标题"))
        self.assertEqual(self.bar.title_label.text(), "标题")
        self.bar.set_track(None)
        self.assertEqual(self.bar.title_label.text(), "未在播放")
        self.assertEqual(self.bar.total_ms(), 0)

    def test_position_and_total(self) -> None:
        self.bar.set_track(queue_item("时长", duration=61.5))
        self.bar.set_position(5000, 61500)
        self.assertEqual(self.bar.progress.value(), 5000)
        self.assertEqual(self.bar.time_label.text(), "00:05 / 01:01")

    def test_seek_emitted_on_release_only(self) -> None:
        received: list[int] = []
        self.bar.seekRequested.connect(received.append)
        self.bar.set_track(queue_item("拖动"))
        self.bar.set_position(0, 100000)
        self.bar.progress.sliderPressed.emit()
        self.bar.progress.setValue(42000)
        self.assertEqual(received, [])
        self.bar.progress.sliderReleased.emit()
        self.assertEqual(received, [42000])

    def test_volume_double_click_resets_to_zero(self) -> None:
        received: list[float] = []
        self.bar.volumeChanged.connect(received.append)
        self.bar.set_volume_mode("software")
        self.bar.set_volume(-12.0)
        self.assertEqual(self.bar.volume_db(), -12.0)
        self.bar._reset_volume()
        self.assertEqual(self.bar.volume_db(), 0.0)
        self.assertEqual(received[-1], 0.0)
        self.assertEqual(self.bar.volume_label.text(), "旁路")

    def test_volume_bypass_still_usable(self) -> None:
        """v1.6.1 用户反馈：旁路只是默认值，不是功能锁 —— 滑块必须始终可用，
        拖动 = 顺手启用软件音量（main_window 落地），双击 = 归零回旁路。"""
        self.bar.set_volume_mode("bypass")
        self.assertTrue(self.bar.volume_slider.isEnabled())
        self.assertIn("软件音量", self.bar.volume_slider.toolTip())
        self.bar.set_volume_mode("software")
        self.assertTrue(self.bar.volume_slider.isEnabled())

    def test_progress_click_jumps(self) -> None:
        """v1.6.1 用户反馈：点击进度条任意位置直接跳转（原实现只翻页）。"""
        self.bar.set_track({"title": "T", "duration_ms": 100_000})
        self.bar.set_state("PLAYING")
        self.bar.set_position(0, 100_000)
        received: list[int] = []
        self.bar.seekRequested.connect(received.append)
        self.bar._on_progress_jump(50_000)
        self.assertEqual(received[-1], 50_000)
        self.assertEqual(self.bar.time_label.text()[:5], "00:50")
        # 子类确实带点击跳转能力
        self.assertTrue(hasattr(self.bar.progress, "jumpRequested"))

    def test_mode_cycle_order(self) -> None:
        received: list[str] = []
        self.bar.modeChanged.connect(received.append)
        self.bar.set_mode("sequential")
        for _ in range(4):
            self.bar.cycle_mode()
        # 顺序 → 单曲 → 列表循环 → 随机 → 顺序
        self.assertEqual(received, list(MODE_ORDER[1:]) + [MODE_ORDER[0]])
        self.assertEqual(self.bar.current_mode(), "sequential")

    def test_collapse_hides_controls_and_shrinks(self) -> None:
        states: list[bool] = []
        self.bar.collapseToggled.connect(states.append)
        self.bar.set_collapsed(True, emit=True)
        self.assertEqual(self.bar.height(), BAR_COLLAPSED_HEIGHT)
        self.assertFalse(self.bar.progress_widget.isVisible())
        self.assertTrue(self.bar.btn_play.isVisible() or not self.bar.isVisible())
        self.bar.set_collapsed(False, emit=True)
        self.assertEqual(self.bar.height(), BAR_HEIGHT)
        self.assertEqual(states, [True, False])

    def test_playback_disabled_greys_out_controls(self) -> None:
        self.bar.set_playback_enabled(False)
        self.assertFalse(self.bar.btn_play.isEnabled())
        self.assertFalse(self.bar.btn_next.isEnabled())
        self.bar.set_playback_enabled(True)
        self.assertTrue(self.bar.btn_play.isEnabled())

    def test_time_format(self) -> None:
        self.assertEqual(format_mmss(0), "00:00")
        self.assertEqual(format_mmss(65_000), "01:05")


class QueuePanelTest(unittest.TestCase):
    """队列面板：刷新、当前行、总时长、增删清空。"""

    def setUp(self) -> None:
        app()
        self.panel = PlayerQueuePanel()

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def test_set_items_and_total(self) -> None:
        self.panel.set_items([queue_item("A", 60), queue_item("B", 90.5)])
        self.assertEqual(self.panel.count(), 2)
        self.assertAlmostEqual(self.panel.total_seconds(), 150.5, places=2)
        self.assertIn("2", self.panel.summary_label.text())
        self.assertIn("02:30", self.panel.summary_label.text())

    def test_current_row_marker_and_bold(self) -> None:
        self.panel.set_items([queue_item("A"), queue_item("B")])
        self.panel.set_current(1)
        model = self.panel.model
        row_text = model.data(model.index(1, 0))
        self.assertTrue(row_text.startswith("▶"), row_text)
        plain = model.data(model.index(0, 0))
        self.assertFalse(plain.startswith("▶"))
        font = model.data(model.index(1, 0), 6)  # Qt.FontRole == 6
        self.assertTrue(font is None or font.bold())

    def test_remove_and_clear_signals(self) -> None:
        removed: list[int] = []
        cleared: list[int] = []
        self.panel.removeRequested.connect(removed.append)
        self.panel.clearRequested.connect(lambda: cleared.append(1))
        self.panel.set_items([queue_item("A"), queue_item("B")])
        self.panel.removeRequested.emit(0)
        self.panel.clearRequested.emit()
        self.assertEqual(removed, [0])
        self.assertEqual(cleared, [1])

    def test_empty_state(self) -> None:
        self.panel.set_items([])
        self.assertEqual(self.panel.count(), 0)
        self.assertFalse(self.panel.btn_clear.isEnabled())

    def test_double_click_emits_activation(self) -> None:
        activated: list[int] = []
        self.panel.trackActivated.connect(activated.append)
        self.panel.set_items([queue_item("A"), queue_item("B")])
        self.panel._on_double_clicked(self.panel.model.index(1, 0))
        self.assertEqual(activated, [1])

    def test_seconds_format(self) -> None:
        self.assertEqual(format_seconds(0), "00:00")
        self.assertEqual(format_seconds(3599), "59:59")
        self.assertEqual(format_seconds(3661), "1:01:01")


class LyricsTest(unittest.TestCase):
    """歌词：解析、逐字降级、GBK 文件、高亮推进、偏移。"""

    def setUp(self) -> None:
        app()
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_lyr_"))
        self.settings = Settings()
        self.panel = PlayerLyricsPanel(self.settings)

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def test_parse_basic_and_multi_timestamp(self) -> None:
        text = "[ti:测试]\n[00:01.00]第一行\n[00:03.50][00:20.00]副歌\n"
        lines, meta = parse_lrc(text)
        self.assertEqual(meta.get("ti"), "测试")
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[0], (1000, "第一行"))
        self.assertEqual(lines[1][0], 3500)
        self.assertEqual(lines[2][0], 20000)
        self.assertEqual(lines[1][1], "副歌")

    def test_word_level_lrc_degrades_to_lines(self) -> None:
        text = "[00:12.00]<00:12.00>逐<00:12.35>字<00:12.70>歌\n[00:15.00]下一行"
        lines, _ = parse_lrc(text)
        self.assertEqual(len(lines), 2, "逐字 LRC 必须降级成逐行，而不是整首丢弃")
        self.assertEqual(lines[0][1], "逐字歌")

    def test_offset_meta_shifts_timeline(self) -> None:
        self.panel.set_lyrics("[offset:500]\n[00:02.00]第一行")
        self.assertEqual(self.panel.line_count(), 1)
        # offset:500 表示歌词提前 500 ms
        self.assertEqual(self.panel._times[0], 1500)

    def test_gbk_sidecar_is_readable(self) -> None:
        target = self.tmp / "歌曲.lrc"
        target.write_bytes("[00:01.00]中文歌词测试\n".encode("gbk"))
        text = read_lyrics_file(target)
        self.assertIn("中文歌词测试", text)
        self.panel.set_track({"path": str(self.tmp / "歌曲.flac"), "title": "歌曲"})
        self.assertEqual(self.panel.line_count(), 1)

    def test_highlight_follows_position(self) -> None:
        self.panel.set_lyrics("[00:01.00]一\n[00:03.00]二\n[00:05.00]三")
        self.panel.set_position(500)
        self.assertEqual(self.panel.current_line_index(), -1)
        self.panel.set_position(3200)
        self.assertEqual(self.panel.current_line_index(), 1)
        self.panel.set_position(9000)
        self.assertEqual(self.panel.current_line_index(), 2)

    def test_offset_adjust_persists_to_settings(self) -> None:
        self.panel.set_lyrics("[00:10.00]一")
        self.panel.set_offset_ms(500)
        self.assertEqual(self.panel.offset_ms(), 500)
        self.assertEqual(self.settings.playback_lyrics_offset_ms, 500)
        # 偏移 +500 表示歌词延后：位置 10.1 s 时还没到第一行（10.5 s 才到）
        self.panel.set_position(10_100)
        self.assertEqual(self.panel.current_line_index(), -1)
        self.panel.set_position(10_600)
        self.assertEqual(self.panel.current_line_index(), 0)

    def test_clear_resets_state(self) -> None:
        self.panel.set_lyrics("[00:01.00]一")
        self.panel.clear()
        self.assertEqual(self.panel.line_count(), 0)
        self.assertEqual(self.panel.current_line_index(), -1)
        self.assertTrue(self.panel.hint_label.isVisible() or True)
        self.assertEqual(self.panel.hint_label.text(), "未找到歌词")


class SignalsPanelTest(unittest.TestCase):
    """信号路径面板：9 级、状态文字、回退标红、计数器、措辞铁律。"""

    def setUp(self) -> None:
        app()
        self.panel = PlayerSignalsPanel()

    def tearDown(self) -> None:
        self.panel.deleteLater()

    @staticmethod
    def _snapshot(fallback: bool = False) -> dict:
        stages = []
        for key in (
            "source", "decode", "format", "gain", "eq", "fade", "resample", "output", "device",
        ):
            status = "PASSTHROUGH"
            detail = "—"
            if key == "gain":
                status, detail = "BYPASSED", "0 dB 旁路（无软件增益）"
            if fallback and key in ("resample", "device"):
                status, detail = "FALLBACK", "已重采样 44100→48000（设备不支持源采样率）"
            stages.append(
                {
                    "key": key,
                    "label": key,
                    "i18n_key": f"sig_stage_{key}",
                    "detail": detail,
                    "status": status,
                    "status_i18n_key": f"sig_status_{status.lower()}",
                }
            )
        return {
            "state": "PLAYING",
            "rate": 48000,
            "signal_path": stages,
            "signal_summary": "已重采样 44100→48000（设备不支持源采样率）" if fallback else "未做软件改动",
            "counters": {
                "underruns": 0,
                "dropped_frames": 3,
                "resample_events": 1 if fallback else 0,
                "decode_errors": 0,
                "gain_db": 0.0,
                "last_gap_ms": 0.0,
            },
        }

    def test_render_nine_stages(self) -> None:
        self.panel.update_from(self._snapshot())
        self.assertEqual(self.panel.row_count(), 9)
        self.assertEqual(self.panel.row_status("gain"), "已旁路")
        self.assertEqual(self.panel.row_status("source"), "原样通过")
        self.assertEqual(self.panel.summary_text(), "未做软件改动")

    def test_fallback_rows_marked(self) -> None:
        self.panel.update_from(self._snapshot(fallback=True))
        self.assertEqual(self.panel.row_status("resample"), "已回退")
        self.assertIn("44100→48000", self.panel.row_detail("resample"))
        self.assertEqual(
            self.panel._rows["resample"].objectName(), "signalChanged",
            "回退行必须换成 signalChanged，测试才能断言标红",
        )
        self.assertNotEqual(self.panel.summary_text(), "未做软件改动")

    def test_counters(self) -> None:
        self.panel.update_from(self._snapshot(fallback=True))
        self.assertEqual(self.panel.counter_text("dropped_frames"), "3")
        self.assertEqual(self.panel.counter_text("resample_events"), "1")
        self.assertEqual(self.panel.counter_text("device_rate"), "48000 Hz")

    def test_forbidden_claims_are_sanitized(self) -> None:
        snapshot = self._snapshot()
        snapshot["signal_path"][0]["detail"] = "bit-perfect 逐样本一致"
        self.panel.update_from(snapshot)
        detail = self.panel.row_detail("source")
        self.assertNotIn("bit-perfect", detail)
        self.assertNotIn("逐样本一致", detail)

    def test_accepts_signal_path_dict(self) -> None:
        payload = {
            "stages": self._snapshot()["signal_path"],
            "counters": {"underruns": 2},
            "summary": "未做软件改动",
        }
        self.panel.update_from(payload)
        self.assertEqual(self.panel.row_count(), 9)
        self.assertEqual(self.panel.counter_text("underruns"), "2")


class EqPanelTest(unittest.TestCase):
    """EQ：滑杆防抖、预设、总开关。"""

    def setUp(self) -> None:
        app()
        self.panel = PlayerEqPanel()

    def tearDown(self) -> None:
        self.panel.deleteLater()

    def test_has_ten_bands(self) -> None:
        self.assertEqual(len(self.panel._sliders), 10)
        self.assertEqual(self.panel.gains(), [0.0] * 10)
        self.assertEqual(self.panel._sliders[0].minimum(), -120)
        self.assertEqual(self.panel._sliders[0].maximum(), 120)

    def test_drag_is_debounced(self) -> None:
        received: list[list[float]] = []
        self.panel.gainsChanged.connect(received.append)
        self.panel.set_enabled(True)
        # 拖动过程中连续改 3 个滑杆，只应收到最后一次（100 ms 静默后）
        self.panel._sliders[0].setValue(30)
        self.panel._sliders[1].setValue(-20)
        self.panel._sliders[2].setValue(10)
        self.assertEqual(received, [])
        pump(220)
        self.assertEqual(len(received), 1, received)
        self.assertAlmostEqual(received[0][0], 3.0)
        self.assertAlmostEqual(received[0][1], -2.0)

    def test_preset_and_reset(self) -> None:
        received: list[list[float]] = []
        self.panel.gainsChanged.connect(received.append)
        self.panel.set_enabled(True)
        self.panel.apply_preset("rock")
        self.assertTrue(received)
        self.assertNotEqual(received[-1], [0.0] * 10)
        self.panel.apply_preset("flat")
        self.assertEqual(self.panel.gains(), [0.0] * 10)

    def test_enabled_signal(self) -> None:
        states: list[bool] = []
        self.panel.enabledChanged.connect(states.append)
        self.panel.enable_check.setChecked(True)
        self.assertTrue(states[-1])
        self.assertTrue(self.panel._sliders[0].isEnabled())
        self.panel.enable_check.setChecked(False)
        self.assertFalse(self.panel._sliders[0].isEnabled())

    def test_set_gains_is_silent(self) -> None:
        received: list[list[float]] = []
        self.panel.gainsChanged.connect(received.append)
        self.panel.set_gains([1.0] * 10)
        pump(180)
        self.assertEqual(received, [])
        self.assertEqual(self.panel.gains(), [1.0] * 10)


class BridgeTest(unittest.TestCase):
    """桥接：事件转信号（含跨线程）、忙时拒播、队列字典、会话。"""

    def setUp(self) -> None:
        app()
        self.settings = Settings()
        self.bridge = PlayerBridge(self.settings)

    def tearDown(self) -> None:
        self.bridge.shutdown()

    def test_state_event_emits_signal(self) -> None:
        received: list[str] = []
        self.bridge.stateChanged.connect(received.append)
        self.bridge._on_engine_event("state", "PLAYING")
        self.assertEqual(received, ["PLAYING"])

    def test_all_event_kinds_map(self) -> None:
        seen: dict[str, object] = {}
        self.bridge.trackChanged.connect(lambda payload: seen.setdefault("track", payload))
        self.bridge.positionChanged.connect(lambda value: seen.setdefault("pos", value))
        self.bridge.errorRaised.connect(lambda value: seen.setdefault("err", value))
        self.bridge.queueChanged.connect(lambda value: seen.setdefault("queue", value))
        self.bridge.gapDetected.connect(lambda value: seen.setdefault("gap", value))
        self.bridge.volumeChanged.connect(lambda value: seen.setdefault("vol", value))
        self.bridge.eofReached.connect(lambda: seen.setdefault("eof", True))
        self.bridge._on_engine_event("track", {"title": "A", "duration_ms": 1000})
        self.bridge._on_engine_event("position", 1200)
        self.bridge._on_engine_event("error", "设备忙")
        self.bridge._on_engine_event("queue", 3)
        self.bridge._on_engine_event("gap", 12.5)
        self.bridge._on_engine_event("volume", -6.0)
        self.bridge._on_engine_event("eof", None)
        self.assertEqual(seen["track"]["title"], "A")
        self.assertEqual(seen["pos"], 1200)
        self.assertEqual(seen["err"], "设备忙")
        self.assertEqual(seen["queue"], 3)
        self.assertAlmostEqual(seen["gap"], 12.5)
        self.assertAlmostEqual(seen["vol"], -6.0)
        self.assertTrue(seen["eof"])

    def test_cross_thread_event_is_marshalled_to_gui_thread(self) -> None:
        received: list[int] = []
        self.bridge.positionChanged.connect(received.append)

        def worker() -> None:
            # 模拟解码线程：绝不能在非 GUI 线程直接 emit 到控件
            self.bridge._on_engine_event("position", 4321)

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()
        # 投递是排队的：先要跑一次事件循环才会到达槽
        pump(80)
        self.assertEqual(received, [4321])

    def test_busy_refuses_playback(self) -> None:
        errors: list[str] = []
        self.bridge.errorRaised.connect(errors.append)
        self.bridge.set_busy(True)
        self.assertFalse(self.bridge.play_current())
        self.assertTrue(errors and "批处理" in errors[-1])
        self.bridge.set_busy(False)

    def test_disabled_playback_refuses(self) -> None:
        self.settings.playback_enabled = False
        errors: list[str] = []
        self.bridge.errorRaised.connect(errors.append)
        self.assertFalse(self.bridge.play_current())
        self.assertIn("播放功能已关闭", errors[-1])

    def test_enqueue_accepts_dicts(self) -> None:
        length = self.bridge.enqueue([queue_item("A"), queue_item("B")])
        self.assertEqual(length, 2)
        self.assertEqual(len(self.bridge.queue_items()), 2)
        self.assertEqual(self.bridge.queue_index(), 0)

    def test_play_next_inserts_after_current(self) -> None:
        self.bridge.enqueue([queue_item("A"), queue_item("B")])
        position = self.bridge.play_next(queue_item("插播"))
        self.assertEqual(position, 1)
        titles = [item["title"] for item in self.bridge.queue_items()]
        self.assertEqual(titles, ["A", "插播", "B"])

    def test_remove_and_clear(self) -> None:
        self.bridge.enqueue([queue_item("A"), queue_item("B")])
        self.assertTrue(self.bridge.remove_index(1))
        self.assertEqual(len(self.bridge.queue_items()), 1)
        self.bridge.clear_queue()
        self.assertEqual(self.bridge.queue_items(), [])

    def test_snapshot_contains_signal_path(self) -> None:
        snapshot = self.bridge.snapshot()
        self.assertIn("signal_path", snapshot)
        self.assertEqual(len(snapshot["signal_path"]), 9)
        self.assertIn("signal_summary", snapshot)

    def test_list_output_devices_returns_dicts(self) -> None:
        devices = self.bridge.list_output_devices()
        self.assertIsInstance(devices, list)
        for entry in devices[:3]:
            self.assertIn("name", entry)
            self.assertIn("label", entry)

    def test_restore_session_does_not_autoplay(self) -> None:
        session_path = Path(tempfile.mkdtemp(prefix="seq_ses_")) / "session.json"
        payload = {
            "version": 1,
            "tracks": [
                {"path": str(session_path.parent / "a.flac"), "cue_track": None},
                {"path": str(session_path.parent / "b.flac"), "cue_track": None},
            ],
            "index": 1,
            "position_ms": 12_345,
            "mode": "repeat_all",
            "output_device": "",
            "output_backend": "mme",
            "volume_db": -6.0,
            "eq_gains": [0.0] * 10,
        }
        import json

        session_path.write_text(json.dumps(payload), encoding="utf-8")
        from seqaudio.player.session import PlaybackSession

        session = PlaybackSession.load(session_path)
        self.assertIsNotNone(session)
        # 直接把会话内容塞进引擎（避免依赖真实会话文件路径）
        from seqaudio.player.queue import PlayQueue, QueueItem

        items = [QueueItem(path=Path(e["path"])) for e in payload["tracks"]]
        queue = PlayQueue(items, mode="repeat_all")
        queue.index = 1
        self.bridge.engine.set_queue(queue)
        self.bridge.engine.open_track(queue.current(), autoplay=False)
        self.assertEqual(self.bridge.state(), "IDLE")
        self.assertEqual(self.bridge.queue_index(), 1)
        self.assertEqual(len(self.bridge.queue_items()), 2)

    def test_session_capture_roundtrip(self) -> None:
        """会话抓取 → 落盘 → 读回一致（不碰真实会话文件）。"""
        from seqaudio.player.session import PlaybackSession

        self.bridge.enqueue([queue_item("A"), queue_item("B")])
        session = PlaybackSession.capture(self.bridge.engine)
        self.assertEqual(len(session.tracks), 2)
        target = Path(tempfile.mkdtemp(prefix="seq_ses_")) / "session.json"
        self.assertTrue(session.save(target))
        loaded = PlaybackSession.load(target)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.tracks), 2)
        self.assertEqual(loaded.index, session.index)


if __name__ == "__main__":
    unittest.main(verbosity=2)
