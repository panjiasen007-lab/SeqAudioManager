"""自适应布局与「已选操作条」移除的 **QA 独立验证**。

刻意不 import 工程师的 ``tests/test_ui_adaptive.py``：那份用例的表格断言是
``table.height() >= table.minimumHeight()``（几何恒等式，等同于没测可见行数），
也没有像素序列与还原前后的逐项比对。本文件全部用实测像素与 ``rowAt()`` 计数
说话。

运行：``QT_QPA_PLATFORM=offscreen PYTHONPATH=src python tests/test_ui_adaptive_independent.py -v``

覆盖：
* 1040×720 / 1420×880 / 1920×1040 三档：底部三栏高度、右侧分栏宽度、像素差值；
* 右侧分栏宽度随窗口宽度**单调递增**（三点序列，不是只比大小两档）；
* 三档尺寸下表格**不被父容器裁切**（`table.height() <= parent.height()` 且
  `visibleRegion` 不小于自身高度），并按高度分档守可见行数（720≥3 / 880≥6 / 1040≥10）；
  小窗的行数上限由纵向预算决定，不是"想要多少就多少"（见 `ROWS_AT_*` 注释）；
* 窗口变大→还原、最大化→还原：window / 底部 / 右侧 / 表格尺寸逐项回到基线；
* ``selection_bar`` / ``selection_label`` 属性与控件同时消失，且 ``_on_selection_changed``
  不再引用它们（选行不抛异常）；
* 「鉴定 / 清理 / 编辑标签」三个入口仍在，且各自**真的被触发一次**。

已知环境限制（不记为缺陷）：offscreen 平台主屏只有 800×800，
``showMaximized()`` 实际尺寸约 796×796，因此"最大化"只用于验证**还原一致性**，
大尺寸布局用 ``resize(1920, 1040)`` 直接构造。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFrame, QSplitter, QWidget  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.core.model import Track  # noqa: E402
from seqaudio.ui import theme  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402

MIN_SIZE = (1040, 720)
MID_SIZE = (1420, 880)
LARGE_SIZE = (1920, 1040)
TALL_SIZE = (1600, 2000)  # 只为验证 360 px 上限

BOTTOM_MIN = 120
BOTTOM_MAX = 360

# 可见行门槛分档：**小窗的物理上限由纵向预算决定，不是「想要多少就多少」**。
# 1040×720 实测预算：body 634 = 过滤行 74 + 区块头 70 + 表格区 218 + 播放条 56
# + 底部 158（底部三栏 minHint 140 卡着，压不到 120），表格只剩 140px
# → (140-35)/34 ≈ 3 行。所以小窗守「不裁切 + ≥3 行」，10 行的承诺挪到
# 高度 ≥1000 的窗口上守（实测 1920×1040 有 12 行）。
ROWS_AT_MIN = 3      # 1040×720
ROWS_AT_MID = 6      # 1420×880
ROWS_AT_LARGE = 10   # 1920×1040


def pump(ms: int = 120) -> None:
    """驱动 offscreen 事件循环，让 ``resizeEvent`` 与布局计算落定。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def visible_rows(table) -> tuple[int, int, int]:
    """数**用户真正看得见**的完整行，返回 ``(行数, 可见视口高, 行高)``。

    三重口径，缺一不可：

    1. 不能用 ``rowCount()``（模型行数，跟窗口多大无关）；
    2. 不能用 ``table.height()``（这是**自己声明**的高度，父容器可能比它小）；
    3. 必须用 ``visibleRegion()`` —— Qt 会给"被祖先裁掉之后"的真实可见矩形。

    本机实测：1040×720 下 ``table.height()==360`` 而 ``visibleRegion().height()==128``，
    父容器只有 206 px，表格比父容器还高、下半截被裁掉。只看 ``table.height()``
    会得出"能显示 10 行"的假绿。
    """
    region = table.visibleRegion().boundingRect()
    if region.height() <= 0:
        return 0, 0, table.verticalHeader().defaultSectionSize()
    header_h = table.horizontalHeader().height()
    visible_h = max(0, region.height() - header_h)
    row_h = max(1, table.verticalHeader().defaultSectionSize())
    # 用 rowAt 交叉校验：只数真的落在可见区间里的行
    hits = {row for row in (table.rowAt(y) for y in range(0, visible_h)) if row >= 0}
    return len(hits), visible_h, row_h


