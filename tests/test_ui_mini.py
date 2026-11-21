"""迷你播放条（v1.7.2 重构）产品侧用例。

覆盖用户点名的六项：按钮不被裁切、播放键不是整块矩形、功能齐备、
透明度 10–100（默认 80）、窗口三键、歌词当前行高亮与主题跟随。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QAbstractAnimation, QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.ui import i18n, theme  # noqa: E402
from seqaudio.ui.mini_player import (  # noqa: E402
    CARD_RADIUS,
    MINI_HEIGHT,
    MINI_WIDTH,
    OPACITY_DEFAULT,
    OPACITY_MAX,
    OPACITY_MIN,
    MiniPlayerWindow,
)

_APP = QApplication.instance() or QApplication([])


class _FakeSettings:
    def __init__(self, opacity: int = OPACITY_DEFAULT) -> None:
        self.playback_mini_opacity = opacity
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


class _FakeLyrics:
    def __init__(self) -> None:
        self._lines: list[tuple[float, str]] = []
        self._current = -1


def _make(opacity: int = OPACITY_DEFAULT) -> tuple[MiniPlayerWindow, _FakeSettings, _FakeLyrics]:
    settings = _FakeSettings(opacity)
    lyrics = _FakeLyrics()
    window = MiniPlayerWindow(settings, lyrics)
    return window, settings, lyrics


def _press(window: MiniPlayerWindow, x: float, y: float) -> None:
    event = QMouseEvent(
        QEvent.MouseButtonPress,
        QPointF(x, y),
        QPointF(x, y),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    window.mousePressEvent(event)


class TestMiniStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.window, self.settings, self.lyrics = _make()

    def tearDown(self) -> None:
        self.window.deleteLater()

    def test_geometry_and_rounded_card(self) -> None:
        self.assertEqual((self.window.width(), self.window.height()), (MINI_WIDTH, MINI_HEIGHT))
        self.assertTrue(self.window.windowFlags() & Qt.FramelessWindowHint)
        self.assertTrue(self.window.testAttribute(Qt.WA_TranslucentBackground),
                        "圆角必须靠透明顶层 + 自绘，QSS 裁不了顶层窗口")
        self.assertEqual(self.window.card.objectName(), "miniCard")
        self.assertGreater(CARD_RADIUS, 0)
        self.assertIsNotNone(self.window.card.graphicsEffect(), "应有发光阴影")

    def test_all_controls_exist_and_visible(self) -> None:
        self.window.show()
        _APP.processEvents()
        try:
            controls = {
                "上一曲": self.window.btn_prev,
                "播放": self.window.btn_play,
                "停止": self.window.btn_stop,
                "下一曲": self.window.btn_next,
                "模式": self.window.btn_mode,
                "视图切换": self.window.btn_view,
                "最小化": self.window.btn_min,
                "最大化": self.window.btn_max,
                "关闭": self.window.btn_close,
                "音量图标": self.window.btn_volume,
            }
            for name, widget in controls.items():
                with self.subTest(control=name):
                    self.assertTrue(widget.isVisible(), f"{name} 不可见（被裁切/未布局）")
                    self.assertGreater(widget.width(), 0)
            for name, widget in (
                ("进度", self.window.progress),
                ("音量", self.window.volume_slider),
                ("透明度", self.window.opacity_slider),
            ):
                with self.subTest(control=name):
                    self.assertTrue(widget.isVisible(), f"{name} 滑杆不可见")
        finally:
            self.window.hide()

    def test_play_button_is_round_and_small(self) -> None:
        button = self.window.btn_play
        self.assertEqual(button.width(), button.height(), "播放键应为正圆")
        self.assertLessEqual(button.width(), 44, "播放键不得放大成整块矩形（整块蓝回归）")
        self.assertEqual(button.icon_key, "play")

    def test_view_button_left_of_prev(self) -> None:
        self.window.show()
        _APP.processEvents()
        try:
            self.assertLess(self.window.btn_view.x(), self.window.btn_prev.x(),
                            "切换按钮必须在上一曲左侧")
        finally:
            self.window.hide()

    def test_glow_animation_stops_when_hidden(self) -> None:
        self.window.show()
        _APP.processEvents()
        self.assertEqual(self.window._glow.state(), QAbstractAnimation.Running)
        self.window.hide()
        _APP.processEvents()
        self.assertEqual(self.window._glow.state(), QAbstractAnimation.Stopped)


class TestMiniPlaybackSignals(unittest.TestCase):
    def setUp(self) -> None:
        self.window, self.settings, self.lyrics = _make()
        self.calls: dict[str, int] = {}

    def tearDown(self) -> None:
        self.window.deleteLater()

    def _wire(self) -> None:
        for name in (
            "playRequested", "pauseRequested", "stopRequested",
            "prevRequested", "nextRequested", "viewModeToggled", "closeRequested",
        ):
            signal = getattr(self.window, name)
            signal.connect(lambda n=name: self.calls.__setitem__(n, self.calls.get(n, 0) + 1))

    def test_transport_buttons_emit(self) -> None:
        self._wire()
        self.window.set_nav_enabled(True)
        self.window.set_state("PLAYING")   # 停止键在 IDLE 下按设计置灰
        self.window.btn_prev.click()
        self.window.btn_next.click()
        self.window.btn_stop.click()
        self.assertEqual(self.calls.get("prevRequested"), 1)
        self.assertEqual(self.calls.get("nextRequested"), 1)
        self.assertEqual(self.calls.get("stopRequested"), 1)

    def test_play_button_toggles_by_state(self) -> None:
        self._wire()
        self.window.set_state("IDLE")
        self.window.btn_play.click()
        self.assertEqual(self.calls.get("playRequested"), 1)
        self.window.set_state("PLAYING")
        self.assertEqual(self.window.btn_play.icon_key, "pause")
        self.window.btn_play.click()
        self.assertEqual(self.calls.get("pauseRequested"), 1)

    def test_window_buttons(self) -> None:
        self._wire()
        self.window.btn_close.click()
        self.window.btn_max.click()
        self.assertEqual(self.calls.get("closeRequested"), 1)
        self.assertEqual(self.calls.get("viewModeToggled"), 1)

    def test_seek_and_volume(self) -> None:
        seeks: list[int] = []
        volumes: list[float] = []
        self.window.seekRequested.connect(seeks.append)
        self.window.volumeChanged.connect(volumes.append)
        self.window.progress.jumpRequested.emit(1234)
        self.window.volume_slider.setValue(-6 * 10)
        self.assertEqual(seeks, [1234])
        self.assertTrue(volumes)
        self.assertAlmostEqual(volumes[-1], -6.0, delta=0.01)

    def test_mode_cycles(self) -> None:
        modes: list[str] = []
        self.window.modeChanged.connect(modes.append)
        self.window.set_mode("sequential")
        for _ in range(4):
            self.window.btn_mode.click()
        self.assertEqual(modes, ["shuffle", "repeat_one", "repeat_all", "sequential"])
        self.assertEqual(self.window.btn_mode.icon_key, "mode_seq")


class TestMiniOpacity(unittest.TestCase):
    def setUp(self) -> None:
        self.window, self.settings, self.lyrics = _make()

    def tearDown(self) -> None:
        self.window.deleteLater()

    def test_default_is_eighty(self) -> None:
        self.assertAlmostEqual(self.window.windowOpacity(), 0.8, delta=0.02)
        self.assertEqual(self.window.opacity_slider.minimum(), OPACITY_MIN)
        self.assertEqual(self.window.opacity_slider.maximum(), OPACITY_MAX)
        self.assertEqual(self.window.opacity_value.text(), "80%")

    def test_slider_changes_opacity_and_persists(self) -> None:
        self.window.opacity_slider.setValue(30)
        self.assertAlmostEqual(self.window.windowOpacity(), 0.3, delta=0.02)
        self.window._persist_opacity()
        self.assertEqual(self.settings.playback_mini_opacity, 30)
        self.assertEqual(self.settings.saved, 1)

    def test_opacity_clamped(self) -> None:
        self.window.apply_opacity(0)
        self.assertAlmostEqual(self.window.windowOpacity(), OPACITY_MIN / 100.0, delta=0.02)
        self.window.apply_opacity(999)
        self.assertAlmostEqual(self.window.windowOpacity(), 1.0, delta=0.02)


class TestMiniLyricsAndTheme(unittest.TestCase):
    def setUp(self) -> None:
        self.window, self.settings, self.lyrics = _make()

    def tearDown(self) -> None:
        self.window.deleteLater()

    def test_three_lines_and_highlight(self) -> None:
        self.lyrics._lines = [(0, "第一行"), (1000, "当前行"), (2000, "第三行"), (3000, "第四行")]
        self.lyrics._current = 1
        self.window.update_lyrics()
        self.assertEqual([l.text() for l in self.window.lyric_labels], ["第一行", "当前行", "第三行"])
        self.assertTrue(self.window.lyric_labels[1].font().bold(), "当前行应加粗")
        self.assertIn(theme.ACCENT, self.window.lyric_labels[1].styleSheet())
        self.assertIn(theme.FG_DIM, self.window.lyric_labels[0].styleSheet())

    def test_empty_lyrics_hint(self) -> None:
        self.lyrics._lines = []
        self.lyrics._current = -1
        self.window.update_lyrics()
        self.assertEqual(self.window.lyric_labels[0].text(), i18n.tr("lyr_mini_empty"))
        self.assertEqual(self.window.lyric_labels[1].text(), "")

    def test_theme_refresh_uses_new_accents(self) -> None:
        self.window.refresh_theme()
        self.assertIn(theme.ACCENT, self.window.styleSheet())
        self.assertEqual(self.window._shadow.color().name(), theme.ACCENT.lower())

    def test_all_locales_have_texts(self) -> None:
        original = i18n.current()
        try:
            for locale in i18n.locale_names():
                i18n.set_locale(locale)
                self.window.refresh_texts()
                with self.subTest(locale=locale):
                    for widget in (self.window.btn_play, self.window.btn_prev,
                                   self.window.btn_next, self.window.btn_stop,
                                   self.window.btn_view, self.window.btn_close,
                                   self.window.opacity_label):
                        self.assertTrue(widget.toolTip() if widget is not self.window.opacity_label
                                        else widget.text(), f"{locale} 文案为空")
        finally:
            i18n.set_locale(original)


class TestMiniDrag(unittest.TestCase):
    def setUp(self) -> None:
        self.window, self.settings, self.lyrics = _make()
        self.window.show()
        _APP.processEvents()

    def tearDown(self) -> None:
        self.window.hide()
        self.window.deleteLater()

    def test_drag_on_blank_area(self) -> None:
        self.assertFalse(self.window._dragging)
        _press(self.window, 4, 4)
        self.assertTrue(self.window._dragging, "空白处按下应开始拖动")
        self.window._dragging = False

    def test_drag_not_started_on_button(self) -> None:
        button = self.window.btn_play
        center = button.geometry().center()
        _press(self.window, center.x(), center.y())
        self.assertFalse(self.window._dragging, "按钮上的按下不应触发整窗拖动")


if __name__ == "__main__":
    unittest.main(verbosity=2)
