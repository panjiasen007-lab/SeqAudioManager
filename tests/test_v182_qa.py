"""v1.8.2 五项改动的 QA 独立验证用例（与实现者的 test_v182.py 互相独立）。

由 QA 工程师编写，不采信实现侧自查结论，逐条重新证明：
1. ASIO 两级验证：check 通过但开流失败 → 判不可用；播放中跳过开流；钩子容错。
2. 字体恰好 8 款，资产一一对应，被删 id 回退不崩。
3. 预览 label 零残留，SettingsPanel 构建正常。
4. 迷你条转盘：存在、播放旋转、暂停缓停、hideEvent 停表。
5. 版本号三处一致 1.8.2。
6. devices.py 依赖方向：不 import UI / 引擎。

运行：``PYTHONPATH=src python -m pytest tests/test_v182_qa.py -q``
"""

from __future__ import annotations

import importlib.util
import inspect
import re
import sys
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QBuffer, Qt  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.ui import fonts, i18n  # noqa: E402
from seqaudio.ui.mini_player import (  # noqa: E402
    MINI_HEIGHT,
    MINI_WIDTH,
    SPIN_COVER_SIZE,
    SPIN_SPEED_DPS,
    SPIN_TICK_MS,
    MiniPlayerWindow,
    _SpinCover,
)
from seqaudio.ui.settings_panel import SettingsPanel  # noqa: E402

_APP = QApplication.instance() or QApplication([])


def _device(name: str = "Realtek ASIO", rate: float = 44100.0,
            index: int = 18) -> "dev_mod.DeviceInfo":
    return dev_mod.DeviceInfo(
        index=index, name=name, channels=2, default_samplerate=rate,
        is_default=False, hostapi_index=3, hostapi_name="ASIO",
    )


# ---------------------------------------------------------------- 1. ASIO


