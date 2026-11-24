"""v1.8.2 五项改动的验收用例。

覆盖：
* ASIO 可用性：空壳驱动（check 通过但开流失败）不再误报；播放中跳过开流验证。
  （核心逻辑用例在 ``tests/test_asio_backend.py``，本文件补桥接/面板侧。）
* 随包字体精简为 8 款：choices 恰好 8、被删字体回退默认、资产文件同步。
* 「字体与外观」页预览 label 移除后面板构建正常。
* 迷你播放条左上角旋转封面：控件存在、播放态旋转、暂停缓停、封面随曲目更新。
* 版本号三处一致为 1.8.2。

运行：``PYTHONPATH=src python -m pytest tests/test_v182.py -q``
"""

from __future__ import annotations

import importlib.util
import os
import sys
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

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
    SPIN_COVER_SIZE,
    SPIN_SPEED_DPS,
    MiniPlayerWindow,
    _SpinCover,
)
from seqaudio.ui.settings_panel import SettingsPanel  # noqa: E402

_APP = QApplication.instance() or QApplication([])


# ---------------------------------------------------------------- 字体（任务 2）


class TestFontReduction(unittest.TestCase):
    """随包字体精简为 8 款：5 界面 + 3 等宽。"""

    def test_ui_choices_are_exactly_five(self) -> None:
        ids = [choice.id for choice in fonts.UI_CHOICES]
        self.assertEqual(
            ids, ["noto", "wenkai", "smiley", "inter", "plex_sans"]
        )

    def test_mono_choices_are_exactly_three(self) -> None:
        ids = [choice.id for choice in fonts.MONO_CHOICES]
        self.assertEqual(ids, ["jetbrains", "fira_code", "cascadia"])

    def test_total_is_eight(self) -> None:
        self.assertEqual(len(fonts.UI_CHOICES) + len(fonts.MONO_CHOICES), 8)

    def test_removed_ui_font_ids_fall_back_to_noto(self) -> None:
        """旧配置里存的被删字体 id 必须回退默认，不许崩。"""
        for removed in ("mashan", "zcool_qk", "source_sans", "fira_sans",
                        "public_sans", "manrope", "work_sans", "rubik",
                        "lato", "noto_sans"):
            choice = fonts._choice_or_default(fonts.UI_CHOICES, removed)
            self.assertEqual(choice.id, "noto", f"{removed} 应回退到 noto")

    def test_removed_mono_font_ids_fall_back_to_jetbrains(self) -> None:
        for removed in ("plex_mono", "source_code_pro"):
            choice = fonts._choice_or_default(fonts.MONO_CHOICES, removed)
            self.assertEqual(choice.id, "jetbrains", f"{removed} 应回退到 jetbrains")

    def test_bundled_font_assets_match_choices(self) -> None:
        """assets/fonts 里只留 8 款字体与其 OFL 文本。"""
        fonts_dir = ROOT / "assets" / "fonts"
        self.assertTrue(fonts_dir.is_dir())
        names = sorted(entry.name for entry in fonts_dir.iterdir())
        self.assertEqual(
            names,
            sorted([
                "CascadiaCode.ttf", "OFL-CascadiaCode.txt",
                "FiraCode.ttf", "OFL-FiraCode.txt",
                "IBMPlexSans.ttf", "OFL-IBMPlexSans.txt",
                "Inter-Variable.ttf", "OFL-Inter.txt",
                "JetBrainsMono-Bold.ttf", "JetBrainsMono-Regular.ttf",
                "OFL-JetBrainsMono.txt",
                "LXGWWenKai-Regular.ttf", "OFL-LXGWWenKai.txt",
                "NotoSansSC-Bold.otf", "NotoSansSC-Regular.otf",
                "OFL-NotoSansSC.txt",
                "SmileySans-Oblique.ttf", "OFL-SmileySans.txt",
            ]),
        )
        for removed in ("MaShanZheng.ttf", "ZCOOLQingKeHuangYou.ttf",
                        "SourceSans3.ttf", "FiraSans.ttf", "PublicSans.ttf",
                        "Manrope.ttf", "WorkSans.ttf", "Rubik.ttf", "Lato.ttf",
                        "NotoSans.ttf", "IBMPlexMono.ttf", "SourceCodePro.ttf"):
            self.assertFalse((fonts_dir / removed).exists(),
                             f"{removed} 应已删除")


# ---------------------------------------------------------------- 外观页（任务 3）


