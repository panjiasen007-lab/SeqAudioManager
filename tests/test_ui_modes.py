"""界面形态回归（最小化 / 托盘 / 迷你条）：全部离屏，无音频设备依赖。

运行：``python tests/test_ui_modes.py`` 或 ``python -m unittest tests.test_ui_modes -q``。

覆盖
----
* 需求 1：最小化（hideEvent）后 100 ms 轮询 timer 仍 active；
* 需求 2：close_action=tray 只隐藏不退出，=quit 走完整清理；托盘菜单三项；
* 需求 3：切换按钮位于「上一曲」左侧、两个形态互斥可见、迷你条三行歌词与主题刷新。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.ui import i18n, theme  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402

#: 本次新增的全部文案键（必须 8 语言齐全）
NEW_I18N_KEYS = (
    "ui_close_action",
    "ui_close_tray",
    "ui_close_quit",
    "ui_tray_show",
    "ui_tray_playpause",
    "ui_tray_quit",
    "ui_view_mini",
    "ui_view_main",
    "ui_mini_title",
    "lyr_mini_empty",
    "ui_restored_from_tray",
)


def _app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def _controls(widget) -> list:
    """按水平布局顺序取出播放条的传输控件。"""
    layout = widget.controls_widget.layout()
    return [layout.itemAt(i).widget() for i in range(layout.count())]


class UiModesTest(unittest.TestCase):
    """全类共用一个窗口：离屏下反复 setStyleSheet 会卡住 Qt 事件循环。

    每个用例在 setUp 里显式复位可变状态（_quitting / 关闭行为 / 脏标记 /
    歌词 / 视图），且所有会真正关引擎的路径都打桩，保证用例彼此隔离。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _app()
        cls.window = MainWindow(Settings(offline_mode=True, online_enabled=False))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.settings.ui_close_action = "quit"
        with patch.object(cls.window.settings_panel, "is_dirty", return_value=False):
            cls.window.closeEvent(QCloseEvent())
        cls.window.hide()

    def setUp(self) -> None:
        window = self.window
        window._quitting = False
        window.settings.ui_close_action = "tray"
        window.settings.playback_mini_bar = False
        window.settings_panel._set_dirty(False)
        window.player_lyrics.clear()
        window.mini_player.hide()
        window.show()
        self.app.processEvents()

    # ------------------------------------------------------------ 需求 1

    def test_hide_keeps_poll_timer_active(self) -> None:
        """最小化 / 隐藏后轮询必须常驻（EOF 续播与状态回传的唯一通道）。"""
        self.assertTrue(self.window.player_bridge.timer.isActive())
        self.window.hide()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(
            self.window.player_bridge.timer.isActive(),
            "hideEvent 停掉了 100ms 轮询：最小化后播放会失效",
        )

    # ------------------------------------------------------------ 需求 2

    def test_close_tray_only_hides_without_quit(self) -> None:
        """托盘分支：只隐藏，不保存会话、不 shutdown、不移除 listener、不停轮询。"""
        self.window.settings.ui_close_action = "tray"
        with (
            patch.object(self.window.settings_panel, "is_dirty", return_value=False),
            patch.object(self.window, "_save_playback_session") as save,
            patch.object(self.window.player_bridge, "shutdown") as shutdown,
            patch("seqaudio.ui.main_window.remove_listener") as remove,
        ):
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertFalse(event.isAccepted())
            save.assert_not_called()
            shutdown.assert_not_called()
            remove.assert_not_called()
        self.assertFalse(self.window.isVisible())
        self.assertFalse(self.window._quitting)
        self.assertTrue(self.window.player_bridge.timer.isActive())

    def test_close_quit_runs_full_cleanup(self) -> None:
        """直接退出分支：保存视图/会话、移除 listener、接受关闭事件。"""
        self.window.settings.ui_close_action = "quit"
        with (
            patch.object(self.window.settings_panel, "is_dirty", return_value=False),
            patch.object(self.window, "_save_view_state") as view,
            patch.object(self.window, "_save_playback_session") as session,
            patch("seqaudio.ui.main_window.remove_listener") as remove,
        ):
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.assertTrue(event.isAccepted())
            view.assert_called_once_with()
            session.assert_called_once_with()
            remove.assert_called_once_with(self.window._log_listener)
        self.assertTrue(self.window._quitting)

    def test_quit_from_tray_reuses_cleanup(self) -> None:
        """托盘「退出」复用同一段清理逻辑，不复制第二份。"""
        with (
            patch.object(self.window, "_finish_quit") as finish,
            patch("seqaudio.ui.main_window.QApplication") as qapp_cls,
        ):
            self.window.quit_from_tray()
            finish.assert_called_once_with()
            qapp_cls.instance.return_value.quit.assert_called_once_with()

    def test_tray_menu_has_required_actions(self) -> None:
        """右键菜单：显示主窗口 / 播放暂停 / 退出。"""
        actions = [
            action
            for action in self.window.tray_icon.contextMenu().actions()
            if not action.isSeparator()
        ]
        self.assertEqual(
            [action.text() for action in actions],
            [
                i18n.tr("ui_tray_show"),
                i18n.tr("ui_tray_playpause"),
                i18n.tr("ui_tray_quit"),
            ],
        )
        self.assertIs(self.window.tray_show_action, actions[0])
        self.assertIs(self.window.tray_play_action, actions[1])
        self.assertIs(self.window.tray_quit_action, actions[2])

    def test_close_action_roundtrip_in_settings(self) -> None:
        """设置面板「关闭窗口时」与 Settings 双向同步。"""
        combo = self.window.settings_panel.close_action_combo
        for value in ("quit", "tray"):
            self.window.settings.ui_close_action = value
            self.window.settings_panel.load_from(self.window.settings)
            self.assertEqual(combo.currentData(), value)
            combo.setCurrentIndex(combo.findData(value))
            self.window.settings_panel.apply_to(self.window.settings)
            self.assertEqual(self.window.settings.ui_close_action, value)

    # ------------------------------------------------------------ 需求 3

    def test_view_mode_button_precedes_prev_on_both_bars(self) -> None:
        """主界面与迷你条的切换按钮都在「上一曲」左边。"""
        bar = self.window.player_bar
        controls = _controls(bar)
        self.assertIn(bar.btn_view_mode, controls)
        self.assertIn(bar.btn_prev, controls)
        self.assertLess(
            controls.index(bar.btn_view_mode),
            controls.index(bar.btn_prev),
            "主界面切换按钮必须在「上一曲」左边",
        )
        # 迷你条是自绘卡片（不再复用 PlayerBar），按几何位置验同一契约
        mini = self.window.mini_player
        mini.show()
        self.app.processEvents()
        try:
            self.assertLess(
                mini.btn_view.x(), mini.btn_prev.x(),
                "迷你条切换按钮必须在「上一曲」左边",
            )
        finally:
            mini.hide()

    def test_switch_between_main_and_mini_keeps_timer(self) -> None:
        """两个形态互斥可见，切换过程中轮询始终 active。"""
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.mini_player.isVisible())
        self.window.player_bar.viewModeToggled.emit()
        self.app.processEvents()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.mini_player.isVisible())
        self.assertTrue(self.window.player_bridge.timer.isActive())
        self.assertTrue(self.window.settings.playback_mini_bar)

        self.window.mini_player.viewModeToggled.emit()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.mini_player.isVisible())
        self.assertTrue(self.window.player_bridge.timer.isActive())
        self.assertFalse(self.window.settings.playback_mini_bar)

    def test_restore_from_tray_hides_mini(self) -> None:
        """托盘「显示主窗口」同时隐藏迷你条，且轮询不停。"""
        self.window.switch_to_mini_window()
        self.app.processEvents()
        self.window.restore_from_tray()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible())
        self.assertFalse(self.window.mini_player.isVisible())
        self.assertTrue(self.window.player_bridge.timer.isActive())

    def test_mini_lyrics_shows_current_and_neighbours(self) -> None:
        """三行歌词 = 上一行 / 当前行 / 下一行，当前行主色加粗。"""
        lyrics = self.window.player_lyrics
        lyrics._lines = [
            (0, "第一行"),
            (1000, "第二行"),
            (2000, "第三行"),
            (3000, "第四行"),
        ]
        lyrics._current = 1
        labels = self.window.mini_player.lyric_labels
        self.window.mini_player.update_lyrics()
        self.assertEqual([label.text() for label in labels], ["第一行", "第二行", "第三行"])
        self.assertTrue(labels[1].font().bold())
        self.assertFalse(labels[0].font().bold())
        self.assertIn(theme.ACCENT, labels[1].styleSheet())
        self.assertIn(theme.FG_DIM, labels[0].styleSheet())

    def test_mini_lyrics_empty_hint(self) -> None:
        """无歌词时第一行给提示，其余两行留空。"""
        self.window.player_lyrics._lines = []
        self.window.player_lyrics._current = -1
        labels = self.window.mini_player.lyric_labels
        self.window.mini_player.update_lyrics()
        self.assertEqual(labels[0].text(), i18n.tr("lyr_mini_empty"))
        self.assertEqual([label.text() for label in labels[1:]], ["", ""])

    def test_mini_refreshes_on_theme_change(self) -> None:
        """主题切换必须同步刷新迷你条（与 player_bar 同样对待）。"""
        with patch.object(self.window.mini_player, "refresh_theme") as refresh:
            self.window.apply_theme(theme.CURRENT, persist=False, quiet=True)
            refresh.assert_called_once_with()

    # ------------------------------------------------------------ i18n

    def test_new_i18n_keys_complete_in_eight_locales(self) -> None:
        """本轮 11 条新文案在 8 种语言下均非空。"""
        from seqaudio.ui.i18n import LOCALE_NAMES, _TRANSLATIONS

        self.assertEqual(len(LOCALE_NAMES), 8)
        for key in NEW_I18N_KEYS:
            entry = _TRANSLATIONS.get(key)
            self.assertIsNotNone(entry, f"缺少词条 {key}")
            for locale in LOCALE_NAMES:
                self.assertTrue(entry.get(locale), f"{key} 缺少 {locale} 译文")


if __name__ == "__main__":
    unittest.main(verbosity=2)
