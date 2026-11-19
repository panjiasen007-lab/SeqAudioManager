"""v1.7 输出层与设备能力探测自测（T01 + T02）。

运行：``python tests/test_player_devices.py``（需要真实声卡；没有设备时相关用例自动 skip）。

本文件的核心不是"代码能跑"，而是**把主理人实测出来的四条硬事实钉成断言**：

1. ``check_output_settings(extra_settings=...)`` 与真实开流结论一致（设计 §2.2 补测 A/D）；
2. dev21「扬声器 (Realtek(R) Audio)」在**独占**下解锁 96k/192k，共享模式全部被拒；
3. 8 声道设备（dev19 Strix 7.1）按 2ch 探测必然假阴性 —— 必须用实际声道数；
4. 严格独占 + float32 必然 ``-9997``，所以"严格"与"整数通路"是强绑定。

设备按**名字**定位而不是硬编码索引：PortAudio 的全局索引会随驱动/插拔变化，
硬编码 21 号在别人机器上是另一台设备，那样写出来的测试只会骗自己。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.player.constants import OutputMode  # noqa: E402
from seqaudio.player.output import AudioOutput, OutputConfig, map_portaudio_error  # noqa: E402
from seqaudio.player.ringbuffer import PcmRingBuffer  # noqa: E402


def _has_device() -> bool:
    try:
        dev_mod.pick_default_device()
        return True
    except Exception:  # noqa: BLE001
        return False


HAS_DEVICE = _has_device()


def _find(name_part: str, backend: str = "wasapi") -> dev_mod.DeviceInfo | None:
    for device in dev_mod.list_output_devices(backend):
        if name_part.lower() in device.name.lower():
            return device
    return None


# ---------------------------------------------------------------- T01 输出层


class TestOutputConfig(unittest.TestCase):
    """三参数与旧布尔镜像。"""

    def test_mode_mirrors_legacy_bool(self) -> None:
        cfg = OutputConfig(exclusive=True)
        self.assertIs(cfg.mode, OutputMode.EXCLUSIVE, "v1.6 的 exclusive=True 应镜像成宽松独占")
        self.assertTrue(cfg.exclusive)
        self.assertFalse(cfg.explicit_format, "宽松独占不做格式协商")

    def test_strict_sets_explicit(self) -> None:
        cfg = OutputConfig(mode="exclusive_strict")
        self.assertIs(cfg.mode, OutputMode.EXCLUSIVE_STRICT)
        self.assertTrue(cfg.explicit_format, "严格独占必须 explicit_sample_format=True")
        self.assertTrue(cfg.exclusive)

    def test_shared_has_no_exclusive(self) -> None:
        cfg = OutputConfig()
        self.assertIs(cfg.mode, OutputMode.SHARED)
        self.assertFalse(cfg.exclusive)
        self.assertFalse(cfg.explicit_format)

    def test_unknown_mode_falls_back(self) -> None:
        cfg = OutputConfig(mode="no_such_mode")
        self.assertIs(cfg.mode, OutputMode.SHARED)


class TestErrorDisambiguation(unittest.TestCase):
    """-9997 双义、-9996 独占、-9985 独占争用（设计 §4.4 五种场景）。"""

    def _map(self, text: str, **kwargs):
        return map_portaudio_error(RuntimeError(text), **kwargs)

    def test_strict_float_is_format_problem_not_rate(self) -> None:
        """严格 + float32 的 -9997 必须说"格式"，不能让用户去改采样率。"""
        mapped = self._map(
            "Invalid sample rate [PaErrorCode -9997]",
            mode=OutputMode.EXCLUSIVE_STRICT,
            dtype="float32",
            rate=44100,
        )
        text = str(mapped)
        self.assertIn("整数", text)
        self.assertIn("float32", text)
        self.assertNotIn("设备不支持该采样率", text)

    def test_strict_int_lists_supported_rates(self) -> None:
        mapped = self._map(
            "Invalid sample rate [PaErrorCode -9997]",
            mode=OutputMode.EXCLUSIVE_STRICT,
            dtype="int32",
            rate=44100,
            supported_rates=(48000, 96000),
        )
        text = str(mapped)
        self.assertIn("独占", text)
        self.assertIn("48000", text)
        self.assertIn("44100", text)

    def test_shared_rate_message_unchanged(self) -> None:
        mapped = self._map(
            "Invalid sample rate [PaErrorCode -9997]",
            mode=OutputMode.SHARED,
            dtype="int32",
            rate=44100,
        )
        self.assertIn("设备不支持该采样率", str(mapped))

    def test_invalid_device_under_exclusive(self) -> None:
        mapped = self._map("Invalid device [PaErrorCode -9996]", mode=OutputMode.EXCLUSIVE)
        text = str(mapped)
        self.assertIn("不支持独占", text)
        self.assertIn("共享", text)

    def test_busy_under_exclusive(self) -> None:
        mapped = self._map("Device unavailable [PaErrorCode -9985]", mode=OutputMode.EXCLUSIVE)
        self.assertIn("独占", str(mapped))

    def test_backward_compatible_signature(self) -> None:
        """旧调用（不带上下文）仍然能用 —— T02 的代码路径不能因为新参数全改。"""
        mapped = map_portaudio_error(RuntimeError("Invalid sample rate [PaErrorCode -9997]"))
        self.assertIn("设备不支持该采样率", str(mapped))


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
class TestOutputLayer(unittest.TestCase):
    """真实设备上的输出层行为。"""

    def test_strict_float_rejected_before_opening(self) -> None:
        """严格 + float32 在**开流之前**就被拦下，且原因说的是格式不是采样率。"""
        ring = PcmRingBuffer.from_seconds(0.5, 44100, 2, "float32")
        output = AudioOutput(ring, device="", backend="wasapi", mode=OutputMode.EXCLUSIVE_STRICT)
        with self.assertRaises(Exception) as ctx:
            output.prepare(44100, 2)
        self.assertIn("整数", str(ctx.exception))
        self.assertFalse(output.is_open, "拦截必须发生在开流之前")

    def test_non_wasapi_exclusive_downgrades_without_error(self) -> None:
        """MME 请求独占：不抛错，如实降级为共享（T01 ⑤）。"""
        device = _find("", "mme")
        if device is None:
            self.skipTest("没有 MME 设备")
        ring = PcmRingBuffer.from_seconds(0.5, 44100, 2, "int16")
        output = AudioOutput(
            ring, device=device.index, backend="mme", mode=OutputMode.EXCLUSIVE
        )
        try:
            rate = output.prepare(44100, 2)
            self.assertGreater(rate, 0)
            self.assertIs(output.actual_mode(), OutputMode.SHARED)
            self.assertFalse(output.exclusive_actual)
            snapshot = output.snapshot()
            self.assertFalse(snapshot["exclusive"], "snapshot 必须报实际值")
            self.assertEqual(snapshot["mode_requested"], "exclusive")
        finally:
            output.stop()

    def test_exclusive_actual_reported_on_wasapi(self) -> None:
        """WASAPI 独占成功时 snapshot 报 exclusive=True + 系统混音率。"""
        device = _find("Realtek(R) Audio")
        if device is None:
            self.skipTest("没有 Realtek 扬声器（WASAPI）")
        if not dev_mod.supports_rate(device, 44100, mode=OutputMode.EXCLUSIVE):
            self.skipTest("该设备不支持 44.1k 独占")
        ring = PcmRingBuffer.from_seconds(0.5, 44100, 2, "int32")
        output = AudioOutput(
            ring, device=device.index, backend="wasapi", mode=OutputMode.EXCLUSIVE
        )
        try:
            rate = output.prepare(44100, 2)
            self.assertEqual(rate, 44100)
            snapshot = output.snapshot()
            self.assertTrue(snapshot["exclusive"], "实际拿到独占就必须报 True")
            self.assertEqual(snapshot["mode"], "exclusive")
            self.assertGreater(snapshot["system_mix_rate"], 0)
            self.assertEqual(snapshot["system_mix_rate"], int(device.rate))
        finally:
            output.stop()


# ---------------------------------------------------------------- T02 探测层


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
class TestCapabilityMatrix(unittest.TestCase):
    """把主理人的实测矩阵钉成断言（设备按名字找，索引会变）。"""

    def test_dev21_unlocks_high_rates_in_exclusive(self) -> None:
        """dev21「扬声器 (Realtek(R) Audio)」：共享全拒、独占解锁 44.1/96/192。"""
        device = _find("Realtek(R) Audio")
        if device is None:
            self.skipTest("没有 Realtek 扬声器（WASAPI）")
        self.assertFalse(
            dev_mod.supports_rate(device, 44100, mode=OutputMode.SHARED),
            "共享模式下 44.1k 应被拒（Windows 混音格式是 48k）",
        )
        for rate in (44100, 96000, 192000):
            self.assertTrue(
                dev_mod.supports_rate(device, rate, mode=OutputMode.EXCLUSIVE),
                f"独占模式应支持 {rate} Hz",
            )
            self.assertTrue(
                dev_mod.supports_rate(device, rate, mode=OutputMode.EXCLUSIVE_STRICT),
                f"严格独占应支持 {rate} Hz",
            )
        self.assertTrue(dev_mod.supports_rate(device, 48000, mode=OutputMode.SHARED))

        capability = dev_mod.probe_capabilities(device)
        self.assertTrue(capability.supports(44100, OutputMode.EXCLUSIVE))
        self.assertFalse(capability.supports(44100, OutputMode.SHARED))
        self.assertIn(96000, capability.unlocked_rates(), "96k 应是「仅独占可用」")
        self.assertIn(192000, capability.unlocked_rates(), "192k 也应仅独占可用")

    def test_dev20_hdmi_matrix(self) -> None:
        """dev20（HDMI C27HG7x）：44.1k 仅独占、48k 双通、96/192 全无。"""
        device = _find("C27HG7x")
        if device is None:
            self.skipTest("没有这台 HDMI 输出")
        self.assertFalse(dev_mod.supports_rate(device, 44100, mode=OutputMode.SHARED))
        self.assertTrue(dev_mod.supports_rate(device, 44100, mode=OutputMode.EXCLUSIVE))
        self.assertTrue(dev_mod.supports_rate(device, 48000, mode=OutputMode.SHARED))
        self.assertTrue(dev_mod.supports_rate(device, 48000, mode=OutputMode.EXCLUSIVE))
        for rate in (96000, 192000):
            self.assertFalse(dev_mod.supports_rate(device, rate, mode=OutputMode.SHARED))
            self.assertFalse(dev_mod.supports_rate(device, rate, mode=OutputMode.EXCLUSIVE))

    def test_eight_channel_device_not_false_negative(self) -> None:
        """dev19 Strix 7.1（8ch）：按实际声道探测必须通过，按 2ch 会假阴性（R8）。"""
        device = next(
            (d for d in dev_mod.list_output_devices("wasapi") if d.channels >= 8), None
        )
        if device is None:
            self.skipTest("没有 8 声道 WASAPI 设备")
        self.assertTrue(
            dev_mod.supports_rate(device, 48000, mode=OutputMode.SHARED),
            "8ch 设备共享 48k 应通过（探测必须用它自己的声道数）",
        )
        self.assertTrue(
            dev_mod.supports_rate(device, 44100, channels=8, mode=OutputMode.EXCLUSIVE),
            "8ch 设备独占 44.1k 应通过",
        )
        capability = dev_mod.probe_capabilities(device)
        self.assertEqual(capability.channels, 8)

    def test_strict_dtypes_are_integer_only(self) -> None:
        """严格模式的 dtype 白名单里不能有 float（否则 100% -9997）。"""
        # Q11 + QA 口径：严格列一律用 int32 探测/开流（16bit 源进 int32 容器）。
        # 用 int16 探会造出"支持严格"的假阳性，然后强开 int32 失败。
        self.assertEqual(tuple(dev_mod.STRICT_DTYPES), ("int32",))


class TestProbeCache(unittest.TestCase):
    """缓存命中后不得重复探测（设计 §4.2）。"""

    def test_cache_hit_skips_probe(self) -> None:
        original = dev_mod.probe_capabilities
        calls = {"n": 0}

        def fake_probe(device, **kwargs):  # noqa: ANN001 - 测试替身
            calls["n"] += 1
            return original(device, **kwargs)

        cache = dev_mod.ProbeCache()
        device = None
        try:
            device = dev_mod.pick_default_device()
        except Exception:  # noqa: BLE001
            self.skipTest("没有可用设备")
        dev_mod.probe_capabilities = fake_probe
        try:
            cache.get(device)
            cache.get(device)
            cache.get(device)
        finally:
            dev_mod.probe_capabilities = original
        self.assertEqual(calls["n"], 1, "第二次起应命中缓存，不得重复探测")
        self.assertTrue(cache.has(device))

    def test_invalidate(self) -> None:
        try:
            device = dev_mod.pick_default_device()
        except Exception:  # noqa: BLE001
            self.skipTest("没有可用设备")
        cache = dev_mod.ProbeCache()
        cache.get(device)
        self.assertTrue(cache.has(device))
        cache.invalidate(device)
        self.assertFalse(cache.has(device))


class TestFourLevelFallback(unittest.TestCase):
    """四级回退阶梯的每条分支（用注入的假探测函数，不碰声卡）。"""

    def _device(self) -> dev_mod.DeviceInfo:
        return dev_mod.DeviceInfo(
            index=0, name="fake", channels=2, default_samplerate=48000.0,
            hostapi_index=2, hostapi_name="Windows WASAPI",
        )

    def test_level1_strict(self) -> None:
        plan = dev_mod.plan_output(
            self._device(), 44100, mode="exclusive_strict",
            probe=lambda device, rate, ch, mode: mode is OutputMode.EXCLUSIVE_STRICT,
        )
        self.assertEqual((plan.rate, plan.mode, plan.level), (44100, OutputMode.EXCLUSIVE_STRICT, 1))
        self.assertFalse(plan.fallback)

    def test_level2_degrade_to_loose(self) -> None:
        plan = dev_mod.plan_output(
            self._device(), 44100, mode="exclusive_strict",
            probe=lambda device, rate, ch, mode: mode is OutputMode.EXCLUSIVE,
        )
        self.assertEqual((plan.rate, plan.mode, plan.level), (44100, OutputMode.EXCLUSIVE, 2))
        self.assertTrue(plan.mode_fallback)
        self.assertIn("严格", plan.note)

    def test_level3_exclusive_rate_fallback(self) -> None:
        plan = dev_mod.plan_output(
            self._device(), 192000, mode="exclusive",
            probe=lambda device, rate, ch, mode: (
                mode is OutputMode.EXCLUSIVE and rate == 48000
            ),
        )
        self.assertEqual((plan.rate, plan.mode), (48000, OutputMode.EXCLUSIVE))
        self.assertEqual(plan.level, 3)
        self.assertTrue(plan.rate_fallback)
        self.assertIn("192000", plan.note)

    def test_level4_shared_fallback(self) -> None:
        plan = dev_mod.plan_output(
            self._device(), 192000, mode="exclusive",
            probe=lambda device, rate, ch, mode: (
                mode is OutputMode.SHARED and rate == 48000
            ),
        )
        self.assertEqual((plan.rate, plan.mode), (48000, OutputMode.SHARED))
        self.assertEqual(plan.level, 4)
        self.assertIn("退回共享", plan.note)

    def test_no_option_raises(self) -> None:
        with self.assertRaises(dev_mod.RateUnsupportedError):
            dev_mod.plan_output(
                self._device(), 44100, mode="exclusive",
                probe=lambda *args: False,
            )

    def test_pick_rate_for_mode_wrapper(self) -> None:
        """设计 §4.2 给引擎的薄封装：返回三元组，模式随回退一起变。"""
        plan = dev_mod.plan_output(
            self._device(), 192000, mode="exclusive_strict",
            probe=lambda device, rate, ch, mode: (
                mode is OutputMode.SHARED and rate == 48000
            ),
        )
        self.assertEqual(plan.mode, OutputMode.SHARED)
        self.assertEqual(plan.requested_mode, OutputMode.EXCLUSIVE_STRICT)
        self.assertTrue(plan.fallback)


class _ExclusiveHostileOutput:
    """第一次以独占模式 prepare 就抛 -9996 的假输出（模拟 Pa_IsFormatSupported 假阳性）。

    本机真实案例：Nahimic 设备 44.1k 独占，查询说可用、开流报 ``-9996``。
    这种"探测说行、开流失败"必须**降级共享重试**，而不是把用户丢进 ERROR。
    """

    calls: list[str] = []

    def __init__(self, ring, hook) -> None:
        self.ring = ring
        self.hook = hook
        self.rate = 0
        self.channels = ring.channels
        self.started = False
        self._actual_mode = OutputMode.SHARED

    def prepare(self, rate, channels=0, *, mode=None, rate_decided=False):  # noqa: ANN001
        from seqaudio.player.output import DeviceError

        mode_value = mode if isinstance(mode, OutputMode) else OutputMode(str(mode or "shared"))
        type(self).calls.append(mode_value.value)
        if mode_value.is_exclusive:
            raise DeviceError(
                "该设备不支持独占模式（驱动或系统保留了它）[PaErrorCode -9996]"
            )
        self.rate = int(rate)
        self._actual_mode = OutputMode.SHARED
        return self.rate

    def begin(self) -> None:
        self.started = True

    def start(self, rate, channels=0) -> int:  # noqa: ANN001
        actual = self.prepare(rate, channels)
        self.begin()
        return actual

    def stop(self, abort: bool = False) -> None:
        self.started = False

    def pause(self) -> None:
        self.started = False

    def resume(self) -> None:
        self.started = True

    def actual_mode(self) -> OutputMode:
        return self._actual_mode

    @property
    def exclusive_actual(self) -> bool:
        return self._actual_mode.is_exclusive

    def snapshot(self) -> dict:
        return {
            "is_open": self.started,
            "rate": self.rate,
            "want_rate": self.rate,
            "fallback": False,
            "channels": 2,
            "dtype": str(self.ring.dtype),
            "device": "hostile",
            "device_index": -1,
            "backend": "wasapi",
            "exclusive": self._actual_mode.is_exclusive,
            "mode": self._actual_mode.value,
            "system_mix_rate": 48000,
            "exclusive_dummy": False,
            "blocksize": 1024,
            "latency_ms": 0.0,
            "underruns": 0,
            "frames_out": 0,
            "pa_flags": 0,
            "cb_errors": 0,
            "cb_error": "",
            "paused": False,
            "volume_db": 0.0,
            "error": "",
        }


class TestExclusiveOpenFailureFallback(unittest.TestCase):
    """探测假阳性 → 降级共享重试，且信号路径留痕（不静默、不 ERROR）。"""

    @classmethod
    def setUpClass(cls) -> None:
        import subprocess
        import tempfile

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            raise unittest.SkipTest("ffmpeg 不可用")
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_excl_"))
        cls.file = cls.tmp / "sine.flac"
        subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                "-i", "sine=frequency=440:duration=1.5:sample_rate=44100",
                "-ac", "2", "-c:a", "flac", str(cls.file),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_degrades_to_shared_instead_of_error(self) -> None:
        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.queue import PlayQueue, QueueItem

        _ExclusiveHostileOutput.calls = []
        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_output_mode = "exclusive"
        settings.playback_exclusive = True
        settings.playback_int_pipeline = True

        engine = PlayerEngine(
            settings,
            output_factory=lambda ring, device, hook: _ExclusiveHostileOutput(ring, hook),
        )
        try:
            item = QueueItem(
                path=self.file, title="x", duration=1.5, sample_rate=44100,
                channels=2, bit_depth=16,
            )
            engine.set_queue(PlayQueue([item]))
            self.assertTrue(engine.play(), "独占开流失败后应降级共享并成功起播")
            snapshot = engine.snapshot()
            self.assertNotEqual(snapshot["state"], "ERROR")
            self.assertFalse(snapshot["exclusive"], "实际是共享，不能报独占")
            output_stage = next(
                stage for stage in snapshot["signal_path"] if stage["key"] == "output"
            )
            self.assertEqual(output_stage["status"], "FALLBACK")
            # 阶梯顺序：独占@源率(1) → 独占@回退率(3) → 共享(4)，每级只降一次
            calls = _ExclusiveHostileOutput.calls
            self.assertGreaterEqual(len(calls), 2, f"应发生多级降级：{calls}")
            self.assertEqual(calls[0], "exclusive")
            self.assertEqual(calls[-1], "shared", f"最后应落到共享：{calls}")
            self.assertEqual(calls.count("shared"), 1, "共享只应尝试一次（同一级不原地重试）")
        finally:
            engine.shutdown()


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
class TestProbeFalsePositive(unittest.TestCase):
    """E7：探测说行、开流被拒 → 降级重试，**不进 ERROR**（设计 §4.3 硬规则 1）。"""

    def test_dev18_44100_exclusive_degrades(self) -> None:
        device = _find("Nahimic")
        if device is None:
            self.skipTest("本机没有 Nahimic 设备")
        if not dev_mod.supports_rate(device, 44100, mode=OutputMode.EXCLUSIVE):
            self.skipTest("该设备探测就不支持 44.1k 独占（不构成假阳性样本）")
        from seqaudio.core import probe as probe_mod
        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.queue import PlayQueue

        import subprocess
        import tempfile

        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            self.skipTest("ffmpeg 不可用")
        tmp = Path(tempfile.mkdtemp(prefix="seq_e7_"))
        try:
            src = tmp / "e7.flac"
            subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                 "-i", "sine=frequency=440:duration=1.5:sample_rate=44100",
                 "-ac", "2", "-c:a", "flac", str(src)],
                stdin=subprocess.DEVNULL, capture_output=True,
            )
            settings = Settings()
            settings.playback_output_backend = "wasapi"
            settings.playback_device = str(device.index)
            settings.playback_output_mode = "exclusive"
            settings.playback_int_pipeline = True
            engine = PlayerEngine(settings)
            try:
                engine.set_queue(PlayQueue.from_tracks([probe_mod.probe(src)]))
                self.assertTrue(engine.play(), "假阳性必须降级重试，不能直接 ERROR")
                engine.poll()
                snapshot = engine.snapshot()
                self.assertNotEqual(snapshot["state"], "ERROR")
                self.assertEqual(snapshot["rate"], 48000, "应降级到 48k 独占")
                self.assertTrue(snapshot["exclusive"], "降级后仍是独占（只是换了采样率）")
                output = next(
                    stage for stage in snapshot["signal_path"] if stage["key"] == "output"
                )
                self.assertEqual(output["status"], "FALLBACK", "降级必须留痕（标红）")
                self.assertIn("探测通过但开流被拒", output["detail"])
                self.assertGreaterEqual(snapshot["counters"]["probe_mismatch"], 1)
                self.assertFalse(snapshot["error"])
            finally:
                engine.shutdown()
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    def test_non_monotonic_rate_fallback(self) -> None:
        """E8：非单调 —— 独占支持 192k 但不支持 176.4k 时，回退不得靠"越高越容易"猜。"""
        device = next(
            (
                candidate
                for candidate in dev_mod.list_output_devices("wasapi")
                if dev_mod.supports_rate(candidate, 192000, mode=OutputMode.EXCLUSIVE)
                and not dev_mod.supports_rate(candidate, 176400, mode=OutputMode.EXCLUSIVE)
            ),
            None,
        )
        if device is None:
            self.skipTest("本机没有「192k 独占可用但 176.4k 不可用」的设备")
        plan = dev_mod.plan_output(device, 176400, mode="exclusive")
        self.assertNotEqual(plan.rate, 176400)
        self.assertTrue(plan.rate_fallback)
        self.assertIn("176400", plan.note)
        self.assertEqual(dev_mod.supports_rate(device, plan.rate, mode=OutputMode.EXCLUSIVE), True)


class TestSettingsMigration(unittest.TestCase):
    """v1.6 布尔独占升级到 v1.7 三值模式不能丢设置。"""

    def test_old_config_with_exclusive_true(self) -> None:
        settings = Settings.from_dict({"playback_exclusive": True})
        self.assertEqual(settings.playback_output_mode, "exclusive")

    def test_old_config_without_keys(self) -> None:
        settings = Settings.from_dict({"language": "en"})
        # Q9 拍板：默认改成「宽松独占」（44.1k 不再被系统混音到 48k），
        # 配套首次启动说明 + 一键改回共享（notice_shown 初始必须为 False）
        self.assertEqual(settings.playback_output_mode, "exclusive")
        self.assertFalse(settings.playback_exclusive_notice_shown)
        self.assertEqual(settings.playback_exclusive_fallback, "auto")
        self.assertIs(settings.playback_release_on_pause, True, "Q3 定稿：默认暂停即释放")
        self.assertIs(settings.playback_strict_require_int, True)

    def test_explicit_mode_wins(self) -> None:
        settings = Settings.from_dict(
            {"playback_exclusive": True, "playback_output_mode": "exclusive_strict"}
        )
        self.assertEqual(settings.playback_output_mode, "exclusive_strict")


if __name__ == "__main__":
    unittest.main(verbosity=2)