class QaAsioTwoLevelVerification(unittest.TestCase):
    """空壳驱动必须被「开流验证」拦下，即使零出声 check 全部通过。"""

    def test_check_pass_open_fail_is_unusable(self) -> None:
        devices = [_device("Realtek ASIO 空壳")]
        opened: list[tuple[str, int, int]] = []

        def open_probe(device, rate, channels) -> bool:
            opened.append((device.name, int(rate), int(channels)))
            raise RuntimeError("Invalid device [PaErrorCode -9985]")

        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=lambda *a, **k: True, open_probe=open_probe)
        self.assertIsNone(got, "check 通过但开流失败 → 必须判不可用")
        self.assertEqual(opened, [("Realtek ASIO 空壳", 44100, 2)],
                         "check 通过后必须真的做开流验证，且按设备默认率/立体声")

    def test_open_fail_falls_through_to_next_device(self) -> None:
        """-9985 只否掉当前设备：第二台真设备仍能被选出。"""
        bad = _device("空壳 ASIO", index=18)
        good = _device("TempoTec ASIO", index=19)
        devices = [bad, good]
        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=lambda *a, **k: True,
                open_probe=lambda d, r, c: d is good,
            )
        self.assertIs(got, good)

    def test_open_probe_false_tries_next_rate(self) -> None:
        """开流返回 False（非异常）→ 换下一个候选采样率继续验证。

        候选率 = (设备默认率, 44100, 48000) 去重保序；默认 96k 与兜底 44.1k
        都被拒后，48k 通过。
        """
        devices = [_device("TempoTec ASIO", rate=96000.0)]
        rates: list[int] = []

        def open_probe(device, rate, channels) -> bool:
            rates.append(int(rate))
            return int(rate) == 48000

        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=lambda *a, **k: True, open_probe=open_probe)
        self.assertIsNotNone(got)
        self.assertEqual(rates, [96000, 44100, 48000])

    def test_playing_flag_skips_open_stream(self) -> None:
        devices = [_device("TempoTec ASIO")]
        opened: list[int] = []
        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=lambda *a, **k: True,
                open_probe=lambda d, r, c: opened.append(r) or True,
                playing=True,
            )
        self.assertIsNotNone(got)
        self.assertEqual(opened, [], "播放中绝不许再开第二条流")

    def test_no_hook_defaults_to_open_verification(self) -> None:
        """未注册钩子时按「没在播」处理，开流验证照常执行（不炸）。"""
        self.assertIsNone(dev_mod._PLAYING_QUERY)
        self.assertFalse(dev_mod.playback_in_progress())
        devices = [_device("TempoTec ASIO")]
        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=lambda *a, **k: True, open_probe=lambda *a, **k: True)
        self.assertIsNotNone(got)

    def test_hook_exception_tolerated(self) -> None:
        def boom() -> bool:
            raise RuntimeError("hook 炸了")

        dev_mod.set_playing_query(boom)
        try:
            self.assertFalse(dev_mod.playback_in_progress(),
                             "钩子异常按「未在播」处理，不许拖垮探测")
            devices = [_device("TempoTec ASIO")]
            with mock.patch.object(dev_mod, "list_output_devices",
                                   return_value=devices):
                got = dev_mod.asio_usable_device(
                    probe=lambda *a, **k: True, open_probe=lambda *a, **k: True)
            self.assertIsNotNone(got)
        finally:
            dev_mod.set_playing_query(None)

    def test_set_and_clear_hook(self) -> None:
        dev_mod.set_playing_query(lambda: True)
        try:
            self.assertTrue(dev_mod.playback_in_progress())
        finally:
            dev_mod.set_playing_query(None)
        self.assertIsNone(dev_mod._PLAYING_QUERY)
        self.assertFalse(dev_mod.playback_in_progress())

    def test_open_stream_uses_silent_int16_stereo_short_buffer(self) -> None:
        """默认开流验证的参数：exclusive 语义、int16、2ch、≈0.15s 静音。"""
        created: list[dict] = []

        class _Stream:
            def __init__(self, **kwargs) -> None:
                self.kwargs = dict(kwargs)
                created.append(self.kwargs)

            def start(self) -> None: ...
            def write(self, _data) -> None: ...
            def stop(self) -> None: ...
            def close(self) -> None: ...

        fake_sd = SimpleNamespace(OutputStream=_Stream)
        device = _device("TempoTec ASIO", rate=48000.0, index=7)
        with mock.patch.object(dev_mod, "sounddevice", return_value=fake_sd):
            self.assertTrue(dev_mod._asio_open_stream_ok(device, 48000, 2))
        self.assertEqual(len(created), 1)
        kwargs = created[0]
        self.assertEqual(kwargs["samplerate"], 48000)
        self.assertEqual(kwargs["channels"], 2)
        self.assertEqual(kwargs["dtype"], "int16")
        self.assertEqual(kwargs["device"], 7)
        self.assertIsNone(kwargs["extra_settings"], "ASIO 不传 WasapiSettings")

    def test_open_stream_constructor_failure_propagates(self) -> None:
        """构造流就失败（空壳驱动典型路径）→ 异常上抛，由调用方判不可用。"""
        class _Boom:
            def __init__(self, **_kwargs) -> None:
                raise RuntimeError("Invalid device [PaErrorCode -9985]")

        fake_sd = SimpleNamespace(OutputStream=_Boom)
        with mock.patch.object(dev_mod, "sounddevice", return_value=fake_sd):
            with self.assertRaises(RuntimeError):
                dev_mod._asio_open_stream_ok(_device(), 44100, 2)

    def test_devices_module_does_not_import_ui_or_engine(self) -> None:
        """依赖方向：devices.py 不得 import seqaudio.ui / 播放引擎。"""
        source = inspect.getsource(dev_mod)
        import_lines = re.findall(
            r"^\s*(?:from\s+\S+\s+import\s|import\s+)\S+", source, re.M)
        for line in import_lines:
            self.assertNotIn("seqaudio.ui", line, f"违规导入：{line}")
            self.assertNotIn("player_bridge", line, f"违规导入：{line}")
            self.assertNotIn("mini_player", line, f"违规导入：{line}")
            self.assertNotIn("engine", line, f"违规导入：{line}")


class QaAsioDeviceCombo(unittest.TestCase):
    """ASIO 后端下拉：无「系统默认设备」占位，无设备时置灰。"""

    def setUp(self) -> None:
        self.panel = SettingsPanel(Settings(offline_mode=True))
        self.addCleanup(self.panel.deleteLater)
        self.panel.backend_combo.setCurrentIndex(
            self.panel.backend_combo.findData("asio"))

    def test_no_default_placeholder_for_asio(self) -> None:
        self.panel.set_output_devices(
            [{"name": "TempoTec ASIO", "label": "ASIO · TempoTec ASIO",
              "channels": 2}], scoped=True)
        combo = self.panel.device_combo
        self.assertTrue(combo.isEnabled())
        texts = [combo.itemText(i) for i in range(combo.count())]
        self.assertEqual(texts, ["TempoTec ASIO"])
        self.assertNotIn(i18n.tr_text("系统默认设备"), texts)

    def test_empty_asio_list_disabled_with_hint(self) -> None:
        self.panel.set_output_devices([], scoped=True)
        combo = self.panel.device_combo
        self.assertFalse(combo.isEnabled())
        self.assertEqual(combo.count(), 1)
        self.assertEqual(combo.itemText(0), i18n.tr("dev_asio_no_device"))
        self.assertTrue(str(i18n.tr("dev_asio_no_device")).strip())

    def test_i18n_entry_has_8_locales(self) -> None:
        entry = i18n._TRANSLATIONS.get("dev_asio_no_device")
        self.assertIsNotNone(entry)
        self.assertEqual(len(entry), 8, "8 种语言都要有译文")
        for locale, text in entry.items():
            self.assertTrue(str(text).strip(), f"{locale} 译文为空")


