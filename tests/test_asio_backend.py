"""ASIO 后端「可选可播」（v1.8.1 P0-3）自测。

**全部用例都不依赖真实 ASIO 驱动**：声卡、hostapi、``OutputStream``、
采样率支持表一律靠注入的假实现。理由很实在 —— CI 与大多数开发机没有
TempoTec 那台解码器，真机用例一换机器就红，红了就会被"改成 skip"，
最后等于没人看。

覆盖：
* ``plan_output`` 的 ASIO 单级分支（不套共享/独占四级阶梯）；
* ``AudioOutput`` 对 ASIO 上报「驱动独占」且**绝不构造** ``WasapiSettings``；
* ``devices.sounddevice()`` / ``app._bootstrap_asio()`` 的环境变量时机与
  ``--no-asio`` 语义；
* 设置面板：ASIO 不可用时置灰 + 说明、选中 ASIO 时隐藏三档模式。

运行：``python -m unittest tests.test_asio_backend -v``
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QCheckBox  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.player.constants import OutputMode  # noqa: E402
from seqaudio.player.output import AudioOutput  # noqa: E402
from seqaudio.player.ringbuffer import PcmRingBuffer  # noqa: E402
from seqaudio.ui.settings_panel import SettingsPanel  # noqa: E402

_APP = QApplication.instance() or QApplication([])

ASIO_DRIVER = "TempoTecUSBAudio ASIO Driver"

#: 本机实测可用的采样率（写死在这里是**测试数据**，不是运行时能力声明）
ASIO_RATES = (44100, 48000, 88200, 96000, 176400, 352800)

#: ASIO 设备在假表里的全局索引。之所以不放在 0 号，是要复刻真机场景：
#: 启用 ASIO 后 hostapi 表多了一项，WASAPI 默认输出从 22 挪到 23。用例必须
#: 证明"按名字定位"在索引漂移下照样正确，否则哪天有人手滑存了索引也测不出来。
ASIO_INDEX = 23


def _asio_device() -> dev_mod.DeviceInfo:
    return dev_mod.DeviceInfo(
        index=ASIO_INDEX,
        name=ASIO_DRIVER,
        channels=2,
        default_samplerate=44100.0,
        hostapi_index=2,
        hostapi_name="ASIO",
    )


def _wasapi_device() -> dev_mod.DeviceInfo:
    return dev_mod.DeviceInfo(
        index=5, name="扬声器 (Realtek Audio)", channels=2,
        default_samplerate=48000.0, hostapi_index=1, hostapi_name="Windows WASAPI",
    )


class _FakeStream:
    """假 PortAudio 流：只记录参数并模拟 start/stop/close。"""

    def __init__(self, **kwargs) -> None:
        # ``_latency`` 是测试专用注入口（模拟 PortAudio 报的 (input, output)），
        # 先摘掉再记录，否则会混进"交给 PortAudio 的参数"里干扰断言。
        self.latency = kwargs.pop("_latency", (0.010, 0.304))
        self.kwargs = dict(kwargs)
        self.active = False
        self.samplerate = int(kwargs.get("samplerate", 0) or 0)

    def start(self) -> None:
        self.active = True

    def stop(self) -> None:
        self.active = False

    def abort(self) -> None:
        self.active = False

    def close(self) -> None:
        self.active = False


class _FakeSoundDevice:
    """够用的 sounddevice 替身（**刻意不提供 WasapiSettings**）。

    不提供它是有意的：ASIO 设备一旦被构造 ``WasapiSettings`` 就该炸 ——
    那说明 ``_extra_settings_for`` 的分支写错了。
    """

    def __init__(self, rates=ASIO_RATES) -> None:
        self.rates = tuple(rates)
        self.created: list[_FakeStream] = []
        self.checked: list[dict] = []
        #: 假驱动「实测」的延迟（PortAudio 约定 ``(input, output)``）
        self.created_latency: tuple[float, float] = (0.010, 0.304)
        self.default = SimpleNamespace(device=SimpleNamespace(output=ASIO_INDEX))
        # 索引 0..ASIO_INDEX-1 全给 WASAPI 占位（真机上它们就是其它 hostapi 的
        # 输入/输出设备），ASIO 驱动落在 ASIO_INDEX 号 —— 与真机索引漂移一致。
        self._devices = [
            {
                "name": f"占位设备 {index}",
                "max_output_channels": 2,
                "default_samplerate": 48000.0,
                "hostapi": 1,
            }
            for index in range(ASIO_INDEX)
        ]
        self._devices.append({
            "name": ASIO_DRIVER,
            "max_output_channels": 2,
            "default_samplerate": 44100.0,
            "hostapi": 2,
        })
        self._hostapis = [
            {"name": "MME", "default_output_device": -1},
            {"name": "Windows WASAPI", "default_output_device": ASIO_INDEX - 1},
            {"name": "ASIO", "default_output_device": ASIO_INDEX},
        ]

    # ---- 只读查询 ----
    def query_devices(self):
        return list(self._devices)

    def query_hostapis(self):
        return list(self._hostapis)

    def check_output_settings(self, **kwargs):
        self.checked.append(dict(kwargs))
        if int(kwargs.get("samplerate", 0)) not in self.rates:
            raise RuntimeError("Invalid sample rate [PaErrorCode -9997]")
        return None

    def OutputStream(self, **kwargs):
        stream = _FakeStream(**kwargs, _latency=self.created_latency)
        self.created.append(stream)
        return stream

    def WasapiSettings(self, **_kwargs):  # pragma: no cover - 见类 docstring
        raise AssertionError("ASIO 设备不该构造 WasapiSettings")


class TestAsioPlanOutput(unittest.TestCase):
    """``plan_output`` 的 ASIO 分支：单级、恒独占、只降采样率。"""

    def test_exclusive_single_level(self) -> None:
        plan = dev_mod.plan_output(
            _asio_device(), 44100, mode="exclusive",
            probe=lambda device, rate, ch, mode: mode is OutputMode.EXCLUSIVE,
        )
        self.assertEqual((plan.rate, plan.mode, plan.level), (44100, OutputMode.EXCLUSIVE, 1))
        self.assertTrue(plan.asio)
        self.assertFalse(plan.fallback)
        self.assertEqual(plan.note, "")

    def test_shared_request_is_still_exclusive(self) -> None:
        """ASIO 没有共享模式：请求共享也必须如实按独占计划，并写下原因。"""
        plan = dev_mod.plan_output(
            _asio_device(), 44100, mode="shared",
            probe=lambda device, rate, ch, mode: True,
        )
        self.assertEqual(plan.mode, OutputMode.EXCLUSIVE)
        self.assertEqual(plan.requested_mode, OutputMode.SHARED)
        self.assertIn("没有共享模式", plan.note)

    def test_rate_fallback_keeps_exclusive(self) -> None:
        """705.6 kHz 被驱动拒绝时只能降采样率，**不许**退回共享。"""
        plan = dev_mod.plan_output(
            _asio_device(), 705600, mode="exclusive",
            probe=lambda device, rate, ch, mode: (
                mode is OutputMode.EXCLUSIVE and rate == 352800
            ),
        )
        self.assertEqual((plan.rate, plan.mode, plan.level), (352800, OutputMode.EXCLUSIVE, 3))
        self.assertTrue(plan.fallback)
        self.assertIn("705600", plan.note)
        self.assertIn("352800", plan.note)

    def test_no_rate_available_raises_with_actionable_text(self) -> None:
        with self.assertRaises(dev_mod.RateUnsupportedError) as ctx:
            dev_mod.plan_output(
                _asio_device(), 44100, mode="exclusive", probe=lambda *a: False
            )
        text = str(ctx.exception)
        self.assertIn("ASIO 只支持驱动声明的采样率", text)
        self.assertIn("WASAPI", text, "必须给出换后端这条可行出路")
        self.assertTrue(ctx.exception.tried)

    def test_skipped_level_does_not_retry_forever(self) -> None:
        """已失败的层级不许再出：两级都失败就该报错，而不是原地重试。"""
        with self.assertRaises(dev_mod.RateUnsupportedError):
            dev_mod.plan_output(
                _asio_device(), 44100, mode="exclusive",
                probe=lambda device, rate, ch, mode: True,
                skip_levels=(1, 3),
            )

    def test_wasapi_device_still_uses_four_level_ladder(self) -> None:
        """回归：ASIO 分支不能顺手把 WASAPI 的四级阶梯吃掉。"""
        plan = dev_mod.plan_output(
            _wasapi_device(), 192000, mode="exclusive",
            probe=lambda device, rate, ch, mode: (
                mode is OutputMode.SHARED and rate == 48000
            ),
        )
        self.assertEqual((plan.mode, plan.level), (OutputMode.SHARED, 4))
        self.assertFalse(plan.asio)

    def test_candidates_keep_dop_carrier_rates(self) -> None:
        """回退链必须包含 352.8k/176.4k：少了它们 DoP 会从 705.6k 直落 88.2k。"""
        rates = dev_mod._asio_candidate_rates(_asio_device(), 705600)
        self.assertEqual(rates[0], 705600, "源采样率永远排第一")
        self.assertIn(352800, rates)
        self.assertIn(176400, rates)
        self.assertNotIn(1411200, rates, "只许向下回退，不许往上试")
        self.assertEqual(len(rates), len(set(rates)), "候选不许重复")


class TestAsioProbe(unittest.TestCase):
    """``supports_rate`` 必须认 ASIO 的独占（否则 ASIO 永远探测不过）。"""

    def setUp(self) -> None:
        self.sd = _FakeSoundDevice()
        patcher = mock.patch.object(dev_mod, "_SD", self.sd)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_asio_exclusive_probe_passes(self) -> None:
        self.assertTrue(
            dev_mod.supports_rate(_asio_device(), 96000, 2, mode="exclusive"),
            "ASIO 的独占是驱动天然行为，不该因为没有 WasapiSettings 被判死",
        )
        self.assertTrue(self.sd.checked, "探测应当真的问过 PortAudio")

    def test_asio_probe_sends_no_extra_settings(self) -> None:
        dev_mod.supports_rate(_asio_device(), 96000, 2, mode="exclusive")
        self.assertTrue(self.sd.checked)
        self.assertIsNone(self.sd.checked[-1].get("extra_settings"))

    def test_mme_exclusive_still_rejected(self) -> None:
        mme = dev_mod.DeviceInfo(
            index=1, name="扬声器 (Realtek)", channels=2,
            default_samplerate=48000.0, hostapi_index=0, hostapi_name="MME",
        )
        self.assertFalse(
            dev_mod.supports_rate(mme, 48000, 2, mode="exclusive"),
            "MME 确实没有独占，这条不能因为放行 ASIO 一起松掉",
        )


class TestAsioAudioOutput(unittest.TestCase):
    """``AudioOutput`` 在 ASIO 设备上：模式如实报独占、不传 WasapiSettings。"""

    def setUp(self) -> None:
        self.sd = _FakeSoundDevice()
        patcher = mock.patch.object(dev_mod, "_SD", self.sd)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.ring = PcmRingBuffer(8192, channels=2, dtype="int32")
        self.output = AudioOutput(self.ring, device=ASIO_DRIVER, backend="asio")

    def tearDown(self) -> None:
        self.output.stop()

    def test_prepare_reports_driver_exclusive(self) -> None:
        rate = self.output.prepare(44100, 2)
        self.assertEqual(rate, 44100)
        self.assertIs(self.output.mode_actual, OutputMode.EXCLUSIVE)
        self.assertTrue(self.output.exclusive_actual)
        self.assertFalse(self.output.downgraded, "ASIO 的独占不是降级来的")
        snapshot = self.output.snapshot()
        self.assertTrue(snapshot["asio"])
        self.assertEqual(snapshot["mode"], "exclusive")
        self.assertEqual(snapshot["backend"], "asio")

    def test_no_wasapi_settings_for_asio(self) -> None:
        self.output.prepare(44100, 2)
        self.assertTrue(self.sd.created, "应当真的开了一条流")
        self.assertNotIn(
            "extra_settings", self.sd.created[0].kwargs,
            "ASIO 设备不许传 WasapiSettings",
        )

    def test_rate_fallback_stays_exclusive(self) -> None:
        """驱动只给到 352.8k 时：率降下来，模式仍是独占，且标注回退原因。"""
        self.sd.rates = (352800,)
        rate = self.output.prepare(705600, 2)
        self.assertEqual(rate, 352800)
        self.assertIs(self.output.mode_actual, OutputMode.EXCLUSIVE)
        self.assertTrue(self.output.snapshot()["fallback"])
        self.assertTrue(self.output.downgraded is False)

    def test_all_rates_rejected_raises(self) -> None:
        self.sd.rates = ()
        with self.assertRaises(dev_mod.RateUnsupportedError):
            self.output.prepare(44100, 2)

    def test_device_format_marks_asio(self) -> None:
        self.output.prepare(44100, 2)
        self.assertTrue(self.output.device_format()["asio"])

    def test_latency_comes_from_measurement_not_from_the_hint(self) -> None:
        """ASIO 的延迟必须报**驱动实测值**，不能报我们请求的 40 ms。

        真实事故（v1.8.1 收口）：engine 传 ``latency=0.04``，PortAudio 的 ASIO
        后端把这个请求值原样回显到 ``stream.latency``，于是信号路径写着
        「延迟 40 ms」而真实缓冲是 0.11~0.30 s —— 自我印证的一句谎。
        做法：ASIO 不传 latency 提示，读数只用 ``stream.latency``。
        """
        self.output.prepare(44100, 2)
        stream = self.sd.created[0]
        self.assertNotIn(
            "latency", stream.kwargs,
            "ASIO 不该把 latency 提示交给 PortAudio（会被原样回显，变成假实测值）",
        )
        snapshot = self.output.snapshot()
        # 假流报的是 (0.010, 0.304)：取输出侧 304 ms，而不是 40 ms
        self.assertAlmostEqual(snapshot["latency_ms"], 304.0, places=1)
        self.assertEqual(snapshot["latency_source"], "measured")
        self.assertEqual(self.output.device_format()["latency_source"], "measured")

    def test_latency_falls_back_to_estimate_when_driver_reports_nothing(self) -> None:
        """驱动不报延迟时退回估算，并**如实标注**是估算。"""
        self.sd.created_latency = (0.0, 0.0)   # 驱动什么都不报
        self.output.prepare(44100, 2)
        snapshot = self.output.snapshot()
        self.assertEqual(snapshot["latency_source"], "estimated")
        # 估算 = 两块 @44100，明显不是恒定的 40 ms 也不是 0
        self.assertGreater(snapshot["latency_ms"], 0.0)
        self.assertNotAlmostEqual(snapshot["latency_ms"], 40.0, places=1)


class TestAsioBootstrap(unittest.TestCase):
    """环境变量时机与 ``--no-asio`` 语义（**只看行为，不真依赖驱动**）。"""

    def setUp(self) -> None:
        self._saved = os.environ.get("SD_ENABLE_ASIO")
        self._saved_no = os.environ.get("SEQAUDIO_NO_ASIO")
        self._saved_flag = os.environ.get("SEQAUDIO_ASIO_DISABLED")
        for key in ("SD_ENABLE_ASIO", "SEQAUDIO_NO_ASIO", "SEQAUDIO_ASIO_DISABLED"):
            os.environ.pop(key, None)

    def tearDown(self) -> None:
        for key, value in (
            ("SD_ENABLE_ASIO", self._saved),
            ("SEQAUDIO_NO_ASIO", self._saved_no),
            ("SEQAUDIO_ASIO_DISABLED", self._saved_flag),
        ):
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value

    def test_default_enables_asio(self) -> None:
        from seqaudio.app import _bootstrap_asio

        self.assertTrue(_bootstrap_asio([]))
        self.assertIn("SD_ENABLE_ASIO", os.environ)

    def test_no_asio_flag_removes_variable(self) -> None:
        """必须**删除**而不是写 0：sounddevice 只看这个键是否存在。"""
        from seqaudio.app import _bootstrap_asio

        os.environ["SD_ENABLE_ASIO"] = "1"
        self.assertFalse(_bootstrap_asio(["--no-asio"]))
        self.assertNotIn("SD_ENABLE_ASIO", os.environ)
        # 光删变量不够：devices.sounddevice() 的兜底会把缺省的变量补回来，
        # 所以必须留下"用户主动关过"的标记，否则 --no-asio 一次枚举就被推翻。
        self.assertEqual(os.environ.get("SEQAUDIO_ASIO_DISABLED"), "1")

    def test_environment_switch_removes_variable(self) -> None:
        from seqaudio.app import _bootstrap_asio

        os.environ["SEQAUDIO_NO_ASIO"] = "true"
        self.assertFalse(_bootstrap_asio([]))
        self.assertNotIn("SD_ENABLE_ASIO", os.environ)

    def test_bootstrap_is_idempotent(self) -> None:
        from seqaudio.app import _bootstrap_asio

        self.assertTrue(_bootstrap_asio([]))
        self.assertTrue(_bootstrap_asio([]))
        self.assertEqual(os.environ.get("SD_ENABLE_ASIO"), "1")

    def test_run_py_helper_matches_app(self) -> None:
        """``run.py`` 与 ``app.py`` 两份引导必须同义（否则入口不同行为不同）。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("seqaudio_run", ROOT / "run.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)   # 会 import seqaudio.app，但不再执行 main()

        self.assertFalse(module._bootstrap_asio(["--no-asio"]))
        self.assertNotIn("SD_ENABLE_ASIO", os.environ)
        self.assertTrue(module._bootstrap_asio([]))
        self.assertIn("SD_ENABLE_ASIO", os.environ)

    def test_sounddevice_restores_env_before_import(self) -> None:
        """兜底：即使入口没设，``devices.sounddevice()`` 也要在 import 前补上。"""
        dev_mod.reimport_sounddevice()
        try:
            dev_mod.sounddevice()
        except dev_mod.DeviceError as exc:  # pragma: no cover - 无声卡环境
            self.skipTest(f"本机无法加载 sounddevice：{exc}")
        finally:
            dev_mod.reimport_sounddevice()
        self.assertIn("SD_ENABLE_ASIO", os.environ)


class TestAsioLoadFallback(unittest.TestCase):
    """ASIO 构建加载不了时必须**降级**，不能把整个 PortAudio 一起陪葬。

    真实事故（v1.8.1 打包冒烟）：便携版包体只带 ``libportaudio64bit.dll``，
    没有 ``-asio.dll``，``SD_ENABLE_ASIO`` 一置上就在 import 期 dlopen 失败
    （``error 0x7e``），整个程序能开却完全放不出声 —— 比"没有 ASIO 后端"
    严重得多，所以这里必须有用例守住。
    """

    def setUp(self) -> None:
        self._saved = {key: os.environ.get(key) for key in (
            "SD_ENABLE_ASIO", "SEQAUDIO_ASIO_DISABLED", "SEQAUDIO_NO_ASIO",
        )}
        for key in ("SD_ENABLE_ASIO", "SEQAUDIO_ASIO_DISABLED", "SEQAUDIO_NO_ASIO"):
            os.environ.pop(key, None)
        dev_mod.reimport_sounddevice()
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        for key, value in self._saved.items():
            os.environ.pop(key, None)
            if value is not None:
                os.environ[key] = value
        dev_mod.reimport_sounddevice()

    def test_load_failure_falls_back_to_plain_build(self) -> None:
        fake = _FakeSoundDevice()
        calls: list[bool] = []

        def importer(enable_asio: bool):
            calls.append(bool(enable_asio))
            if enable_asio:
                raise OSError(
                    "cannot load library 'libportaudio64bit-asio.dll': error 0x7e"
                )
            return fake

        with mock.patch.object(dev_mod, "_import_sounddevice", side_effect=importer):
            self.assertIs(dev_mod.sounddevice(), fake)

        self.assertEqual(calls, [True, False], "必须先试 ASIO，失败才退回普通构建")
        self.assertNotIn("SD_ENABLE_ASIO", os.environ, "失败后必须摘掉变量，否则下次还炸")
        self.assertEqual(dev_mod.asio_unavailable_reason(), "load_failed")
        self.assertFalse(dev_mod.backend_present("asio"), "加载失败时 ASIO 一律不可用")

    def test_second_call_does_not_retry_asio(self) -> None:
        fake = _FakeSoundDevice()
        calls: list[bool] = []

        def importer(enable_asio: bool):
            calls.append(bool(enable_asio))
            if enable_asio:
                raise OSError("boom")
            return fake

        with mock.patch.object(dev_mod, "_import_sounddevice", side_effect=importer):
            dev_mod.sounddevice()
            dev_mod.sounddevice()
        self.assertEqual(calls, [True, False], "第二次起直接命中缓存，不再重试坏 DLL")

    def test_both_builds_failing_still_raises_device_error(self) -> None:
        def importer(_enable_asio: bool):
            raise OSError("没有 PortAudio")

        with mock.patch.object(dev_mod, "_import_sounddevice", side_effect=importer):
            with self.assertRaises(dev_mod.DeviceError):
                dev_mod.sounddevice()

    def test_switched_off_marker_is_respected(self) -> None:
        """``--no-asio`` 之后，兜底不得把 SD_ENABLE_ASIO 补回来。"""
        os.environ["SEQAUDIO_ASIO_DISABLED"] = "1"
        fake = _FakeSoundDevice()
        calls: list[bool] = []

        def importer(enable_asio: bool):
            calls.append(bool(enable_asio))
            return fake

        with mock.patch.object(dev_mod, "_import_sounddevice", side_effect=importer):
            dev_mod.sounddevice()
        self.assertEqual(calls, [False], "用户关掉 ASIO 后不该再尝试 ASIO 构建")
        self.assertEqual(dev_mod.asio_unavailable_reason(), "switched_off")


class TestAsioSettingsPanel(unittest.TestCase):
    """设置面板：置灰 + 说明、选中 ASIO 时隐藏三档模式、DoP 开关默认降级。"""

    def setUp(self) -> None:
        self.settings = Settings(offline_mode=True)
        self.panel = SettingsPanel(self.settings)
        self.addCleanup(self.panel.deleteLater)

    def _asio_index(self) -> int:
        index = self.panel.backend_combo.findData("asio")
        self.assertGreaterEqual(index, 0, "后端下拉必须有 ASIO 项")
        return index

    def test_unavailable_grays_out_with_reason(self) -> None:
        self.panel.set_asio_state(False)
        item = self.panel.backend_combo.model().item(self._asio_index())
        self.assertIsNotNone(item)
        self.assertFalse(item.isEnabled())
        tip = self.panel.backend_combo.itemData(self._asio_index(), 3)  # ToolTipRole
        self.assertIn("SD_ENABLE_ASIO", str(tip))

    def test_available_enables_entry(self) -> None:
        self.panel.set_asio_state(True)
        item = self.panel.backend_combo.model().item(self._asio_index())
        self.assertTrue(item.isEnabled())

    def test_selected_but_unavailable_falls_back_to_mme(self) -> None:
        self.panel.backend_combo.setCurrentIndex(self._asio_index())
        self.panel.set_asio_state(False)
        self.assertEqual(self.panel._current_backend_key(), "mme")
        self.assertIn("SD_ENABLE_ASIO", self.panel.backend_hint.text())

    def test_asio_hides_three_modes_and_shows_note(self) -> None:
        self.panel.set_asio_state(True)
        self.panel.backend_combo.setCurrentIndex(self._asio_index())
        # 用 isHidden() 而不是 isVisibleTo()：面板没 show() 时所属页签的
        # QScrollArea 本身就是隐藏的，isVisibleTo() 恒为 False（settings_panel
        # 里刷新能力那处也是同一个原因改用 isHidden()）。
        self.assertTrue(self.panel.mode_row.isHidden(), "ASIO 下三档模式应当整行收起")
        self.assertFalse(self.panel.asio_note.isHidden(), "ASIO 下应显示独占说明")
        self.assertIs(self.panel._current_output_mode(), OutputMode.EXCLUSIVE)

        # 切回 WASAPI：三档模式重新出现，说明行隐藏
        self.panel.backend_combo.setCurrentIndex(
            self.panel.backend_combo.findData("wasapi")
        )
        self.assertFalse(self.panel.mode_row.isHidden())
        self.assertTrue(self.panel.asio_note.isHidden())

    def test_asio_backend_survives_apply_to(self) -> None:
        self.panel.set_asio_state(True)
        self.panel.backend_combo.setCurrentIndex(self._asio_index())
        applied = self.panel.apply_to(Settings(offline_mode=True))
        self.assertEqual(applied.playback_output_backend, "asio")
        self.assertEqual(applied.playback_output_mode, "exclusive")

    def test_dop_switch_is_disabled_until_engine_confirms(self) -> None:
        self.assertIsInstance(self.panel.dop_check, QCheckBox)
        self.assertFalse(self.panel.dop_check.isEnabled())
        self.assertIn("PCM", self.panel.dop_check.toolTip())

        self.panel.set_dop_available(True)
        self.assertTrue(self.panel.dop_check.isEnabled())
        self.assertNotIn("回退", self.panel.dop_check.toolTip())

    def test_dop_setting_roundtrip(self) -> None:
        self.panel.dop_check.setChecked(False)
        settings = self.panel.apply_to(Settings(offline_mode=True))
        self.assertFalse(settings.playback_dsd_dop)
        self.panel.load_from(settings)
        self.assertFalse(self.panel.dop_check.isChecked())


class TestPortAudioBundle(unittest.TestCase):
    """``build.py`` 必须把两个 win64 变体都打进包（阻塞项，v1.8.1 收口）。

    为什么值得用例守：v1.7 的 ``build_onedir()`` 会主动**删掉** ASIO DLL 并断言
    "出现 -asio 就退出"。v1.8.1 起 ASIO 是一等后端，那段逻辑不改，用户装完就
    永远点不亮 ASIO；反过来若哪天又"顺手清理"掉普通 DLL，一次 ``--no-asio``
    或 ASIO 库加载失败就会让程序彻底没声。这里直接调真正的剪枝函数，不打包。
    """

    ALL_VARIANTS = (
        "libportaudio64bit.dll",
        "libportaudio64bit-asio.dll",
        "libportaudio32bit.dll",
        "libportaudio32bit-asio.dll",
        "libportaudioarm64.dll",
        "libportaudioarm64-asio.dll",
        "libportaudio.dylib",
        "README.md",
    )

    def _app_dir(self, names=ALL_VARIANTS):
        tmp = Path(tempfile.mkdtemp(prefix="pa_prune_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        pa_dir = Path(tmp) / "_internal" / "_sounddevice_data" / "portaudio-binaries"
        pa_dir.mkdir(parents=True)
        for name in names:
            (pa_dir / name).write_bytes(b"\0" * 1024)
        return Path(tmp), pa_dir

    def test_keeps_both_win64_variants_and_drops_the_rest(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("seqaudio_build", ROOT / "build.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        app_dir, pa_dir = self._app_dir()
        leftovers = module._prune_portaudio(app_dir)

        self.assertEqual(
            leftovers,
            sorted(["libportaudio64bit.dll", "libportaudio64bit-asio.dll", "README.md"]),
            "只该留下两个 win64 变体（README 不是二进制，不参与清理）",
        )
        self.assertTrue((pa_dir / "libportaudio64bit-asio.dll").exists(), "ASIO 变体不许被删")

    def test_missing_asio_dll_fails_the_build(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("seqaudio_build", ROOT / "build.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        app_dir, _pa_dir = self._app_dir(("libportaudio64bit.dll", "README.md"))
        with self.assertRaises(SystemExit) as ctx:
            module._prune_portaudio(app_dir)
        self.assertIn("asio", str(ctx.exception).lower())

    def test_passes_with_exactly_the_two_variants(self) -> None:
        import importlib.util

        spec = importlib.util.spec_from_file_location("seqaudio_build", ROOT / "build.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        _app_dir, pa_dir = self._app_dir(
            ("libportaudio64bit.dll", "libportaudio64bit-asio.dll")
        )
        self.assertEqual(
            module._verify_portaudio(pa_dir),
            ["libportaudio64bit-asio.dll", "libportaudio64bit.dll"],
        )

    def test_residual_32bit_variant_is_a_hard_failure(self) -> None:
        """两个都在、还混进一个 32bit/arm64 → 必须 SystemExit。

        这一支只有把校验单独成函数才测得到：``_prune_portaudio()`` 自己刚删完，
        "残留"永远不会成立。换句话说，这个用例防的就是"有人把清理逻辑删了"。
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location("seqaudio_build", ROOT / "build.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        for residual in ("libportaudio32bit.dll", "libportaudioarm64-asio.dll"):
            with self.subTest(residual=residual):
                _app_dir, pa_dir = self._app_dir(
                    ("libportaudio64bit.dll", "libportaudio64bit-asio.dll", residual)
                )
                with self.assertRaises(SystemExit) as ctx:
                    module._verify_portaudio(pa_dir)
                self.assertIn("残留", str(ctx.exception))

    def test_missing_portaudio_dir_is_a_hard_failure(self) -> None:
        """连目录都没有 = 两个 DLL 都没进包，不能静默放过。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("seqaudio_build", ROOT / "build.py")
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        tmp = Path(tempfile.mkdtemp(prefix="pa_nodir_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        with self.assertRaises(SystemExit) as ctx:
            module._prune_portaudio(Path(tmp))
        self.assertIn("PortAudio", str(ctx.exception))


class TestBridgeAsioPresent(unittest.TestCase):
    """桥接层：ASIO 可用性 = 构建里有 **且** 至少一台设备**探测可通过**。"""

    def test_present_requires_a_device(self) -> None:
        from seqaudio.ui.player_bridge import PlayerBridge

        bridge = PlayerBridge(Settings(offline_mode=True))
        try:
            with mock.patch.object(dev_mod, "backend_present", return_value=True), \
                 mock.patch.object(dev_mod, "asio_usable_device", return_value=None):
                self.assertFalse(bridge.asio_present(refresh=True),
                                 "探测一台都不通过时不许点亮（哪怕枚举得到）")
            with mock.patch.object(dev_mod, "backend_present", return_value=True), \
                 mock.patch.object(dev_mod, "asio_usable_device",
                                   return_value=SimpleNamespace(name=ASIO_DRIVER)):
                self.assertTrue(bridge.asio_present(refresh=True))
        finally:
            bridge.shutdown()

    def test_present_requires_the_build(self) -> None:
        from seqaudio.ui.player_bridge import PlayerBridge

        bridge = PlayerBridge(Settings(offline_mode=True))
        try:
            with mock.patch.object(dev_mod, "backend_present", return_value=False), \
                 mock.patch.object(dev_mod, "asio_usable_device",
                                   return_value=SimpleNamespace(name=ASIO_DRIVER)):
                self.assertFalse(bridge.asio_present(refresh=True))
        finally:
            bridge.shutdown()

    def test_result_is_cached(self) -> None:
        from seqaudio.ui.player_bridge import PlayerBridge

        bridge = PlayerBridge(Settings(offline_mode=True))
        try:
            calls: list[str] = []

            def fake_usable():
                calls.append("probe")
                return SimpleNamespace(name=ASIO_DRIVER)

            with mock.patch.object(dev_mod, "backend_present", return_value=True), \
                 mock.patch.object(dev_mod, "asio_usable_device",
                                   side_effect=fake_usable):
                self.assertTrue(bridge.asio_present())
                self.assertTrue(bridge.asio_present())
                self.assertEqual(len(calls), 1, "同一进程内不必反复探测")
                self.assertTrue(bridge.asio_present(refresh=True))
                self.assertEqual(len(calls), 2)
        finally:
            bridge.shutdown()


class TestAsioUsableDevice(unittest.TestCase):
    """``devices.asio_usable_device``：零出声探测 + 短开流验证（v1.8.2）。"""

    def _device(self, name: str, rate: float) -> "dev_mod.DeviceInfo":
        return dev_mod.DeviceInfo(
            index=18,
            name=name,
            channels=2,
            default_samplerate=rate,
            is_default=False,
            hostapi_index=3,
            hostapi_name="ASIO",
        )

    def test_no_devices_returns_none(self) -> None:
        with mock.patch.object(dev_mod, "list_output_devices", return_value=[]):
            self.assertIsNone(dev_mod.asio_usable_device())

    def test_all_probe_failures_return_none(self) -> None:
        devices = [self._device("坏驱动 ASIO", 44100.0)]
        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(probe=lambda *a, **k: False)
        self.assertIsNone(got)

    def test_first_passing_device_wins(self) -> None:
        good = self._device("TempoTec ASIO", 44100.0)
        devices = [self._device("Realtek ASIO", 44100.0), good]
        probed: list[tuple[str, int, int]] = []

        def probe(device, rate, channels, mode) -> bool:
            probed.append((device.name, int(rate), int(channels)))
            return device is good

        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=probe, open_probe=lambda *a, **k: True
            )
        self.assertIs(got, good)
        self.assertTrue(probed, "至少要探测一次")
        self.assertTrue(
            all(channels == 2 for _, _, channels in probed),
            "按立体声探测（与真实起播口径一致）",
        )

    def test_probe_exception_counts_as_failure(self) -> None:
        """单台探测抛异常 = 当它不可用，换下一台/返回 None，不许把异常抛出。"""
        devices = [self._device("会炸的 ASIO", 44100.0)]

        def probe(device, rate, channels, mode) -> bool:
            raise RuntimeError("驱动炸了")

        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            self.assertIsNone(dev_mod.asio_usable_device(probe=probe))

    def test_open_stream_failure_rejects_device(self) -> None:
        """v1.8.2：check 通过但**开流失败**（空壳驱动 -9985/-9999）→ 判不可用。"""
        devices = [self._device("Realtek ASIO (空壳)", 44100.0)]
        opened: list[tuple[str, int]] = []

        def open_probe(device, rate, channels) -> bool:
            opened.append((device.name, int(rate)))
            raise RuntimeError("Invalid device [PaErrorCode -9985]")

        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=lambda *a, **k: True, open_probe=open_probe
            )
        self.assertIsNone(got, "开流失败的 ASIO 设备不许被当成可用")
        self.assertTrue(opened, "check 通过后必须做开流验证")

    def test_open_stream_success_passes(self) -> None:
        """零出声探测 + 开流验证都通过 → 返回该设备。"""
        devices = [self._device("TempoTec ASIO", 48000.0)]
        rates: list[int] = []

        def open_probe(device, rate, channels) -> bool:
            rates.append(int(rate))
            return True

        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=lambda *a, **k: True, open_probe=open_probe
            )
        self.assertIsNotNone(got)
        self.assertEqual(rates, [48000], "设备默认采样率优先验证")

    def test_playing_skips_open_stream(self) -> None:
        """播放保护：正在播放（尤其 ASIO 独占）时跳过开流验证，不打断播放。"""
        devices = [self._device("TempoTec ASIO", 44100.0)]
        opened: list[int] = []

        def open_probe(device, rate, channels) -> bool:
            opened.append(int(rate))
            return True

        with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
            got = dev_mod.asio_usable_device(
                probe=lambda *a, **k: True,
                open_probe=open_probe,
                playing=True,
            )
        self.assertIsNotNone(got)
        self.assertEqual(opened, [], "播放中绝不允许开第二条流")

    def test_playing_hook_from_bridge_query(self) -> None:
        """``set_playing_query`` 注入的钩子生效：播放中跳过开流验证。"""
        devices = [self._device("TempoTec ASIO", 44100.0)]
        opened: list[int] = []

        def open_probe(device, rate, channels) -> bool:
            opened.append(int(rate))
            return True

        dev_mod.set_playing_query(lambda: True)
        try:
            with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
                got = dev_mod.asio_usable_device(
                    probe=lambda *a, **k: True, open_probe=open_probe
                )
            self.assertIsNotNone(got)
            self.assertEqual(opened, [], "钩子报「播放中」时不得开流")
        finally:
            dev_mod.set_playing_query(None)

        # 钩子报「没在播」→ 开流验证照常执行
        dev_mod.set_playing_query(lambda: False)
        try:
            with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
                got = dev_mod.asio_usable_device(
                    probe=lambda *a, **k: True, open_probe=open_probe
                )
            self.assertIsNotNone(got)
            self.assertEqual(len(opened), 1)
        finally:
            dev_mod.set_playing_query(None)

    def test_playing_hook_exception_is_tolerated(self) -> None:
        """钩子抛异常按「未在播放」处理，不许拖垮可用性探测。"""
        devices = [self._device("TempoTec ASIO", 44100.0)]

        def boom() -> bool:
            raise RuntimeError("状态查询炸了")

        dev_mod.set_playing_query(boom)
        try:
            self.assertFalse(dev_mod.playback_in_progress())
            with mock.patch.object(dev_mod, "list_output_devices", return_value=devices):
                got = dev_mod.asio_usable_device(
                    probe=lambda *a, **k: True, open_probe=lambda *a, **k: True
                )
            self.assertIsNotNone(got)
        finally:
            dev_mod.set_playing_query(None)

    def test_default_open_probe_uses_silent_short_stream(self) -> None:
        """默认开流验证：exclusive/int16/0.1~0.2s 静音流，start→write→stop→close。"""
        fake_sd = SimpleNamespace(created=[])

        class _Stream:
            def __init__(self, **kwargs) -> None:
                self.kwargs = kwargs
                self.started = False
                self.stopped = False
                self.closed = False
                self.written: list[object] = []

            def start(self) -> None:
                self.started = True

            def write(self, data) -> None:
                self.written.append(data)

            def stop(self) -> None:
                self.stopped = True

            def close(self) -> None:
                self.closed = True

        def factory(**kwargs) -> "_Stream":
            stream = _Stream(**kwargs)
            fake_sd.created.append(stream)
            return stream

        fake_sd.OutputStream = factory
        device = self._device("TempoTec ASIO", 44100.0)
        with mock.patch.object(dev_mod, "sounddevice", return_value=fake_sd):
            self.assertTrue(dev_mod._asio_open_stream_ok(device, 44100, 2))
        self.assertEqual(len(fake_sd.created), 1)
        stream = fake_sd.created[0]
        self.assertEqual(stream.kwargs["samplerate"], 44100)
        self.assertEqual(stream.kwargs["channels"], 2)
        self.assertEqual(stream.kwargs["dtype"], "int16")
        self.assertEqual(stream.kwargs["device"], 18)
        self.assertIsNone(stream.kwargs["extra_settings"], "ASIO 不传 WasapiSettings")
        self.assertTrue(stream.started and stream.stopped and stream.closed)
        self.assertEqual(len(stream.written), 1, "写入一次静音缓冲")
        buffer = stream.written[0]
        self.assertEqual(buffer.shape, (int(44100 * 0.15), 2), "0.1~0.2 s 静音流")

    def test_default_open_probe_swallows_write_errors(self) -> None:
        """个别驱动对极短流 write 报错：不影响「能开流」的结论。"""
        class _Stream:
            def __init__(self, **_kwargs) -> None:
                pass

            def start(self) -> None:
                pass

            def write(self, _data) -> None:
                raise RuntimeError("write failed")

            def stop(self) -> None:
                pass

            def close(self) -> None:
                pass

        fake_sd = SimpleNamespace(OutputStream=_Stream)
        device = self._device("TempoTec ASIO", 44100.0)
        with mock.patch.object(dev_mod, "sounddevice", return_value=fake_sd):
            self.assertTrue(dev_mod._asio_open_stream_ok(device, 44100, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
