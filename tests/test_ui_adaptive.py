"""界面自适应回归：移除「已选操作条」+ 最大化/还原时底部与左右分栏伸缩。

运行：``python -m unittest tests.test_ui_adaptive -q``（离屏，无音频设备依赖）。

覆盖
----
* 1040×720 最小窗：底部 ≥ 110（实际被底部三栏 minHint 抬到 ~158），
  表格不溢出父容器、visibleRegion == 自身高度、可见行 ≥ 3；
* 1420×880：不裁切 + 可见行 ≥ 6；
* 1920×1040：不裁切 + 可见行 ≥ 10，底部 > 190 且 ≤ 360，右侧分栏更宽；
* 启动高度按屏幕自适应（offscreen 固定 880，1080p 约 1000，768p 不超屏）；
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
from seqaudio.core.model import Track  # noqa: E402
from seqaudio.ui import theme  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402

MIN_SIZE = (1040, 720)
LARGE_SIZE = (1920, 1040)
#: 小窗时底部下限（底部三栏自身 minHint 会把实际值抬到 ~158）
BOTTOM_MIN = 110
#: 底部上限，超过就挤占曲库区
BOTTOM_MAX = 360


def visible_rows(table) -> tuple[int, int, int]:
    """只数用户真正看得见的完整行：``(行数, 可见视口高, 行高)``。

    必须用 ``visibleRegion()``（被祖先裁切之后的真实可见矩形），
    ``table.height() >= minimumHeight()`` 是几何恒等式，会假绿。
    """
    region = table.visibleRegion().boundingRect()
    if region.height() <= 0:
        return 0, 0, table.verticalHeader().defaultSectionSize()
    header_h = table.horizontalHeader().height()
    visible_h = max(0, region.height() - header_h)
    row_h = max(1, table.verticalHeader().defaultSectionSize())
    hits = {row for row in (table.rowAt(y) for y in range(0, visible_h)) if row >= 0}
    return len(hits), visible_h, row_h



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
        # 行列测量需要真实行：空模型 rowAt() 永远 -1
        cls.window.model.add_tracks(
            [Track(path=Path(f"C:/qa/adapt_{i:03d}.flac")) for i in range(40)]
        )

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

    # ------------------------------------------------------------ 初始高度

    def test_initial_height_adapts_to_screen(self) -> None:
        """启动高度：1080p 争取 10 行，768p 不超出屏幕，offscreen 保持 880。"""
        calc = MainWindow._initial_window_height
        self.assertEqual(calc("offscreen", 1040), 880, "离屏必须保持 880 基线")
        # 1080p：可用 1040 → min(1000, 1000) = 1000
        self.assertEqual(calc("windows", 1040), 1000)
        # 无任务栏的 1080p：可用 1080 → 仍夹到 1000
        self.assertEqual(calc("windows", 1080), 1000)
        # 768p 笔记本：可用 728 → 不超出屏幕
        self.assertEqual(calc("windows", 728), 728)
        # 900p：可用 860 → 沿用小屏分支
        self.assertEqual(calc("windows", 860), 860)
        # 极端小屏：兜底不低于最小高度 720
        self.assertEqual(calc("windows", 600), 720)

    # ------------------------------------------------------------ 尺寸行为

    def _assert_not_clipped(self) -> tuple[int, int, int]:
        """断言表格没有溢出父容器、可见视口 == 自身视口，返回行统计。"""
        table = self.window.table
        self.assertLessEqual(
            table.height(),
            table.parent().height(),
            f"表格自报高 {table.height()}px 超过父容器 {table.parent().height()}px："
            "仍会被裁切",
        )
        region_h = table.visibleRegion().boundingRect().height()
        self.assertEqual(
            region_h,
            table.height(),
            f"表格被裁：可见 {region_h}px，自身高 {table.height()}px",
        )
        rows, visible_h, _row_h = visible_rows(table)
        self.assertGreater(visible_h, 0, "表格可见视口为 0")
        self.assertGreater(rows, 0, "一行都看不到")
        # 纵向滚动条必须完整落在表格内，否则用户滚不到底部
        self.assertLessEqual(
            table.verticalScrollBar().height(),
            table.height(),
            "纵向滚动条超出表格高度（滚动条被裁）",
        )
        return rows, visible_h, table.height()

    def test_minimum_size_keeps_bottom_and_table(self) -> None:
        """1040×720：底部 ≥ 110，表格不再溢出父容器，可读行数有实质提升。"""
        self._resize(MIN_SIZE)
        self.assertEqual(self.window.height(), MIN_SIZE[1])
        self.assertGreaterEqual(
            self.window._bottom_area.height(), BOTTOM_MIN, "底部被压到 110 以下"
        )
        rows, _visible_h, _table_h = self._assert_not_clipped()
        # 720 高时固定 chrome（工具栏/统计卡/播放条/状态栏/筛选行）已占用大半，
        # 物理预算只够 3~4 行；修复前裁切后同样只有 3 行，现在填满可用高度。
        # 10 行承诺挪到高度 ≥1000 的窗口上守（见 large 用例）。
        self.assertGreaterEqual(rows, 3, f"最小窗下只看得见 {rows} 行")

    def test_mid_window_rows_recover(self) -> None:
        """1420×880：不裁切，可见行数明显多于最小窗。"""
        self._resize(MIN_SIZE)
        min_rows, _vh, _th = visible_rows(self.window.table)

        self._resize((1420, 880))
        self.assertEqual(self.window.height(), 880)
        rows, _visible_h, _table_h = self._assert_not_clipped()
        self.assertGreater(rows, min_rows, "加高窗口后可见行数没有增加")
        self.assertGreaterEqual(rows, 6, f"880 高只看得见 {rows} 行")

    def test_large_window_gives_bottom_more_height(self) -> None:
        """1920×1040：底部跟着长（>190 且 ≤360），右侧分栏明显变宽，行数 ≥10。"""
        self._resize(MIN_SIZE)
        small_bottom = self.window._bottom_area.height()
        small_right = self.window.right_tabs.width()

        self._resize(LARGE_SIZE)
        self.assertEqual(self.window.height(), LARGE_SIZE[1])
        big_bottom = self.window._bottom_area.height()
        big_right = self.window.right_tabs.width()

        self.assertGreater(big_bottom, 190, "最大化后底部没有跟着长")
        self.assertLessEqual(big_bottom, BOTTOM_MAX, "底部超过 360 会挤占曲库区")
        self.assertGreater(big_bottom, small_bottom)
        self.assertGreater(big_right, small_right, "右侧分栏未随窗口变宽")
        rows, _visible_h, _table_h = self._assert_not_clipped()
        self.assertGreaterEqual(rows, 10, f"1920×1040 只看得见 {rows} 行")

    def test_right_panel_max_width_relaxed(self) -> None:
        """参数面板不再被 560px 硬顶住。"""
        panel = self.window.settings_panel
        self.assertGreaterEqual(panel.minimumWidth(), 380)
        self.assertEqual(panel.maximumWidth(), 900)

    def test_restore_minimum_after_large(self) -> None:
        """最大化后还原：底部回到公式值，表格仍不裁切。"""
        self._resize(LARGE_SIZE)
        self.assertGreater(self.window._bottom_area.height(), 190)
        self._resize(MIN_SIZE)
        expected = max(BOTTOM_MIN, min(BOTTOM_MAX, int(MIN_SIZE[1] * 0.22)))
        self.assertEqual(self.window._bottom_area.height(), expected)
        self.assertEqual(expected, 158)
        self._assert_not_clipped()

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