# ---------------------------------------------------------------- 2. 字体


class QaFontReduction(unittest.TestCase):
    def test_total_choices_are_eight(self) -> None:
        self.assertEqual(len(fonts.UI_CHOICES), 5)
        self.assertEqual(len(fonts.MONO_CHOICES), 3)
        self.assertEqual(len(fonts.UI_CHOICES) + len(fonts.MONO_CHOICES), 8)

    def test_choice_ids_exact(self) -> None:
        self.assertEqual([c.id for c in fonts.UI_CHOICES],
                         ["noto", "wenkai", "smiley", "inter", "plex_sans"])
        self.assertEqual([c.id for c in fonts.MONO_CHOICES],
                         ["jetbrains", "fira_code", "cascadia"])

    def test_assets_match_choices_exactly(self) -> None:
        """8 个字族 ↔ 字体文件一一对应（Noto/JetBrains 各含 Bold+Regular 变体）。"""
        fonts_dir = ROOT / "assets" / "fonts"
        names = sorted(p.name for p in fonts_dir.iterdir())
        expected = sorted([
            # noto（中文界面，Regular+Bold）
            "NotoSansSC-Regular.otf", "NotoSansSC-Bold.otf", "OFL-NotoSansSC.txt",
            # wenkai
            "LXGWWenKai-Regular.ttf", "OFL-LXGWWenKai.txt",
            # smiley
            "SmileySans-Oblique.ttf", "OFL-SmileySans.txt",
            # inter
            "Inter-Variable.ttf", "OFL-Inter.txt",
            # plex_sans
            "IBMPlexSans.ttf", "OFL-IBMPlexSans.txt",
            # jetbrains（Regular+Bold）
            "JetBrainsMono-Regular.ttf", "JetBrainsMono-Bold.ttf",
            "OFL-JetBrainsMono.txt",
            # fira_code
            "FiraCode.ttf", "OFL-FiraCode.txt",
            # cascadia
            "CascadiaCode.ttf", "OFL-CascadiaCode.txt",
        ])
        self.assertEqual(names, expected, "assets/fonts 必须与 8 款 choices 一一对应")
        # 被删的 12 组旧字体不得残留（按精确文件名判断，避免误伤 NotoSansSC）
        for stale in ("MaShanZheng.ttf", "ZCOOLQingKeHuangYou.ttf",
                      "SourceSans3.ttf", "FiraSans.ttf", "PublicSans.ttf",
                      "Manrope.ttf", "WorkSans.ttf", "Rubik.ttf", "Lato.ttf",
                      "NotoSans.ttf", "IBMPlexMono.ttf", "SourceCodePro.ttf"):
            self.assertNotIn(stale, names, f"旧字体 {stale} 不应残留在 assets/fonts")

    def test_removed_id_falls_back_without_crash(self) -> None:
        fonts.configure("mashan", "plex_mono", 1.0)
        self.assertEqual(fonts.current_ui_id(), "noto")
        self.assertEqual(fonts.current_mono_id(), "jetbrains")
        fonts.configure("noto", "jetbrains", 1.0)  # 复位

    def test_configure_clamps_bad_scale(self) -> None:
        fonts.configure("noto", "jetbrains", 99.0)
        self.assertEqual(fonts.current_scale(), fonts.MAX_SCALE)
        fonts.configure("noto", "jetbrains", "abc")  # type: ignore[arg-type]
        self.assertEqual(fonts.current_scale(), fonts.DEFAULT_SCALE)
        fonts.configure("noto", "jetbrains", 1.0)


# ---------------------------------------------------------------- 3. 预览移除


class QaPreviewRemoved(unittest.TestCase):
    def test_no_preview_symbols_in_source(self) -> None:
        src_dir = ROOT / "src" / "seqaudio"
        pattern = re.compile(r"font_preview|mono_preview|_refresh_appearance_preview")
        hits: list[str] = []
        for path in src_dir.rglob("*.py"):
            if pattern.search(path.read_text(encoding="utf-8")):
                hits.append(str(path))
        self.assertEqual(hits, [], f"预览相关符号残留：{hits}")

    def test_panel_constructs_and_roundtrips(self) -> None:
        settings = Settings(offline_mode=True)
        panel = SettingsPanel(settings)
        self.addCleanup(panel.deleteLater)
        self.assertFalse(hasattr(panel, "font_preview"))
        self.assertFalse(hasattr(panel, "mono_preview"))
        panel.ui_font_combo.setCurrentIndex(2)
        panel.mono_font_combo.setCurrentIndex(1)
        panel.load_from(Settings(offline_mode=True))  # 旧版会刷预览
        applied = panel.apply_to(Settings(offline_mode=True))
        self.assertIsInstance(applied, Settings)