class TestAppearancePreviewRemoved(unittest.TestCase):
    """「字体与外观」页不再有预览 label，构建/换档/回填都不许崩。"""

    def setUp(self) -> None:
        self.settings = Settings(offline_mode=True)
        self.panel = SettingsPanel(self.settings)
        self.addCleanup(self.panel.deleteLater)

    def test_preview_labels_are_gone(self) -> None:
        self.assertFalse(hasattr(self.panel, "font_preview"))
        self.assertFalse(hasattr(self.panel, "mono_preview"))
        self.assertFalse(hasattr(self.panel, "_refresh_appearance_preview"))

    def test_appearance_change_does_not_crash(self) -> None:
        self.panel.ui_font_combo.setCurrentIndex(1)
        self.panel.mono_font_combo.setCurrentIndex(1)
        self.panel.font_scale_combo.setCurrentIndex(2)
        self.panel.load_from(Settings(offline_mode=True))  # 旧版这里会刷预览


# ---------------------------------------------------------------- ASIO 下拉（任务 1③）


def _asio_entry(name: str = "Realtek ASIO") -> dict:
    return {
        "name": name,
        "label": f"ASIO · {name}",
        "channels": 2,
        "backend": "asio",
        "is_default": False,
    }


class TestAsioDeviceCombo(unittest.TestCase):
    """ASIO 下没有「系统默认设备」：直接列实际设备，空则置灰说明。"""

    def setUp(self) -> None:
        self.panel = SettingsPanel(Settings(offline_mode=True))
        self.addCleanup(self.panel.deleteLater)
        self.panel.backend_combo.setCurrentIndex(
            self.panel.backend_combo.findData("asio")
        )

    def test_asio_lists_devices_without_default_placeholder(self) -> None:
        self.panel.set_output_devices([_asio_entry()], scoped=True)
        combo = self.panel.device_combo
        self.assertTrue(combo.isEnabled())
        self.assertEqual(combo.count(), 1)
        self.assertEqual(combo.itemText(0), "Realtek ASIO")
        for index in range(combo.count()):
            self.assertNotEqual(combo.itemText(index), i18n.tr_text("系统默认设备"))

    def test_asio_without_devices_is_disabled_with_hint(self) -> None:
        self.panel.set_output_devices([], scoped=True)
        combo = self.panel.device_combo
        self.assertFalse(combo.isEnabled())
        self.assertEqual(combo.count(), 1)
        self.assertEqual(combo.itemText(0), i18n.tr("dev_asio_no_device"))
        self.assertTrue(i18n.tr("dev_asio_no_device"))

    def test_non_asio_keeps_default_placeholder(self) -> None:
        self.panel.backend_combo.setCurrentIndex(
            self.panel.backend_combo.findData("wasapi")
        )
        self.panel.set_output_devices([], scoped=True)
        combo = self.panel.device_combo
        self.assertTrue(combo.isEnabled())
        self.assertEqual(combo.itemText(0), i18n.tr_text("系统默认设备"))

    def test_i18n_key_has_all_eight_locales(self) -> None:
        entry = i18n._TRANSLATIONS.get("dev_asio_no_device")
        self.assertIsNotNone(entry)
        for locale in i18n.LOCALE_NAMES:
            self.assertTrue(entry.get(locale), f"缺 {locale} 译文")


class TestBridgeAsioPlayingGuard(unittest.TestCase):
    """桥接层把「播放中」注册给 devices：开流验证在播放时自动跳过。"""

    def test_bridge_registers_playing_query(self) -> None:
        from seqaudio.ui.player_bridge import PlayerBridge

        bridge = PlayerBridge(Settings(offline_mode=True))
        try:
            self.assertTrue(dev_mod.playback_in_progress() is False)
            # 引擎未播放 → 钩子返回 False；直接把钩子换成 True 验证通路
            self.assertEqual(dev_mod._PLAYING_QUERY, bridge._engine_playing)
            with mock.patch.object(
                PlayerBridge, "state", lambda self: "PLAYING"
            ):
                self.assertTrue(dev_mod.playback_in_progress())
        finally:
            bridge.shutdown()
        self.assertIsNone(dev_mod._PLAYING_QUERY, "shutdown 后钩子应被摘除")


# ---------------------------------------------------------------- 迷你条转盘（任务 4）


class _FakeSettings:
    def __init__(self) -> None:
        self.playback_mini_opacity = 80
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


class _FakeLyrics:
    def __init__(self) -> None:
        self._lines: list[tuple[float, str]] = []
        self._current = -1