def fake_tracks(count: int = 40) -> list[Track]:
    """不落盘的空壳曲目：模型只需要 path 就能出一行。"""
    return [Track(path=Path(f"/qa/fake_{i:03d}.flac")) for i in range(count)]


def module_height_policy():
    """启动高度**策略本体**：模块级纯函数 ``initial_window_height(available_h, platform)``。

    只测这一个入口——它才是契约。``MainWindow._initial_window_height`` 只是
    兼容委托（见 ``test_legacy_static_entry_delegates``），不重复断言策略内容，
    避免两套 API 都被当成契约、以后改一处漏一处。
    """
    from seqaudio.ui import main_window as mw

    return getattr(mw, "initial_window_height", None)


class AdaptiveLayoutIndependentTest(unittest.TestCase):
    """一个进程只构造一次主窗口：反复构造会触发本机 apply_theme 卡顿。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._writable = mock.patch(
            "seqaudio.core.paths._writable", return_value=True
        )
        cls._writable.start()
        cls.window = MainWindow(
            Settings(
                offline_mode=True,
                online_enabled=False,
                playback_restore_session=False,
            )
        )
        # show() 之前的几何就是构造期的默认启动尺寸，先记下来
        cls.startup_size = (cls.window.width(), cls.window.height())
        # 三栏之右侧「信号路径」默认跟随设置隐藏；测量三栏高度前先打开。
        cls.window.player_signals.setVisible(True)
        cls.window.show()
        cls.app.processEvents()
        pump()

    @classmethod
    def tearDownClass(cls) -> None:
        # 不用 close()：设置面板 dirty 时会弹模态框，离屏下会挂住。
        try:
            cls.window.hide()
        finally:
            cls._writable.stop()

    def setUp(self) -> None:
        self.window.player_signals.setVisible(True)
        self._resize(MID_SIZE)

    def _resize(self, size: tuple[int, int]) -> None:
        self.window.resize(*size)
        pump()
        self.app.processEvents()
        pump(40)

    def _bottom_panes(self) -> list[QWidget]:
        splitter = self.window._bottom_area
        self.assertIsInstance(splitter, QSplitter)
        return [splitter.widget(i) for i in range(splitter.count())]

    def _snapshot(self) -> dict[str, int]:
        win = self.window
        return {
            "window_w": win.width(),
            "window_h": win.height(),
            "bottom_h": win._bottom_area.height(),
            "right_w": win.right_tabs.width(),
            "table_h": win.table.height(),
        }

    # -------------------------------------------------- 两档尺寸的高度/宽度

    def test_bottom_and_right_at_two_sizes(self) -> None:
        """1040×720 与 1920×1040：底部、右侧、三栏各自的实测像素与差值。"""
        self._resize(MIN_SIZE)
        self.assertEqual(self.window.height(), MIN_SIZE[1])
        small = {
            "bottom": self.window._bottom_area.height(),
            "right": self.window.right_tabs.width(),
            "panes": [p.height() for p in self._bottom_panes()],
        }

        self._resize(LARGE_SIZE)
        self.assertEqual(self.window.height(), LARGE_SIZE[1])
        large = {
            "bottom": self.window._bottom_area.height(),
            "right": self.window.right_tabs.width(),
            "panes": [p.height() for p in self._bottom_panes()],
        }

        # 小窗：期望值 720*0.22=158 与下限 120 取大；但三栏各自的 minimumSizeHint
        # （歌词面板 140）会把底部硬顶到 158 左右，所以只断言区间与"三栏等分"。
        self.assertGreaterEqual(
            small["bottom"], BOTTOM_MIN,
            f"1040×720 底部 {small['bottom']}px 低于下限 {BOTTOM_MIN}",
        )
        self.assertLessEqual(
            small["bottom"], 190,
            f"1040×720 底部 {small['bottom']}px 吃掉了表格的空间（上限按 190 守）",
        )
        self.assertEqual(len(small["panes"]), 3, "底部应恰好三栏")
        for i, h in enumerate(small["panes"]):
            self.assertEqual(h, small["bottom"], f"小窗第 {i} 栏高度 {h} != 底部 {small['bottom']}")

        # 大窗：1040*0.22=228 → 不为上限
        self.assertEqual(
            large["bottom"], 228, f"1920×1040 底部应为 228，实测 {large['bottom']}"
        )
        for i, h in enumerate(large["panes"]):
            self.assertEqual(h, large["bottom"], f"大窗第 {i} 栏高度 {h} != 底部 {large['bottom']}")

        self.assertGreater(large["bottom"], small["bottom"])
        self.assertGreaterEqual(
            large["bottom"] - small["bottom"], 30,
            f"底部从 720 到 1040 只长了 {large['bottom'] - small['bottom']}px",
        )
        self.assertGreater(large["right"], small["right"])
        self.assertGreaterEqual(
            large["right"] - small["right"], 150, f"右侧分栏只宽了 {large['right'] - small['right']}px"
        )

    def test_bottom_height_capped(self) -> None:
        """超高窗口：底部夹在 360 px 上限，不无限长。"""
        self._resize(TALL_SIZE)
        bottom = self.window._bottom_area.height()
        self.assertEqual(bottom, BOTTOM_MAX, f"2000 高应夹到 {BOTTOM_MAX}，实测 {bottom}")

    # -------------------------------------------------- 启动尺寸自适应

    def test_startup_size_on_offscreen_is_880(self) -> None:
        """offscreen 下启动尺寸保持 1420×880，不改变既有几何基线。"""
        width, height = self.startup_size
        self.assertEqual(
            (width, height), (1420, 880),
            f"offscreen 启动尺寸应为 1420×880，实测 {width}×{height}",
        )

    def test_initial_window_height_policy_matrix(self) -> None:
        """启动高度策略矩阵：1080p 争取 1000、768p 不超屏、低于最小高兜底 720。"""
        policy = module_height_policy()
        self.assertIsNotNone(
            policy,
            "找不到模块级策略函数 initial_window_height(available_h, platform)，"
            "API 可能被重命名",
        )
        cases = [
            # (屏幕可用高, 平台) -> 期望启动高
            (1040, "offscreen"),   # 自动化环境固定 880，不受屏幕影响
            (1040, "windows"),     # 1080p 可用高约 1040 → min(1000, 1000)
            (900, "windows"),      # 恰好 900 → min(1000, 860)
            (728, "windows"),      # 768p 笔记本：整窗不超屏
            (600, "windows"),      # 低于最小窗高 → 兜底 720
            (1400, "windows"),     # 大屏封顶 1000
        ]
        expected_of = {
            ("offscreen", 1040): 880,
            ("windows", 1040): 1000,
            ("windows", 900): 860,
            ("windows", 728): 728,
            ("windows", 600): 720,
            ("windows", 1400): 1000,
        }
        for available_h, platform_name in cases:
            expected = expected_of[(platform_name, available_h)]
            with self.subTest(platform=platform_name, available_h=available_h):
                actual = policy(available_h, platform_name)
                self.assertEqual(
                    actual, expected,
                    f"{platform_name} / 可用高 {available_h} 应为 {expected}，实测 {actual}",
                )

    def test_legacy_static_entry_delegates(self) -> None:
        """兼容入口 ``MainWindow._initial_window_height(platform, avail)`` 必须与本体一致。"""
        policy = module_height_policy()
        legacy = getattr(MainWindow, "_initial_window_height", None)
        self.assertIsNotNone(policy, "缺少策略本体，无法比对兼容入口")
        if legacy is None:
            self.skipTest("没有兼容入口 _initial_window_height（已清理则无需比对）")
        for available_h in (728, 900, 1040, 1400):
            with self.subTest(available_h=available_h):
                self.assertEqual(
                    int(legacy("windows", available_h)),
                    int(policy(available_h, "windows")),
                    f"兼容入口与本体在可用高 {available_h} 上不一致",
                )

    def test_initial_height_never_exceeds_available_screen(self) -> None:
        """小屏上启动高度不得超过屏幕可用高度（否则标题栏/任务栏被切掉）。"""
        policy = module_height_policy()
        self.assertIsNotNone(
            policy,
            "找不到模块级策略函数 initial_window_height(available_h, platform)",
        )
        for available_h in (728, 768, 800, 864, 900, 1040, 1440):
            with self.subTest(available_h=available_h):
                actual = policy(available_h, "windows")
                self.assertLessEqual(
                    actual, max(available_h, 720),
                    f"可用高 {available_h} 时启动高 {actual} 超出屏幕",
                )
                self.assertGreaterEqual(actual, 720, "启动高不得低于窗口最小高度")

    def test_right_panel_monotonic_growth(self) -> None:
        """三个宽度档的右侧分栏像素序列必须严格递增。"""
        series = []
        for size in (MIN_SIZE, MID_SIZE, LARGE_SIZE):
            self._resize(size)
            series.append(self.window.right_tabs.width())

        self.assertEqual(len(series), 3, f"未取到三档宽度：{series}")
        self.assertLess(
            series[0], series[1], f"1040→1420 右侧未变宽：{series[0]}→{series[1]}"
        )
        self.assertLess(
            series[1], series[2], f"1420→1920 右侧未变宽：{series[1]}→{series[2]}"
        )
        self.assertGreater(
            series[2] - series[0], 200, f"三档总增量仅 {series[2] - series[0]}px"
        )
        # 分栏宽度不得超出窗口可用宽度
        self.assertLess(series[2], LARGE_SIZE[0])

    # -------------------------------------------------- 表格可见行数 / 不裁切

    def _assert_table_not_clipped(self, where: str) -> tuple[int, int, int]:
        """裁切不变量：表格不得高于父容器，``visibleRegion`` 不得小于自身高度。"""
        table = self.window.table
        parent_h = table.parent().height()
        region = table.visibleRegion().boundingRect()
        self.assertLessEqual(
            table.height(),
            parent_h,
            f"{where}：表格自报高 {table.height()}px 超过父容器 {parent_h}px，"
            f"超出部分会被 Qt 裁掉（QA-UI-02 的成因）",
        )
        self.assertGreaterEqual(
            region.height(),
            table.height() - 2,
            f"{where}：表格 {table.height()}px 但 visibleRegion 只有 "
            f"{region.height()}px，说明被祖先裁切了 {table.height() - region.height()}px",
        )
        # 纵向滚动条必须完整落在可见区内，否则用户滚不到底部
        bar = table.verticalScrollBar()
        self.assertLessEqual(
            bar.height(),
            table.height(),
            f"{where}：纵向滚动条 {bar.height()}px 高于表格 {table.height()}px",
        )
        return visible_rows(table)

    def test_min_window_no_clipping_and_min_rows(self) -> None:
        """1040×720：首要保证不裁切，其次尽量多显示行（物理上限约 3~4 行）。"""
        self.window.model.add_tracks(fake_tracks(40))
        self.app.processEvents()
        pump(60)

        self._resize(MIN_SIZE)
        table = self.window.table
        rows, visible_h, row_h = self._assert_table_not_clipped("1040×720")
        self.assertGreaterEqual(
            rows,
            ROWS_AT_MIN,
            f"最小窗下只看得见 {rows} 行（表格 {table.height()}px，"
            f"父容器 {table.parent().height()}px，可见视口 {visible_h}px，行高 {row_h}px）",
        )

    def test_mid_window_no_clipping_and_min_rows(self) -> None:
        """1420×880：不裁切 + 可见行数达到该尺寸的物理上限。"""
        self.window.model.add_tracks(fake_tracks(40))
        self.app.processEvents()
        pump(60)

        self._resize(MID_SIZE)
        table = self.window.table
        rows, visible_h, row_h = self._assert_table_not_clipped("1420×880")
        self.assertGreaterEqual(
            rows,
            ROWS_AT_MID,
            f"1420×880 只看得见 {rows} 行（表格 {table.height()}px，"
            f"可见视口 {visible_h}px，行高 {row_h}px）",
        )

    def test_large_window_shows_ten_visible_rows(self) -> None:
        """1920×1040：高度足够，必须守满「至少 10 行」的产品承诺。"""
        self.window.model.add_tracks(fake_tracks(40))
        self.app.processEvents()
        pump(60)

        self._resize(LARGE_SIZE)
        table = self.window.table
        rows, visible_h, row_h = self._assert_table_not_clipped("1920×1040")
        self.assertGreaterEqual(
            rows,
            ROWS_AT_LARGE,
            f"大窗下只看得见 {rows} 行（表格 {table.height()}px，"
            f"可见视口 {visible_h}px，行高 {row_h}px）",
        )

    # -------------------------------------------------- 还原一致性

    def test_resize_back_is_exact(self) -> None:
        """1920×1040 → 1040×720：四个尺寸逐项回到基线，不残留大尺寸。"""
        self._resize(MIN_SIZE)
        baseline = self._snapshot()

        self._resize(LARGE_SIZE)
        grown = self._snapshot()
        self.assertGreater(grown["bottom_h"], baseline["bottom_h"])
        self.assertGreater(grown["right_w"], baseline["right_w"])

        self._resize(MIN_SIZE)
        restored = self._snapshot()
        self.assertEqual(restored, baseline, f"还原后残留大尺寸：{restored} vs {baseline}")

    def test_maximize_then_normal_restores_baseline(self) -> None:
        """最大化→还原：像素回到基线（不能只看 ``isMaximized()`` 标志）。"""
        self._resize(MIN_SIZE)
        baseline = self._snapshot()

        self.window.showMaximized()
        self.app.processEvents()
        pump()
        self.assertTrue(self.window.isMaximized(), "showMaximized() 未生效")
        maximized = self._snapshot()
        self.assertGreaterEqual(
            maximized["bottom_h"], BOTTOM_MIN, "最大化时底部不可低于下限"
        )

        self.window.showNormal()
        self.app.processEvents()
        pump()
        self.assertFalse(self.window.isMaximized())
        restored = self._snapshot()
        self.assertEqual(
            restored, baseline, f"最大化还原后未回到基线：{restored} vs {baseline}"
        )

    # -------------------------------------------------- 操作条移除

    def test_selection_bar_removed_everywhere(self) -> None:
        """属性、控件、QSS 三处都不能有残留，且选行不再触碰它。"""
        win = self.window
        self.assertFalse(hasattr(win, "selection_bar"), "selection_bar 属性仍存在")
        self.assertFalse(hasattr(win, "selection_label"), "selection_label 属性仍存在")

        self.assertIsNone(
            win.findChild(QFrame, "selectionBar"), "仍能找到 objectName=selectionBar 的控件"
        )
        orphans = [
            child
            for child in win.findChildren(QWidget)
            if child.objectName() in {"selectionBar", "selectionLabel"}
        ]
        self.assertEqual(orphans, [], f"存在孤儿控件：{orphans}")

        self.assertNotIn("selectionBar", theme.QSS, "QSS 仍有 selectionBar 死样式")

        # 关键回归点：删掉的赋值原本就在 _on_selection_changed 里，选行不能抛
        self.window.model.add_tracks(fake_tracks(3))
        self.app.processEvents()
        self.window.table.selectRow(0)
        self.app.processEvents()
        try:
            self.window._on_selection_changed()
        except AttributeError as exc:  # noqa: PERF203
            self.fail(f"_on_selection_changed 仍引用已删除控件：{exc}")


class SelectionEntriesStillWiredTest(unittest.TestCase):
    """入口验证：改的是"操作条"，不能顺手把功能一起删掉。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])
        cls._writable = mock.patch(
            "seqaudio.core.paths._writable", return_value=True
        )
        cls._writable.start()
        # 必须在构造前 patch：按钮的 clicked 连的是构造期取的 bound method，
        # 构造后再替换实例属性不会改变已连接的槽。
        cls.mocks = {
            name: mock.patch.object(MainWindow, name, autospec=True)
            for name in ("run_authenticity", "run_cleanup", "edit_selected")
        }
        for patcher in cls.mocks.values():
            patcher.start()
        cls.window = MainWindow(
            Settings(
                offline_mode=True,
                online_enabled=False,
                playback_restore_session=False,
            )
        )
        cls.window.show()
        cls.app.processEvents()
        pump()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls.window.hide()
        finally:
            for patcher in cls.mocks.values():
                patcher.stop()
            cls._writable.stop()

    def test_auth_and_cleanup_buttons_still_trigger(self) -> None:
        """「鉴定」「清理」按钮仍在工具栏且点击后各自触发一次。"""
        window = self.window
        self.assertTrue(window.btn_auth.isVisible(), "鉴定按钮不可见")
        self.assertTrue(window.btn_cleanup.isVisible(), "清理按钮不可见")

        window.btn_auth.click()
        self.app.processEvents()
        window.btn_cleanup.click()
        self.app.processEvents()

        self.assertEqual(
            MainWindow.run_authenticity.call_count, 1,
            f"鉴定入口未触发：{MainWindow.run_authenticity.call_count}",
        )
        self.assertEqual(
            MainWindow.run_cleanup.call_count, 1,
            f"清理入口未触发：{MainWindow.run_cleanup.call_count}",
        )

    def test_edit_tags_entry_still_trigger(self) -> None:
        """「编辑标签」入口（inspector 请求）仍然连到槽上。"""
        window = self.window
        before = MainWindow.edit_selected.call_count

        window.inspector.editRequested.emit()
        self.app.processEvents()

        self.assertEqual(
            MainWindow.edit_selected.call_count, before + 1,
            f"编辑标签入口未触发：{before} → {MainWindow.edit_selected.call_count}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