# ---------------------------------------------------------------- 4. 转盘


class _FakeSettings:
    playback_mini_opacity = 80

    def save(self) -> None: ...


class _FakeLyrics:
    _lines: list = []
    _current = -1


class QaSpinCover(unittest.TestCase):
    def setUp(self) -> None:
        self.window = MiniPlayerWindow(_FakeSettings(), _FakeLyrics())
        self.addCleanup(self.window.deleteLater)
        self.cover = self.window.spin_cover

    def test_spin_cover_exists(self) -> None:
        self.assertIsInstance(self.cover, _SpinCover)
        self.assertEqual(SPIN_COVER_SIZE, 36)
        self.assertEqual(SPIN_TICK_MS, 33)
        self.assertEqual(SPIN_SPEED_DPS, 90.0)

    def test_window_size_unchanged(self) -> None:
        self.assertEqual(self.window.width(), 440)
        self.assertEqual(self.window.height(), 260)
        self.assertEqual((MINI_WIDTH, MINI_HEIGHT), (440, 260))

    def test_playing_rotates_and_pause_decays_to_stop(self) -> None:
        self.window.set_state("PLAYING")
        self.assertEqual(self.cover._target_speed, SPIN_SPEED_DPS)
        self.assertTrue(self.cover._timer.isActive())
        before = self.cover._angle
        for _ in range(5):
            self.cover._tick()
        self.assertGreater(self.cover._angle, before, "播放态必须转起来")

        self.window.set_state("PAUSED")
        self.assertEqual(self.cover._target_speed, 0.0)
        for _ in range(200):
            if not self.cover._timer.isActive():
                break
            self.cover._tick()
        self.assertFalse(self.cover._timer.isActive(), "缓停后定时器应自动停")
        self.assertEqual(self.cover._speed, 0.0)

    def test_loading_state_also_spins(self) -> None:
        """LOADING 与 PLAYING 同样按「播放中」处理（与 ASIO 探测口径一致）。"""
        self.window.set_state("LOADING")
        self.assertEqual(self.cover._target_speed, SPIN_SPEED_DPS)
        self.window.set_state("IDLE")
        self.assertEqual(self.cover._target_speed, 0.0)

    def test_hide_stops_timer_show_resumes(self) -> None:
        self.window.set_state("PLAYING")
        self.window.show()
        _APP.processEvents()
        self.assertTrue(self.cover._timer.isActive())
        self.window.hide()
        self.assertFalse(self.cover._timer.isActive(), "hideEvent 应停表")
        self.window.show()
        self.assertTrue(self.cover._timer.isActive(), "重新显示且播放中应恢复")
        self.window.hide()

    def test_cover_from_track_bytes(self) -> None:
        pixmap = QPixmap(8, 8)
        pixmap.fill(Qt.red)
        buffer = QBuffer()
        buffer.open(QBuffer.WriteOnly)
        pixmap.save(buffer, "PNG")
        data = bytes(buffer.data())

        self.window.set_track({"title": "海阔天空", "cover": data})
        self.assertIsNotNone(self.cover._pixmap)
        self.window.set_track({"title": "无封面"})
        self.assertIsNone(self.cover._pixmap)
        self.window.set_track(None)
        self.assertIsNone(self.cover._pixmap)
        self.window.set_track({"title": "坏图", "cover": b"not-an-image"})
        self.assertIsNone(self.cover._pixmap)
        self.cover.grab()  # 无封面也走一遍 paintEvent，不崩

    def test_silence_hook_restored(self) -> None:
        """本文件结束时必须把全局钩子恢复为 None，避免污染其它用例。"""
        self.assertIsNone(dev_mod._PLAYING_QUERY)


# ---------------------------------------------------------------- 5. 版本


class QaVersion182(unittest.TestCase):
    def test_three_places_say_1_8_2(self) -> None:
        import seqaudio

        spec = importlib.util.spec_from_file_location(
            "qa_meta", ROOT / "installer" / "_meta.py")
        assert spec is not None
        meta = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(meta)
        with open(ROOT / "pyproject.toml", "rb") as handle:
            pyproject = tomllib.load(handle)

        self.assertEqual(seqaudio.__version__, "1.8.2")
        self.assertEqual(meta.VERSION, "1.8.2")
        self.assertEqual(meta.PAYLOAD, "音序管家-v1.8.2-x86-x64.7z")
        self.assertEqual(pyproject["project"]["version"], "1.8.2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