def _png_bytes() -> bytes:
    """造一张 8x8 的小 PNG，验证 set_cover 的解码路径。"""
    pixmap = QPixmap(8, 8)
    pixmap.fill(Qt.red)
    buffer = QBuffer()
    buffer.open(QBuffer.WriteOnly)
    pixmap.save(buffer, "PNG")
    return bytes(buffer.data())


class TestMiniSpinCover(unittest.TestCase):
    """左上角黑胶转盘：存在、播放旋转、暂停缓停、封面随曲目更新。"""

    def setUp(self) -> None:
        self.window = MiniPlayerWindow(_FakeSettings(), _FakeLyrics())
        self.addCleanup(self.window.deleteLater)

    def test_spin_cover_exists_and_sized(self) -> None:
        self.assertIsInstance(self.window.spin_cover, _SpinCover)
        self.assertEqual(
            self.window.spin_cover.width(), SPIN_COVER_SIZE
        )
        self.assertGreaterEqual(SPIN_COVER_SIZE, 34)
        self.assertLessEqual(SPIN_COVER_SIZE, 38)

    def test_playing_spins_and_pause_decays(self) -> None:
        cover = self.window.spin_cover
        self.window.set_state("PLAYING")
        self.assertEqual(cover._target_speed, SPIN_SPEED_DPS)
        self.assertTrue(cover._timer.isActive())
        angle_before = cover._angle
        cover._tick()
        self.assertGreater(cover._angle, angle_before, "播放态必须转起来")

        self.window.set_state("PAUSED")
        self.assertEqual(cover._target_speed, 0.0)
        for _ in range(200):  # 缓停：速度衰减到 0 后定时器自己停
            if not cover._timer.isActive():
                break
            cover._tick()
        self.assertFalse(cover._timer.isActive(), "缓停后定时器应停止")
        self.assertEqual(cover._speed, 0.0)

    def test_stop_and_no_track_stop_rotation(self) -> None:
        self.window.set_state("PLAYING")
        self.window.set_state("STOPPED")
        self.assertEqual(self.window.spin_cover._target_speed, 0.0)
        self.window.set_state("IDLE")
        self.assertEqual(self.window.spin_cover._target_speed, 0.0)

    def test_cover_follows_track(self) -> None:
        data = _png_bytes()
        self.window.set_track({"title": "海阔天空", "cover": data})
        self.assertIsNotNone(self.window.spin_cover._pixmap)
        # 换成没有封面的曲目 → 回默认纹理
        self.window.set_track({"title": "无封面", "path": "x.flac"})
        self.assertIsNone(self.window.spin_cover._pixmap)
        self.window.set_track(None)
        self.assertIsNone(self.window.spin_cover._pixmap)

    def test_bad_cover_bytes_fall_back_to_texture(self) -> None:
        self.window.set_track({"title": "坏图", "cover": b"not-an-image"})
        self.assertIsNone(self.window.spin_cover._pixmap)

    def test_painting_does_not_crash_with_and_without_cover(self) -> None:
        self.window.set_track({"title": "t", "cover": _png_bytes()})
        self.window.spin_cover.grab()  # 强制走一遍 paintEvent
        self.window.set_track(None)
        self.window.spin_cover.grab()

    def test_hide_stops_timer_and_show_resumes(self) -> None:
        self.window.set_state("PLAYING")
        self.window.show()
        _APP.processEvents()
        self.assertTrue(self.window.spin_cover._timer.isActive())
        self.window.hide()
        self.assertFalse(self.window.spin_cover._timer.isActive(),
                         "窗口隐藏时转盘应停表")
        self.window.show()
        self.assertTrue(self.window.spin_cover._timer.isActive(),
                        "重新显示且在播放时应恢复旋转")
        self.window.hide()


# ---------------------------------------------------------------- 版本号（任务 5）


class TestVersion182(unittest.TestCase):
    """三处版本号一致，且都是 1.8.2。"""

    def test_versions_are_consistent(self) -> None:
        import seqaudio

        meta_spec = importlib.util.spec_from_file_location(
            "installer_meta", ROOT / "installer" / "_meta.py"
        )
        assert meta_spec is not None
        meta = importlib.util.module_from_spec(meta_spec)
        meta_spec.loader.exec_module(meta)

        with open(ROOT / "pyproject.toml", "rb") as handle:
            pyproject = tomllib.load(handle)

        self.assertEqual(seqaudio.__version__, "1.8.2")
        self.assertEqual(meta.VERSION, "1.8.2")
        self.assertEqual(meta.PAYLOAD, "音序管家-v1.8.2-x86-x64.7z")
        self.assertEqual(pyproject["project"]["version"], "1.8.2")


if __name__ == "__main__":
    unittest.main(verbosity=2)
