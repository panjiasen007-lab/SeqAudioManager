"""界面不透明度（品牌条滑杆 + 主窗口整窗透明度）回归。

运行：``python -m unittest tests.test_ui_brand_opacity -q``（离屏，不需要显示器）。

为什么用「假窗口」而不是真 MainWindow
------------------------------------
``MainWindow.__init__`` 会起 ``PlayerBridge`` 的后台线程并读真实 config.json，
用来验「``_apply_ui_opacity(80, persist=True)`` 是否只做两件事」代价过大，
还会污染用户配置（``Settings.save()`` 默认写真实路径）。这里把方法当普通函数
调用，配一个只记录 ``setWindowOpacity`` 的轻量对象 —— 断言的是**行为契约**，
不是 Qt 的窗口系统。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402
from seqaudio.ui.widgets import (  # noqa: E402
    UI_OPACITY_DEFAULT,
    UI_OPACITY_MAX,
    UI_OPACITY_MIN,
    BrandBar,
)


def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


class _FakeWindow:
    """只实现 ``_apply_ui_opacity`` 用到的三个成员。"""

    def __init__(self) -> None:
        self.settings = Settings()
        self.opacity: float | None = None
        self.saves = 0
        self.settings.save = self._save  # 覆盖真实落盘，避免写用户配置

    def _save(self, path=None) -> None:
        self.saves += 1

    def setWindowOpacity(self, value: float) -> None:
        self.opacity = value


class BrandBarOpacityTest(unittest.TestCase):
    """品牌条右侧的「界面不透明度」控件。"""

    def setUp(self) -> None:
        app()
        self.bar = BrandBar("0.0.0")

    def tearDown(self) -> None:
        self.bar.deleteLater()

    def test_slider_range_and_default(self) -> None:
        slider = self.bar.opacity_slider
        self.assertEqual(slider.minimum(), UI_OPACITY_MIN)
        self.assertEqual(slider.maximum(), UI_OPACITY_MAX)
        self.assertEqual((UI_OPACITY_MIN, UI_OPACITY_MAX), (10, 100))
        self.assertEqual(slider.value(), UI_OPACITY_DEFAULT)
        self.assertEqual(UI_OPACITY_DEFAULT, 95)
        self.assertEqual(slider.objectName(), "uiOpacity")
        self.assertEqual(self.bar.opacity_value.text(), "95%")

    def test_value_changed_emits(self) -> None:
        received: list[int] = []
        self.bar.opacityChanged.connect(received.append)
        self.bar.opacity_slider.setValue(72)
        self.assertEqual(received[-1], 72)
        self.assertEqual(self.bar.opacity_value.text(), "72%")

    def test_released_emits_for_persist(self) -> None:
        received: list[int] = []
        self.bar.opacityReleased.connect(received.append)
        self.bar.opacity_slider.setValue(60)
        self.bar.opacity_slider.sliderReleased.emit()
        self.assertEqual(received[-1], 60)

    def test_set_opacity_clamps_and_is_silent(self) -> None:
        received: list[int] = []
        self.bar.opacityChanged.connect(received.append)
        self.bar.set_opacity(5)
        self.assertEqual(self.bar.opacity_slider.value(), UI_OPACITY_MIN)
        self.assertEqual(self.bar.opacity_value.text(), "10%")
        self.bar.set_opacity(1000)
        self.assertEqual(self.bar.opacity_slider.value(), UI_OPACITY_MAX)
        self.bar.set_opacity(88)
        self.assertEqual(self.bar.opacity(), 88)
        self.assertEqual(self.bar.opacity_value.text(), "88%")
        self.assertEqual(received, [], "set_opacity 不得回环发信号")

    def test_set_opacity_treats_zero_as_value_not_default(self) -> None:
        """回归（QA 缺陷 1）：`0` 是合法输入，不能被 `0 or 95` 当成缺省。"""
        self.bar.set_opacity(0)
        self.assertEqual(self.bar.opacity(), UI_OPACITY_MIN)
        self.assertEqual(self.bar.opacity_value.text(), "10%")
        self.bar.set_opacity(None)  # 只有 None 才回落默认值
        self.assertEqual(self.bar.opacity(), UI_OPACITY_DEFAULT)
        self.bar.set_opacity("不是数字")
        self.assertEqual(self.bar.opacity(), UI_OPACITY_DEFAULT)

    def test_tooltip_mentions_range(self) -> None:
        tip = self.bar.opacity_slider.toolTip()
        self.assertIn("10", tip)
        self.assertIn("100", tip)
        self.assertIn("95", tip)


class ApplyUiOpacityTest(unittest.TestCase):
    """主窗口的 ``_apply_ui_opacity``：夹紧 / setWindowOpacity / 按需落盘。"""

    def setUp(self) -> None:
        app()
        self.window = _FakeWindow()

    def test_applies_percentage_without_persisting(self) -> None:
        MainWindow._apply_ui_opacity(self.window, 80)
        self.assertAlmostEqual(self.window.opacity, 0.80)
        self.assertEqual(self.window.saves, 0, "拖动过程中不该写盘")
        self.assertEqual(self.window.settings.ui_opacity, 95, "未 persist 时不该改设置")

    def test_persists_on_release(self) -> None:
        MainWindow._apply_ui_opacity(self.window, 64, persist=True)
        self.assertAlmostEqual(self.window.opacity, 0.64)
        self.assertEqual(self.window.settings.ui_opacity, 64)
        self.assertEqual(self.window.saves, 1)

    def test_clamps_out_of_range(self) -> None:
        MainWindow._apply_ui_opacity(self.window, 0, persist=True)
        self.assertEqual(self.window.settings.ui_opacity, UI_OPACITY_MIN)
        MainWindow._apply_ui_opacity(self.window, 250, persist=True)
        self.assertEqual(self.window.settings.ui_opacity, UI_OPACITY_MAX)
        self.assertAlmostEqual(self.window.opacity, 1.0)

    def test_stored_opacity_treats_zero_as_value(self) -> None:
        """回归（QA 缺陷 1）：设置里存 0 也应夹紧到 10，而不是悄悄变回 95。"""
        self.window.settings.ui_opacity = 0
        self.assertEqual(MainWindow._stored_ui_opacity(self.window), 0)
        self.window.settings.ui_opacity = "坏值"
        self.assertEqual(
            MainWindow._stored_ui_opacity(self.window), UI_OPACITY_DEFAULT
        )

    def test_garbage_value_falls_back_to_default(self) -> None:
        MainWindow._apply_ui_opacity(self.window, "不是数字", persist=True)
        self.assertEqual(self.window.settings.ui_opacity, UI_OPACITY_DEFAULT)

    def test_signal_handlers_exist(self) -> None:
        for name in ("_on_ui_opacity", "_persist_ui_opacity", "_apply_ui_opacity"):
            self.assertTrue(hasattr(MainWindow, name), f"MainWindow 缺少 {name}")


class EqPresetPersistenceTest(unittest.TestCase):
    """EQ 自定义预设写回 Settings（重启后仍在）。"""

    def _window(self) -> _FakeWindow:
        window = _FakeWindow()
        window.logs = []
        window._save_settings_quietly = lambda: setattr(
            window, "saves", window.saves + 1
        )
        window._append_log = lambda level, message: window.logs.append((level, message))
        return window

    def test_saved_preset_is_stored(self) -> None:
        window = self._window()
        MainWindow._on_eq_preset_saved(window, "我的曲线", [1.5] * 31)
        presets = window.settings.playback_eq_custom_presets
        self.assertIn("我的曲线", presets)
        self.assertEqual(len(presets["我的曲线"]), 31)
        self.assertEqual(window.saves, 1, "保存预设必须落盘")

    def test_deleted_preset_is_removed(self) -> None:
        window = self._window()
        window.settings.playback_eq_custom_presets = {"甲": [0.0] * 31}
        MainWindow._on_eq_preset_deleted(window, "甲")
        self.assertEqual(window.settings.playback_eq_custom_presets, {})
        self.assertEqual(window.saves, 1)

    def test_empty_name_is_ignored(self) -> None:
        window = self._window()
        MainWindow._on_eq_preset_saved(window, "  ", [0.0] * 31)
        self.assertEqual(window.settings.playback_eq_custom_presets, {})
        self.assertEqual(window.saves, 0)


class NewUiI18nTest(unittest.TestCase):
    """v1.8 新增文案（品牌条不透明度 + 31 段 EQ）必须 8 语种齐全。

    项目惯例：界面新增文案一律补 8 语种；这里只守护「新加的这批」，
    避免 `tr_text` 在切语言后原样返回中文（QA 提出的风险项）。
    """

    #: 本轮新增/改写的界面文案（简体中文原文）
    STRINGS = (
        "界面不透明度",
        "界面不透明度（10–100%，默认 95%）",
        "停止播放",
        "切到迷你",
        "均衡器",
        "均衡器 · 31 段",
        "均衡器（31 段）",
        "31 段 · 峰值 ±12 dB",
        "频段",
        "启用均衡器",
        "预设曲线（内置 + 自定义）",
        "保存预设",
        "预设名称（不能与内置预设重名）",
        "该名称与内置预设重名，请换一个",
        "名称不能为空",
        "删除预设",
        "确定删除预设「{}」？",
        "删除当前自定义预设",
        "把当前 31 段曲线保存为命名预设",
        "回到平直（全 0 dB）",
        "平直",
        "流行",
        "摇滚",
        "古典",
        "人声",
        "低音",
        "高音",
        "响度",
    )

    def tearDown(self) -> None:
        # 语言是全局状态，必须复位，否则会影响后续用例
        from seqaudio.ui import i18n

        i18n.set_locale("zh_CN")

    def test_all_locales_translate_new_strings(self) -> None:
        from seqaudio.ui import i18n

        for source in self.STRINGS:
            entry = i18n._UI_TEXT.get(source)
            self.assertIsNotNone(entry, f"文案未登记：{source}")
            for locale in ("zh_CN", "zh_TW", "en", "ja", "ko", "ru", "fr", "de"):
                self.assertTrue(
                    str(entry.get(locale) or "").strip(),
                    f"{locale} 缺少译文：{source}",
                )
            # 拉丁/西里尔语种必须与中文原文不同，否则等于没翻译
            for locale in ("en", "de", "fr", "ru"):
                self.assertNotEqual(
                    entry[locale], source, f"{locale} 未真正翻译：{source}"
                )
        i18n.set_locale("en")
        self.assertEqual(i18n.tr_text("界面不透明度"), "Interface opacity")
        i18n.set_locale("zh_CN")
        self.assertEqual(i18n.tr_text("界面不透明度"), "界面不透明度")

    def test_template_string_still_formats(self) -> None:
        from seqaudio.ui import i18n

        i18n.set_locale("en")
        text = i18n.tr_text("确定删除预设「{}」？").format("My curve")
        self.assertIn("My curve", text)
        self.assertNotIn("{}", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
