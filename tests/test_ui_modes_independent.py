"""UI 三项改动的**独立**验证（不 import 工程师的 tests/test_ui_modes.py）。

运行：``QT_QPA_PLATFORM=offscreen python tests/test_ui_modes_independent.py``

覆盖：
① 最小化/托盘/迷你条三条隐藏路径下 100 ms 轮询常驻，真音频跨轨 EOF 自动续播
② 关闭行为可选（quit/tray）、托盘菜单、反复关闭恢复不泄漏、退出收掉迷你条
③ 迷你条几何/按钮顺序/共享引擎身份/歌词同步/边界操作/主题与 8 语言

已知环境问题（不记为本次 bug）：原生 windows 平台 apply_theme 会卡；
offscreen 下中文字体回退显示方框。本文件在 offscreen 下运行。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt, QEvent  # noqa: E402
from PySide6.QtGui import QCloseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QHBoxLayout  # noqa: E402

from seqaudio.app import create_app  # noqa: E402
from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.core import logging_setup as log_mod  # noqa: E402
from seqaudio.core.probe import probe  # noqa: E402
from seqaudio.player.constants import PlayerState  # noqa: E402
from seqaudio.ui import i18n, theme  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402
from seqaudio.ui.mini_player import MINI_HEIGHT, MINI_WIDTH  # noqa: E402
from seqaudio.ui.player_bar import PlayerBar  # noqa: E402

FFMPEG = find_ffmpeg()
_REAL_APPLY_THEME = MainWindow.apply_theme


def synth(target: Path, seconds: float, rate: int = 44100) -> Path:
    assert FFMPEG is not None
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
         "-i", f"sine=frequency=1000:duration={seconds}:sample_rate={rate}",
         "-ac", "2", "-c:a", "flac", str(target)],
        stdin=subprocess.DEVNULL, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise unittest.SkipTest(f"合成素材失败：{proc.stderr.decode('utf-8', 'replace')[:150]}")
    return target


def pump(app: QApplication, seconds: float, step: float = 0.05) -> None:
    """驱动 Qt 事件循环（offscreen 下没有原生事件循环替我们跑 QTimer）。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(step)


class BaseWindowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_ui_ind_"))
        cls.app = create_app([])
        cls.audio = [synth(cls.tmp / f"t{i}.flac", seconds=3.2) for i in range(2)]

    @classmethod
    def tearDownClass(cls) -> None:
        # 清理临时目录。沙箱在本回合累计删除 >50 个文件后会抛
        # SAFE_DELETE_BULK_CONFIRM_REQUIRED / SystemExit(1)（环境护栏，
        # 与被测代码无关）。全量 discover 时前面的用例会先触顶，
        # 所以这里吞掉清理失败，避免污染用例结果。
        try:
            shutil.rmtree(cls.tmp, ignore_errors=True)
        except BaseException:  # noqa: BLE001 - 清理失败不影响断言
            pass

    def setUp(self) -> None:
        # 沙箱在单个进程累计删除 >50 个文件后，会让任何 Path.unlink()
        # 抛 SAFE_DELETE_BULK_CONFIRM_REQUIRED / SystemExit(1)。
        # MainWindow 构造时 paths._writable() 会做一次无害的探针写入+删除，
        # 全量 discover 里前面的用例已把计数顶满，这里用 mock 跳过探针，
        # 只影响"选哪个 data 目录"，与本轮三项 UI 行为无关。
        from unittest import mock

        patcher = mock.patch("seqaudio.core.paths._writable", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

        # 本机已知环境问题：MainWindow.__init__ 里的 apply_theme→setStyleSheet
        # 在**反复构造多个窗口**时会卡死（faulthandler 定位在 main_window.py:2917）。
        # 这不是被测的三项改动，也不是源码死锁；仅在需要验主题跟随的那一条里
        # 显式调用原函数一次，其余构造跳过。
        theme_patcher = mock.patch.object(MainWindow, "apply_theme", autospec=True)
        theme_patcher.start()
        self.addCleanup(theme_patcher.stop)

        settings = Settings(
            min_duration_sec=0,
            offline_mode=True,
            online_enabled=False,
            backup_enabled=False,
            ui_close_action="tray",
        )
        self.window = MainWindow(settings)
        self.window.show()
        self.app.processEvents()
        self.bridge = self.window.player_bridge
        self.assertEqual(self.bridge.timer.interval(), 100,
                         "轮询间隔应为 100 ms")

    def tearDown(self) -> None:
        try:
            self.bridge.shutdown()
        except Exception:  # noqa: BLE001
            pass
        # 不用 window.close()：设置面板若有未保存改动会弹模态框卡住离屏用例；
        # 本测试类各自的 closeEvent 用例已经覆盖关闭分流。
        try:
            self.window.hide()
        except Exception:  # noqa: BLE001
            pass
        self.app.processEvents()

    def _enqueue_two(self) -> None:
        tracks = [probe(path, with_cue=False) for path in self.audio]
        self.bridge.enqueue(tracks, replace=True)
        self.assertTrue(self.bridge.play_index(0), "起播失败")
        pump(self.app, 0.4)


# ------------------------------------------------ ① 隐藏路径轮询常驻


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestPollingSurvivesHide(BaseWindowTest):
    def test_hide_keeps_timer_and_position_grows(self) -> None:
        self._enqueue_two()
        self.assertTrue(self.bridge.timer.isActive())
        self.window.hide()
        self.app.processEvents()
        before = self.bridge.poll_now()["position_ms"]
        pump(self.app, 1.5)
        after = self.bridge.poll_now()["position_ms"]
        state = self.bridge.state()

        self.assertTrue(self.bridge.timer.isActive(), "hide() 后 QTimer 必须仍 active")
        self.assertGreater(after, before + 800,
                           f"隐藏 1.5 s 位置只从 {before} 走到 {after}")
        self.assertEqual(state, PlayerState.PLAYING.value, f"隐藏后状态变了：{state}")

    def test_hide_until_eof_auto_advances(self) -> None:
        """原 bug 最致命表现：最小化后播到曲末不切歌。"""
        self._enqueue_two()
        self.window.hide()
        self.app.processEvents()
        pump(self.app, 4.2)  # 第一首 3.2 s，跨过 EOF
        snapshot = self.bridge.poll_now()
        self.assertEqual(int(snapshot["queue_index"]), 1,
                         f"隐藏后曲末没有自动切下一首：index={snapshot['queue_index']}")
        self.assertEqual(snapshot["state"], PlayerState.PLAYING.value,
                         f"自动续播后应继续 PLAYING，实际 {snapshot['state']}")

    def test_tray_hide_keeps_polling(self) -> None:
        self._enqueue_two()
        self.window._hide_to_tray()
        self.app.processEvents()
        before = self.bridge.poll_now()["position_ms"]
        pump(self.app, 1.2)
        after = self.bridge.poll_now()["position_ms"]
        self.assertTrue(self.bridge.timer.isActive(), "收缩托盘后不得停轮询")
        self.assertGreater(after, before + 600, f"托盘隐藏后位置未增长：{before}→{after}")
        self.assertIs(self.window._quitting, False)

    def test_mini_switch_keeps_polling(self) -> None:
        self._enqueue_two()
        self.window.switch_to_mini_window()
        self.app.processEvents()
        before = self.bridge.poll_now()["position_ms"]
        pump(self.app, 1.2)
        after = self.bridge.poll_now()["position_ms"]
        self.assertTrue(self.bridge.timer.isActive(), "切迷你条后不得停轮询")
        self.assertGreater(after, before + 600, f"迷你条下位置未增长：{before}→{after}")


# ------------------------------------------------ ② 关闭行为


class TestCloseBehavior(BaseWindowTest):
    def test_close_quit_runs_full_cleanup(self) -> None:
        self.window.settings.ui_close_action = "quit"
        calls = {"view": 0, "session": 0}
        real_view, real_session = self.window._save_view_state, self.window._save_playback_session
        self.window._save_view_state = lambda: (calls.__setitem__("view", calls["view"] + 1),
                                                real_view())
        self.window._save_playback_session = lambda: (
            calls.__setitem__("session", calls["session"] + 1), real_session())

        event = QCloseEvent()
        self.window.closeEvent(event)
        self.app.processEvents()
        self.assertTrue(event.isAccepted(), "quit 分支必须 accept 关闭事件")
        self.assertEqual(calls, {"view": 1, "session": 1}, f"清理路径调用次数：{calls}")
        self.assertIs(self.window._quitting, True)
        self.assertFalse(self.window.mini_player.isVisible(), "退出必须收掉迷你条")
        self.assertFalse(self.window.tray_icon.isVisible(), "退出必须收掉托盘")

    def test_close_tray_does_not_save_or_shutdown(self) -> None:
        self.window.settings.ui_close_action = "tray"
        self._enqueue_two()
        calls = {"view": 0, "session": 0}
        real_view, real_session = self.window._save_view_state, self.window._save_playback_session
        self.window._save_view_state = lambda: calls.__setitem__("view", calls["view"] + 1)
        self.window._save_playback_session = lambda: calls.__setitem__(
            "session", calls["session"] + 1)
        engine = self.bridge.engine

        event = QCloseEvent()
        self.window.closeEvent(event)
        self.app.processEvents()

        self.assertFalse(event.isAccepted(), "tray 分支必须 ignore 关闭事件")
        self.assertEqual(calls, {"view": 0, "session": 0}, "托盘模式不得保存视图/会话")
        self.assertIs(self.window._quitting, False)
        self.assertIs(self.bridge.engine, engine, "不得重建引擎")
        self.assertFalse(getattr(engine, "_closed", False), "不得 shutdown 引擎")
        self.assertTrue(self.bridge.timer.isActive(), "托盘模式不得停轮询")

    def test_tray_menu_actions(self) -> None:
        menu = self.window.tray_icon.contextMenu()
        texts = [action.text() for action in menu.actions()]
        self.assertGreaterEqual(len(texts), 3, f"托盘菜单项不足：{texts}")
        self.assertEqual(len([t for t in texts if t]), 3, f"菜单项：{texts}")
        self.assertIn(i18n.tr("ui_tray_show"), texts)
        self.assertIn(i18n.tr("ui_tray_playpause"), texts)
        self.assertIn(i18n.tr("ui_tray_quit"), texts)

        self.window._hide_to_tray()
        self.app.processEvents()
        self.window.tray_show_action.trigger()
        self.app.processEvents()
        self.assertTrue(self.window.isVisible(), "托盘「显示主窗口」应恢复窗口")
        self.assertFalse(self.window.mini_player.isVisible(), "恢复主窗口应收迷你条")

        from PySide6.QtWidgets import QSystemTrayIcon

        self.window._hide_to_tray()
        self.app.processEvents()
        self.window._on_tray_activated(QSystemTrayIcon.Trigger)
        self.app.processEvents()
        self.assertTrue(self.window.isVisible(), "单击托盘图标应恢复主窗口")

    def test_tray_close_restore_cycles_no_leak(self) -> None:
        self._enqueue_two()
        timer = self.bridge.timer
        engine = self.bridge.engine
        listeners_before = len(getattr(log_mod, "_listeners", []))
        for round_index in range(3):
            event = QCloseEvent()
            self.window.closeEvent(event)
            self.app.processEvents()
            self.assertFalse(event.isAccepted())
            self.window.restore_from_tray()
            self.app.processEvents()
            with self.subTest(round=round_index):
                self.assertIs(self.bridge.timer, timer, "不得重建 timer")
                self.assertTrue(timer.isActive(), "恢复后轮询必须 active")
                self.assertIs(self.bridge.engine, engine, "不得重建引擎")
                self.assertFalse(self.window.isHidden())
        listeners_after = len(getattr(log_mod, "_listeners", []))
        self.assertEqual(listeners_before, listeners_after,
                         f"3 轮关闭恢复后 listener 泄漏：{listeners_before}→{listeners_after}")

    def test_quit_from_tray_hides_everything(self) -> None:
        self.window.switch_to_mini_window()
        self.app.processEvents()
        self.assertTrue(self.window.mini_player.isVisible())
        app_quit_called = {"n": 0}
        real_quit = QApplication.instance().quit
        QApplication.instance().quit = lambda: app_quit_called.__setitem__(
            "n", app_quit_called["n"] + 1)
        try:
            self.window.quit_from_tray()
        finally:
            QApplication.instance().quit = real_quit
        self.assertFalse(self.window.mini_player.isVisible(), "托盘退出应收掉迷你条")
        self.assertIs(self.window._quitting, True)
        self.assertEqual(app_quit_called["n"], 1)

    def test_settings_close_action_roundtrip(self) -> None:
        panel = self.window.settings_panel
        for value in ("quit", "tray"):
            with self.subTest(value=value):
                index = panel.close_action_combo.findData(value)
                self.assertGreaterEqual(index, 0, f"下拉缺 {value}")
                panel.close_action_combo.setCurrentIndex(index)
                self.app.processEvents()
                panel.apply_to(self.window.settings)
                self.assertEqual(self.window.settings.ui_close_action, value)


# ------------------------------------------------ ③ 迷你条


class TestMiniPlayer(BaseWindowTest):
    def test_button_before_prev_and_signal_wired(self) -> None:
        bar = self.window.player_bar
        controls = None
        for child in bar.findChildren(QHBoxLayout):
            if child.indexOf(bar.btn_prev) >= 0:
                controls = child
                break
        self.assertIsNotNone(controls, "没找到播放控制布局")
        self.assertLess(controls.indexOf(bar.btn_view_mode),
                        controls.indexOf(bar.btn_prev),
                        "视图按钮必须在「上一曲」左侧")

        fired = []
        bar.viewModeToggled.connect(lambda: fired.append(1))
        bar.btn_view_mode.click()
        self.app.processEvents()
        self.assertEqual(fired, [1], "viewModeToggled 未接通")
        self.assertTrue(self.window.mini_player.isVisible(), "点击后应切到迷你条")
        self.assertTrue(self.window.isHidden(), "主窗口与迷你条必须互斥可见")

    def test_geometry_and_structure(self) -> None:
        mini = self.window.mini_player
        self.assertEqual((mini.width(), mini.height()), (MINI_WIDTH, MINI_HEIGHT),
                         f"迷你条尺寸应为 {MINI_WIDTH}x{MINI_HEIGHT}")
        self.assertTrue(mini.windowFlags() & Qt.FramelessWindowHint, "迷你条应无边框")
        self.assertIsInstance(mini.bar, PlayerBar)
        self.assertIsNot(mini.bar, self.window.player_bar, "应是同一套控件的新实例")
        self.assertEqual(len(mini.lyric_labels), 3, "应有三行歌词")
        self.assertTrue(mini.bar._collapsed, "迷你条播放条应处于折叠态")

    def test_no_new_engine_on_switch(self) -> None:
        """切形态绝不新建引擎（对象身份 + 构造器计数双证据）。"""
        from seqaudio.player.engine import PlayerEngine

        mini = self.window.mini_player
        self.assertFalse(hasattr(mini, "engine"), "MiniPlayerWindow 不该持有引擎")
        engine = self.bridge.engine
        created = []
        real_init = PlayerEngine.__init__

        def counting_init(self, *args, **kwargs):
            created.append(1)
            return real_init(self, *args, **kwargs)

        PlayerEngine.__init__ = counting_init
        try:
            self.window.switch_to_mini_window()
            self.app.processEvents()
            self.window.switch_to_main_window()
            self.app.processEvents()
            self.window.switch_to_mini_window()
            self.app.processEvents()
        finally:
            PlayerEngine.__init__ = real_init
        self.assertEqual(created, [], f"切形态过程中新建了 {len(created)} 个引擎")
        self.assertIs(self.bridge.engine, engine)

    def test_lyrics_read_same_panel(self) -> None:
        mini = self.window.mini_player
        panel = self.window.player_lyrics
        panel._lines = [(0.0, "第一行"), (1.0, "第二行"), (2.0, "第三行")]
        panel._current = 1
        mini.update_lyrics()
        texts = [label.text() for label in mini.lyric_labels]
        self.assertEqual(texts, ["第一行", "第二行", "第三行"], f"歌词行不符：{texts}")
        self.assertTrue(mini.lyric_labels[1].font().bold(), "当前行应加粗")
        self.assertIn(theme.ACCENT, mini.lyric_labels[1].styleSheet())
        self.assertIn(theme.FG_DIM, mini.lyric_labels[0].styleSheet())

        panel._lines = []
        mini.update_lyrics()
        self.assertTrue(mini.lyric_labels[0].text(), "无歌词应显示提示")
        self.assertEqual([label.text() for label in mini.lyric_labels[1:]], ["", ""])

    def test_mini_boundary_operations(self) -> None:
        """迷你条下：曲末续播、切歌、进度、音量、模式都正常。"""
        def mark(label: str) -> None:
            print(f"    [step] {label} @ {time.strftime('%H:%M:%S')}", flush=True)

        mark("switch_to_mini")
        self.window.switch_to_mini_window()
        self.app.processEvents()
        mark("synth third (ffmpeg subprocess)")
        third = synth(self.tmp / "t2.flac", seconds=3.2)
        mark("probe 3 tracks")
        tracks = [probe(path, with_cue=False)
                  for path in (self.audio[0], self.audio[1], third)]
        mark("enqueue + play")
        self.bridge.enqueue(tracks, replace=True)
        self.assertTrue(self.bridge.play_index(0))
        pump(self.app, 0.4)
        mini_bar = self.window.mini_player.bar

        mark("mini next")
        mini_bar.nextRequested.emit()
        self.app.processEvents()
        self.assertEqual(self.bridge.queue_index(), 1, "迷你条下一首无效")

        mark("raise mini + pump 3.5s")
        self.window.mini_player.raise_()
        self.window.mini_player.activateWindow()
        pump(self.app, 3.5)  # 第二首 3.2 s，跨 EOF
        mark("pump done")
        snapshot = self.bridge.poll_now()
        self.assertEqual(snapshot["state"], PlayerState.PLAYING.value,
                         f"迷你条下曲末应继续播放，实际 {snapshot['state']}")
        self.assertEqual(snapshot["queue_index"], 2,
                         f"迷你条下曲末应自动进第 3 首，实际 {snapshot['queue_index']}")

        mini_bar.seekRequested.emit(1000)
        self.app.processEvents()
        self.assertGreaterEqual(self.bridge.poll_now()["position_ms"], 900)

        mini_bar.volumeChanged.emit(-6.0)
        self.app.processEvents()
        self.assertAlmostEqual(self.bridge.engine.volume_db, -6.0, delta=0.01)

        mini_bar.modeChanged.emit("repeat_all")
        self.app.processEvents()
        self.assertEqual(self.bridge.poll_now()["mode"], "repeat_all")

    def test_theme_and_i18n_follow(self) -> None:
        mini = self.window.mini_player
        original_theme = getattr(theme, "CURRENT", None)
        try:
            _REAL_APPLY_THEME(self.window, "深空", persist=False, quiet=True)
            self.app.processEvents()
            self.assertIn(theme.SURFACE_CARD, mini.styleSheet(),
                          "主题切换后迷你条配色未刷新")
        finally:
            if original_theme:
                _REAL_APPLY_THEME(self.window, original_theme, persist=False,
                                  quiet=True)

        original_locale = i18n.current()
        failures: list[str] = []
        try:
            for locale in i18n.locale_names():
                i18n.set_locale(locale)
                try:
                    self.window._apply_playback_i18n()
                except AttributeError as exc:
                    failures.append(f"{locale}: {exc}")
                self.app.processEvents()
                # 迷你条自己的刷新路径单独验（不受主窗口 bug 影响）
                mini.refresh_texts()
                with self.subTest(locale=locale):
                    self.assertEqual(mini.windowTitle(), i18n.tr("ui_mini_title"))
                    self.assertEqual(mini.bar.btn_view_mode.toolTip(),
                                     i18n.tr("ui_view_main"))
        finally:
            i18n.set_locale(original_locale)
        self.assertEqual(
            failures, [],
            "切换语言时 _apply_playback_i18n 抛异常（源码缺陷，见报告）："
            + "; ".join(failures[:2]),
        )

    def test_restore_from_tray_hides_mini(self) -> None:
        self.window.switch_to_mini_window()
        self.app.processEvents()
        self.assertTrue(self.window.mini_player.isVisible())
        self.window.restore_from_tray()
        self.app.processEvents()
        self.assertFalse(self.window.mini_player.isVisible(),
                         "托盘恢复主窗口时迷你条必须隐藏")
        self.assertTrue(self.window.isVisible())


def QSystemTrayIcon_Trigger():  # noqa: N802 - 便于上面显式调用的占位
    from PySide6.QtWidgets import QSystemTrayIcon

    return QSystemTrayIcon.Trigger


if __name__ == "__main__":
    unittest.main(verbosity=2)
