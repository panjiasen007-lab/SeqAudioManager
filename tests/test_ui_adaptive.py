"""界面自适应回归：移除「已选操作条」+ 最大化/还原时底部与左右分栏伸缩。

运行：``python -m unittest tests.test_ui_adaptive -q``（离屏，无音频设备依赖）。

覆盖
----
* 1040×720 最小窗：底部 ≥ 170 px，表格 ≥ 表头 + 10 行（minimumHeight 语义）；
* 1920×1040：底部 > 190 且 ≤ 360，右侧分栏比小窗明显更宽；
* 「已选 N 首」操作条彻底移除（控件与 QSS 均无残留），鉴定/清理入口仍在；
* 从最大化还原回 1040×720 后布局回到小窗数值，不残留大尺寸。
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

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.ui import theme  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402

MIN_SIZE = (1040, 720)
LARGE_SIZE = (1920, 1040)


def pump(ms: int = 40) -> None:
    """跑一小段事件循环，让 resizeEvent 与布局计算都落定。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class AdaptiveLayoutTest(unittest.TestCase):
    """共用一个窗口：离屏下反复构造 MainWindow 会反复 setStyleSheet 卡顿。"""

    @classmethod
    def setUpClass(cls) -> None:
        instance = QApplication.instance()
        cls.app = instance if instance is not None else QApplication([])
        cls.window = MainWindow(
            Settings(
                offline_mode=True,
                online_enabled=False,
                playback_restore_session=False,
            )
        )
        cls.window.show()
        cls.app.processEvents()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.settings.ui_close_action = "quit"
        with patch.object(cls.window.settings_panel, "is_dirty", return_value=False):
            from PySide6.QtGui import QCloseEvent

            cls.window.closeEvent(QCloseEvent())
        cls.window.hide()

    def setUp(self) -> None:
        self.window.resize(1420, 880)
        pump()
        self.assertEqual(self.window.height(), 880)

    def _resize(self, size: tuple[int, int]) -> None:
        self.window.resize(*size)
        pump()
        self.app.processEvents()

    # ------------------------------------------------------------ 尺寸行为

    def test_minimum_size_keeps_bottom_and_table(self) -> None:
        """1040×720：底部不低于 170，表格仍有表头 + 10 行。"""
        self._resize(MIN_SIZE)
        self.assertEqual(self.window.height(), MIN_SIZE[1])
        self.assertGreaterEqual(
            self.window._bottom_area.height(), 170, "底部被压到 170 以下"
        )
        self.assertGreaterEqual(
            self.window.table.height(),
            self.window.table.minimumHeight(),
            "最小窗下表格不足 10 行",
        )

    def test_larger_window_gives_bottom_more_height(self) -> None:
        """1920×1040：底部跟着长（>190 且 ≤360），右侧分栏明显变宽。"""
        self._resize(MIN_SIZE)
        small_bottom = self.window._bottom_area.height()
        small_right = self.window.right_tabs.width()

        self._resize(LARGE_SIZE)
        self.assertEqual(self.window.height(), LARGE_SIZE[1])
        big_bottom = self.window._bottom_area.height()
        big_right = self.window.right_tabs.width()

        self.assertGreater(big_bottom, 190, "最大化后底部没有跟着长")
        self.assertLessEqual(big_bottom, 360, "底部超过 360 会挤占曲库区")
        self.assertGreater(big_bottom, small_bottom)
        self.assertGreater(big_right, small_right, "右侧分栏未随窗口变宽")
        self.assertGreaterEqual(
            self.window.table.height(), self.window.table.minimumHeight()
        )

    def test_right_panel_max_width_relaxed(self) -> None:
        """参数面板不再被 560px 硬顶住。"""
        panel = self.window.settings_panel
        self.assertGreaterEqual(panel.minimumWidth(), 380)
        self.assertEqual(panel.maximumWidth(), 900)

    def test_restore_minimum_after_large(self) -> None:
        """最大化后还原：底部回到 170，表格下限仍成立。"""
        self._resize(LARGE_SIZE)
        self.assertGreater(self.window._bottom_area.height(), 190)
        self._resize(MIN_SIZE)
        self.assertEqual(self.window._bottom_area.height(), 170)
        self.assertGreaterEqual(
            self.window.table.height(),
            self.window.table.minimumHeight(),
            "还原后表格不足 10 行",
        )

    # ------------------------------------------------------------ 操作条移除

    def test_selection_bar_removed(self) -> None:
        """控件、属性与 QSS 三处都不能残留。"""
        self.assertFalse(hasattr(self.window, "selection_bar"), "selection_bar 仍存在")
        self.assertFalse(
            hasattr(self.window, "selection_label"), "selection_label 仍存在"
        )
        self.assertIsNone(
            self.window.findChild(QFrame, "selectionBar"), "仍能找到 selectionBar 控件"
        )
        self.assertNotIn("selectionBar", theme.QSS, "QSS 仍含死样式 selectionBar")

    def test_selection_actions_still_reachable(self) -> None:
        """删条不影响入口：鉴定 / 清理按钮与右键菜单动作仍在。"""
        self.assertIsNotNone(self.window.btn_auth)
        self.assertIsNotNone(self.window.btn_cleanup)
        self.assertTrue(self.window.btn_auth.text())
        self.assertTrue(self.window.btn_cleanup.text())

        # 右键菜单里仍能找到「编辑标签」「清理」等入口（构建一次菜单不 exec）
        if hasattr(self.window, "_build_context_menu"):
            menu = self.window._build_context_menu()
            texts = [action.text() for action in menu.actions()]
            self.assertTrue(any("编辑标签" in text for text in texts))
        elif hasattr(self.window, "edit_selected"):
            self.assertTrue(callable(self.window.edit_selected))


if __name__ == "__main__":
    unittest.main(verbosity=2)
