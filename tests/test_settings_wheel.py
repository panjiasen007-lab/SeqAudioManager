"""设置面板滚轮路由（v1.8.1 用户反馈）。

用户诉求：在参数页滚滚轮，「滚着滚着鼠标移动到里面的参数位置时，里面的参数
也跟着滚动了」——Qt 默认让 QComboBox/QSpinBox/QDoubleSpinBox 悬停滚轮直接
**改值**。修复后滚轮一律转给**外层**滚动区，只滚外面那条滚动条。

断言：
1. 滚轮悬停在 QComboBox 上**不改值**（默认 Qt 会切到上/下一项）；
2. 滚轮悬停在 QDoubleSpinBox 上**不改值**；
3. 滚轮事件被转发给所属 QScrollArea 的垂直滚动条（滚动条真的动了、能滚回来）；
4. 全部下拉都被过滤器覆盖（不允许有个别漏网）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QScrollArea  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.ui.settings_panel import SettingsPanel  # noqa: E402


def _app():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _wheel(widget, dy: int) -> None:
    """向控件发一个滚轮事件（走 eventFilter 的正常路径）。"""
    event = QWheelEvent(
        QPointF(8.0, 8.0),
        QPointF(8.0, 8.0),
        QPoint(0, 0),
        QPoint(0, dy),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    _app().sendEvent(widget, event)


class TestWheelGoesToScrollbarOnly(unittest.TestCase):
    """滚轮只滚外层滚动条：参数控件不许被"路过"的滚轮改值。"""

    def setUp(self) -> None:
        _app()
        self.settings = Settings(offline_mode=True)
        self.panel = SettingsPanel(self.settings)
        self.panel.resize(900, 700)
        self.addCleanup(self.panel.deleteLater)
        self.assertIsNotNone(
            getattr(self.panel, "_wheel_guard", None),
            "滚轮过滤器没有安装",
        )
        area = self._area_of(self.panel.backend_combo)
        self.assertIsNotNone(area, "backend_combo 应该在某个 QScrollArea 里")
        self.area = area
        self.area.verticalScrollBar().setValue(0)

    @staticmethod
    def _area_of(widget):
        parent = widget.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parentWidget()
        return None

    def test_wheel_does_not_change_combobox(self) -> None:
        combo = self.panel.backend_combo
        before = combo.currentIndex()
        _wheel(combo, +120)                       # 默认 Qt 行为：切到下一项
        self.assertEqual(
            combo.currentIndex(), before,
            "滚轮悬停在下拉上不许改选中的后端",
        )

    def test_wheel_does_not_change_spinbox(self) -> None:
        spin = self.panel.threshold_spin
        before = spin.value()
        _wheel(spin, +120)                        # 默认 Qt 行为：加一步
        self.assertEqual(spin.value(), before, "滚轮悬停在数值框上不许改值")

    def test_wheel_moves_the_outer_scrollbar(self) -> None:
        bar = self.area.verticalScrollBar()
        self.assertGreater(bar.maximum(), 0, "页面必须可滚动，否则用例没有意义")
        _wheel(self.panel.backend_combo, -120)    # 向下滚
        self.assertGreater(
            bar.value(), 0,
            "滚轮必须转给外层滚动条（向下滚 → 滚动条前进）",
        )
        value_after_down = bar.value()
        _wheel(self.panel.backend_combo, +120)    # 向上滚
        self.assertLess(bar.value(), value_after_down, "向上滚要能滚回来")

    def test_every_combobox_is_covered(self) -> None:
        """全部下拉都被覆盖，不许有漏网的（例如后加的卡片忘装过滤器）。"""
        combos = self.panel.findChildren(QComboBox)
        self.assertTrue(combos, "面板里必须有下拉")
        for combo in combos:
            before = combo.currentIndex()
            _wheel(combo, +120)
            self.assertEqual(
                combo.currentIndex(), before,
                f"{combo.objectName() or combo} 被滚轮改了值",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
