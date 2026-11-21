"""v1.7 独占输出验收用例（T01–T06 + 冒烟 E1–E6 + 独立验证）。

运行：``python tests/test_player_exclusive.py``（会真开流，测前请把系统音量调低；
没有 WASAPI 设备的机器会自动 skip 设备相关用例）。

写用例时的四条纪律（都是踩过才知道的）
--------------------------------------
1. **不写死设备索引**：dev18/19/20/21/22 是这台机器的 PortAudio 编号，一律按
   名字子串匹配，找不到就 skip。
2. **设备能力会变**：实测同一台 7.1 耳机在「Windows 扬声器配置」变化后，2ch 与
   8ch 的可用性会互换（``check_output_settings`` 与真实开流同步变化）。所以
   断言的是**不变式**（"探测说能用 → 真开流也能用"），不是某台设备的固定矩阵。
3. **探测不是结论**：本机撞到过 ``check_output_settings`` 假阳性（dev18 的
   44.1k 独占：查询通过、开流 -9996），所以"探测说行、开流失败"必须有兜底。
4. **兜底有两级**：① 保住独占、换采样率重试；② 独占彻底不行才退回共享。
   两条是不同行为，必须分开断言。
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.player.constants import (  # noqa: E402
    OutputMode,
    PlayerState,
    StageStatus,
    normalize_output_mode,
)

FFMPEG = find_ffmpeg()
PACKAGED_EXE = ROOT / "build" / "portable" / "dist" / "音序管家" / "音序管家.exe"
SKIP_MANUAL = "需人工造场景（独占被其它程序占用），判定步骤见 docs/1.7-测试报告.md"


def find_wasapi_device(name_substring: str):
    for device in dev_mod.list_output_devices("wasapi"):
        if name_substring in device.name:
            return device
    return None


def has_device() -> bool:
    try:
        dev_mod.pick_default_device()
        return True
    except Exception:  # noqa: BLE001
        return False


HAS_DEVICE = has_device()


def synth_flac(target: Path, seconds: float = 8.0, rate: int = 44100,
               channels: int = 2) -> Path:
    """合成正弦 FLAC。

    ``seconds`` 默认 8 s：打包冒烟要播 3 s，素材必须显著长于播放窗口，否则
    播到文件尾会欠载一次（那是「素材太短」，不是产品缺陷）。
    """
    assert FFMPEG is not None
    target.parent.mkdir(parents=True, exist_ok=True)
    if channels == 2:
        args = ["-f", "lavfi", "-i",
                f"sine=frequency=1000:duration={seconds}:sample_rate={rate}", "-ac", "2"]
    else:
        per_channel = "|".join(
            "0.5*sin(2*PI*1000*t)" if index == 2 else "0" for index in range(channels)
        )
        args = ["-f", "lavfi", "-i", f"aevalsrc={per_channel}:d={seconds}:s={rate}"]
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args,
         "-c:a", "flac", str(target)],
        stdin=subprocess.DEVNULL, capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise unittest.SkipTest(f"合成样本失败：{proc.stderr.decode('utf-8', 'replace')[:150]}")
    return target


def make_item(path: Path, *, duration: float = 8.0, rate: int = 44100,
              channels: int = 2, bit_depth: int = 16, title: str = "t"):
    from seqaudio.player.queue import QueueItem

    return QueueItem(path=path, title=title, duration=duration,
                     sample_rate=rate, channels=channels, bit_depth=bit_depth)


def play_once(settings: Settings, item, *, wait: float = 0.8) -> dict:
    from seqaudio.player.engine import PlayerEngine
    from seqaudio.player.queue import PlayQueue

    engine = PlayerEngine(settings)
    try:
        engine.set_queue(PlayQueue([item]))
        engine.play()
        time.sleep(wait)
        engine.poll()
        return engine.snapshot()
    finally:
        engine.shutdown()


def refuse_exclusive_prepare(real_prepare, error_factory):
    """包一层 ``AudioOutput.prepare``：独占必失败、共享照常。"""

    def wrapper(self, rate, channels=0, **kwargs):
        mode = normalize_output_mode(kwargs.get("mode") or OutputMode.SHARED)
        if mode.is_exclusive:
            raise error_factory()
        return real_prepare(self, rate, channels, **kwargs)

    return wrapper


# ---------------------------------------------------------------- T01 输出层


class TestOutputModeConfig(unittest.TestCase):
    """T01：三参数矩阵、模式别名、实际值上报、严格+浮点前置拦截、错误码分岔。"""

    def test_first_run_defaults_to_shared(self) -> None:
        """首次启动默认共享：不让用户在还不懂独占代价时就被推上独占。

        独占会绕开 Windows 混音器 —— 系统音量失效、其它程序没声音。
        默认值必须是 shared，用户主动选了才用独占（v1.8.0 需求 4）。
        """
        settings = Settings()
        self.assertEqual(settings.playback_output_mode, "shared")
        self.assertFalse(settings.playback_exclusive)

    def test_exclusive_notice_skipped_offscreen(self) -> None:
        """离屏环境不得弹独占提醒（否则自动化用例会被模态框卡死）。"""
        import os
        from types import SimpleNamespace

        from seqaudio.ui.main_window import MainWindow

        if os.environ.get("QT_QPA_PLATFORM", "").lower() != "offscreen":
            self.skipTest("仅在离屏环境下验证跳过逻辑")
        fake = SimpleNamespace(settings=Settings())
        fake.settings.playback_output_mode = "exclusive"
        MainWindow._maybe_announce_exclusive(fake, force=True)
        self.assertFalse(
            fake.settings.playback_exclusive_notice_shown,
            "离屏时应在进入弹窗前返回，不写已读标记",
        )

    def test_output_config_mode_fields(self) -> None:
        from seqaudio.player.output import OutputConfig

        config = OutputConfig(backend="wasapi", device="", samplerate=44100,
                             channels=2, dtype="int32", blocksize=1024, latency=None)
        self.assertIs(config.mode, OutputMode.SHARED)
        self.assertFalse(config.rate_decided)
        self.assertFalse(config.explicit_format)
        self.assertFalse(config.exclusive)

        legacy = OutputConfig(exclusive=True)
        self.assertIs(legacy.mode, OutputMode.EXCLUSIVE, "旧布尔字段应镜像成宽松独占")
        self.assertTrue(legacy.exclusive)

        strict = OutputConfig(mode="exclusive_strict")
        self.assertIs(strict.mode, OutputMode.EXCLUSIVE_STRICT)
        self.assertTrue(strict.explicit_format, "严格模式必须置 explicit_format")
        self.assertTrue(strict.exclusive)

    def test_normalize_aliases(self) -> None:
        self.assertIs(normalize_output_mode("strict"), OutputMode.EXCLUSIVE_STRICT)
        self.assertIs(normalize_output_mode("loose"), OutputMode.EXCLUSIVE)
        self.assertIs(normalize_output_mode("shared"), OutputMode.SHARED)
        self.assertIs(normalize_output_mode(True), OutputMode.EXCLUSIVE)
        self.assertIs(normalize_output_mode(False), OutputMode.SHARED)
        self.assertIs(normalize_output_mode("拼错了"), OutputMode.SHARED,
                      "认不出的值必须回默认，不许抛给界面")

    @unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
    def test_extra_settings_per_mode(self) -> None:
        """三档的 extra_settings 用**行为**验证（``WasapiSettings`` 不暴露字段）。

        * shared → None（不带 extra_settings）
        * 宽松 → 有对象，且 float32 能过前置校验
        * 严格 → 有对象，且 float32 被前置校验拦下（证明 explicit 生效）
        * 非 WASAPI 设备 → None
        """
        import sounddevice as sd

        from seqaudio.player.output import AudioOutput
        from seqaudio.player.ringbuffer import PcmRingBuffer

        device = find_wasapi_device("Realtek") or dev_mod.pick_default_device()
        if device.backend_key != "wasapi":
            self.skipTest("没有 WASAPI 设备，无法验证 extra_settings")

        self.assertIsNone(dev_mod.extra_settings_for(OutputMode.SHARED, device))
        self.assertIsInstance(dev_mod.extra_settings_for(OutputMode.EXCLUSIVE, device),
                              sd.WasapiSettings)
        self.assertIsInstance(dev_mod.extra_settings_for("strict", device),
                              sd.WasapiSettings)

        def guard(mode: str) -> str:
            ring = PcmRingBuffer.from_seconds(1.0, 44100, 2, "float32")
            output = AudioOutput(ring, device="", backend="wasapi", blocksize=1024)
            try:
                with mock.patch("seqaudio.player.output.sounddevice"):
                    output.prepare(44100, 2, mode=mode)
            except dev_mod.DeviceError as exc:
                return str(exc)
            finally:
                output.stop()
            return ""

        # 这条断言的本意是「前置校验允许 float32」，但 guard() 走的是真实
        # prepare → 会受当前默认设备能力影响（Windows 默认设备换成不支持
        # 44.1k 独占的 8ch 设备时会被设备侧拒绝）。设备侧拒绝与"格式被拦"
        # 是两回事：前者只说明这台机器此刻开不了流，应该 skip 而不是判回归。
        message = guard("exclusive")
        if "候选" in message or "打不开" in message or "采样率" in message:
            self.skipTest(f"当前默认设备不支持独占开流，无法验证前置校验：{message}")
        self.assertEqual(message, "", "宽松独占必须允许 float32")
        self.assertIn("整数", guard("exclusive_strict"),
                      "严格独占必须拦下 float32 并说明要整数")

        mme = next(iter(dev_mod.list_output_devices("mme")), None)
        if mme is not None:
            self.assertIsNone(dev_mod.extra_settings_for(OutputMode.EXCLUSIVE, mme),
                              "非 WASAPI 设备没有独占概念，必须返回 None")

    def test_strict_float32_blocked_before_open(self) -> None:
        """独立验证 ③：严格 + f32 在开流前拦下，且**没有真的去开流**。"""
        from seqaudio.player.output import AudioOutput
        from seqaudio.player.ringbuffer import PcmRingBuffer

        ring = PcmRingBuffer.from_seconds(1.0, 44100, 2, "float32")
        output = AudioOutput(ring, device="", backend="wasapi", blocksize=1024)
        with mock.patch("seqaudio.player.output.sounddevice") as fake_sd:
            with self.assertRaises(dev_mod.DeviceError) as ctx:
                output.prepare(44100, 2, mode="exclusive_strict")
        self.assertNotIsInstance(ctx.exception, dev_mod.RateUnsupportedError,
                                 "这是格式问题不是采样率问题，类型不能报成那个")
        message = str(ctx.exception)
        self.assertIn("整数", message)
        self.assertNotIn("9997", message, "不许把 PortAudio 原始错误码抛给用户")
        fake_sd.return_value.OutputStream.assert_not_called()

    def test_error_mapping_types_and_keywords(self) -> None:
        """§4.4 五种歧义：类型 + 文案关键词（表驱动）。"""
        from seqaudio.player.output import map_portaudio_error

        cases = [
            ("-9997 共享", "-9997", "shared", "int32",
             dev_mod.RateUnsupportedError, ("采样率",)),
            ("-9997 严格+浮点", "-9997", "exclusive_strict", "float32",
             dev_mod.RateUnsupportedError, ("整数",)),
            ("-9997 严格+整数", "-9997", "exclusive_strict", "int32",
             dev_mod.RateUnsupportedError, ("独占", "44100")),
            ("-9996 独占", "-9996", "exclusive", "int32",
             dev_mod.DeviceError, ("独占",)),
            ("-9985 独占", "-9985", "exclusive", "int32",
             dev_mod.DeviceBusyError, ("独占",)),
        ]
        raw_text = {
            "-9997": "Invalid sample rate",
            "-9996": "Invalid device",
            "-9985": "Device unavailable",
        }
        for label, code, mode, dtype, expected_type, keywords in cases:
            with self.subTest(case=label):
                mapped = map_portaudio_error(
                    Exception(f"{raw_text[code]} [PaErrorCode {code}]"),
                    mode=mode, dtype=dtype, rate=44100,
                )
                self.assertIsInstance(mapped, expected_type, f"{label}: 类型不对")
                if code != "-9996":
                    self.assertNotIsInstance(mapped, dev_mod.DeviceLostError,
                                             f"{label}: 不是拔线，不能报成设备丢失")
                message = str(mapped)
                for keyword in keywords:
                    self.assertIn(keyword, message, f"{label}: 文案缺少「{keyword}」")
                self.assertNotIn("可能已被拔出", message, f"{label}: 拔线文案串味了")


# ---------------------------------------------------------------- T02 探测与回退


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
class TestDeviceProbe(unittest.TestCase):
    """T02：模式感知探测、实际声道、缓存、四级回退、探测一次。"""

    def test_dev21_unlocks_high_rates_exclusive(self) -> None:
        """dev21：共享 ✗ → 独占 ✓（96k/192k）。找不到即 skip。"""
        device = find_wasapi_device("Realtek(R) Audio")
        if device is None:
            self.skipTest("本机没有「Realtek(R) Audio」WASAPI 设备")
        cap = dev_mod.probe_capabilities(device, channels=2)
        if cap.supports(96000, OutputMode.SHARED):
            self.skipTest("该设备当前共享模式已支持 96k，构不成对照")
        self.assertTrue(cap.supports(96000, OutputMode.EXCLUSIVE),
                        "独占必须解锁 96k —— 本版最硬的卖点")
        self.assertTrue(cap.supports(192000, OutputMode.EXCLUSIVE))
        self.assertIn(96000, cap.unlocked_rates())

    def test_probe_agrees_with_real_open(self) -> None:
        """**不变式**：探测说能用 → 真开流也必须能用。

        这台机器的 7.1 设备会随「Windows 扬声器配置」改变可用声道数，
        所以只断言"探测与真实开流一致"，不写死某档结论。
        """
        import sounddevice as sd

        device = find_wasapi_device("Strix 7.1") or find_wasapi_device("Realtek(R) Audio")
        if device is None:
            self.skipTest("没有可用于比对的 WASAPI 设备")
        for channels in (2, device.channels):
            probed = dev_mod.supports_rate(device, 48000, channels, mode="shared")
            try:
                stream = sd.OutputStream(device=device.index, samplerate=48000,
                                         channels=channels, dtype="float32",
                                         blocksize=1024)
                stream.start()
                stream.stop()
                stream.close()
                opened = True
            except Exception:  # noqa: BLE001
                opened = False
            with self.subTest(channels=channels):
                self.assertEqual(probed, opened,
                                 f"{channels}ch：探测={probed} 真实开流={opened}")

    def test_channels_zero_uses_device_max(self) -> None:
        device = find_wasapi_device("Strix 7.1") or dev_mod.pick_default_device()
        auto = dev_mod.supports_rate(device, 48000)
        explicit = dev_mod.supports_rate(device, 48000, channels=device.channels)
        self.assertEqual(auto, explicit,
                         "channels=0 的结论必须与「设备最大声道」一致")

    def test_probe_cache_avoids_repeated_queries(self) -> None:
        device = find_wasapi_device("Realtek(R) Audio") or dev_mod.pick_default_device()
        cache = dev_mod.ProbeCache()
        real_probe = dev_mod.probe_capabilities
        calls = {"n": 0}

        def counting_probe(*args, **kwargs):
            calls["n"] += 1
            return real_probe(*args, **kwargs)

        with mock.patch.object(dev_mod, "probe_capabilities", counting_probe):
            cap = cache.get(device, 2)
            first = calls["n"]
            again = cache.get(device, 2)
        self.assertEqual(first, 1, "首次探测应查询一次")
        self.assertEqual(calls["n"], 1, "第二次应命中缓存，零查询")
        self.assertEqual(cap.to_dict()["rates"], again.to_dict()["rates"])

    def test_four_level_fallback(self) -> None:
        """四级回退：注入探测函数覆盖 ①②③④，每级都有说明文字。"""
        device = dev_mod.pick_default_device()

        def probe_with(allow: dict[str, set[int]]):
            def probe(dev, rate, channels, mode):
                return rate in allow.get(normalize_output_mode(mode).value, set())
            return probe

        plan = dev_mod.plan_output(device, 44100, channels=2, mode="exclusive_strict",
                                   probe=probe_with({"exclusive_strict": {44100}}))
        self.assertIs(plan.mode, OutputMode.EXCLUSIVE_STRICT)
        self.assertEqual(plan.level, 1)
        self.assertFalse(plan.fallback)

        plan = dev_mod.plan_output(device, 44100, channels=2, mode="exclusive_strict",
                                   probe=probe_with({"exclusive": {44100}}))
        self.assertIs(plan.mode, OutputMode.EXCLUSIVE)
        self.assertEqual(plan.level, 2)
        self.assertIn("严格", plan.note)

        plan = dev_mod.plan_output(device, 96000, channels=2, mode="exclusive",
                                   probe=probe_with({"exclusive": {48000}}))
        self.assertEqual(plan.rate, 48000)
        self.assertEqual(plan.level, 3)
        self.assertTrue(plan.fallback)
        self.assertIn("48000", plan.note)

        plan = dev_mod.plan_output(device, 44100, channels=2, mode="exclusive",
                                   probe=probe_with({"shared": {44100}}))
        self.assertIs(plan.mode, OutputMode.SHARED)
        self.assertEqual(plan.level, 4)
        self.assertIn("退回共享", plan.note)

    def test_rate_decided_skips_probe(self) -> None:
        """「探测一次」：``rate_decided=True`` 时 prepare 不再探测。"""
        from seqaudio.player.output import AudioOutput
        from seqaudio.player.ringbuffer import PcmRingBuffer

        ring = PcmRingBuffer.from_seconds(1.0, 44100, 2, "float32")
        output = AudioOutput(ring, device="", backend="mme", blocksize=1024)
        with mock.patch.object(dev_mod, "pick_rate") as fake_pick:
            try:
                output.prepare(44100, 2, mode="shared", rate_decided=True)
            except dev_mod.DeviceError:
                pass
            fake_pick.assert_not_called()
        output.stop()

    def test_channel_mismatch_error_mentions_channels(self) -> None:
        """失败原因必须归因到**声道**，而不是笼统说"采样率不支持"。

        构造：设备对 6ch 全失败、对 2ch 全成功（本机 7.1 设备在某些扬声器
        配置下正是如此）。这时 `plan_output(channels=6)` 若只报"不支持候选
        采样率"，用户会去改采样率 —— 永远改不好。
        """
        device = dev_mod.pick_default_device()
        probe = lambda dev, rate, channels, mode: int(channels) == 2  # noqa: E731
        with self.assertRaises(dev_mod.RateUnsupportedError) as ctx:
            dev_mod.plan_output(device, 48000, channels=6, mode="shared", probe=probe)
        message = str(ctx.exception)
        self.assertTrue("声道" in message,
                        f"6ch 失败却只提采样率，用户会改错地方：{message}")


# ---------------------------------------------------------------- T03 信号路径


class TestSignalPathWording(unittest.TestCase):
    """T03：output/device 级渲染、L0/L1/L2 分级、措辞铁律反向用例。"""

    def _configure(self, **kwargs):
        from seqaudio.player.signal_path import SignalPath

        base = dict(
            source="FLAC · 16bit/44.1kHz · 2ch", codec="flac",
            decoder_version="ffmpeg", sample_fmt="s24", channels=2,
            sample_rate=44100, int_pipeline=True, ramp_ms=5, gapless=True,
            rate_want=44100, rate_actual=44100, backend="wasapi",
            device="扬声器", latency_ms=40.0, device_dtype="int32",
        )
        base.update(kwargs)
        return SignalPath.blank().configure(**base)

    def test_output_stage_six_scenarios(self) -> None:
        strict_ok = self._configure(exclusive_actual=True, mode_label="strict",
                                   system_mix_rate=48000)
        self.assertIn("严格", strict_ok.stage("output").detail)
        self.assertIs(strict_ok.stage("output").status, StageStatus.PASSTHROUGH)

        loose_ok = self._configure(exclusive_actual=True, mode_label="loose",
                                  system_mix_rate=48000)
        self.assertIn("独占", loose_ok.stage("output").detail)
        self.assertNotIn("严格", loose_ok.stage("output").detail)

        degraded = self._configure(exclusive_actual=False, mode_label="shared",
                                  mode_note="设备不支持独占，已退回共享")
        self.assertIn("共享", degraded.stage("output").detail)
        self.assertIs(degraded.stage("output").status, StageStatus.FALLBACK)

        refused = self._configure(exclusive_actual=False, mode_label="shared",
                                 exclusive_refused=True, mode_note="该设备不支持独占")
        self.assertIn("不支持独占", refused.stage("output").detail)
        self.assertIs(refused.stage("output").status, StageStatus.FALLBACK)

    def test_device_stage_shows_system_mix_rate(self) -> None:
        path = self._configure(exclusive_actual=True, mode_label="loose",
                              system_mix_rate=48000)
        detail = path.stage("device").detail
        self.assertIn("48000", detail)
        self.assertTrue("绕开" in detail or "混音" in detail, f"实际：{detail}")

    def test_wording_level(self) -> None:
        l2 = self._configure(exclusive_actual=True, mode_label="strict",
                            system_mix_rate=48000, int_pipeline=True,
                            gain_active=False, eq_enabled=False, eq_active=False)
        self.assertEqual(l2.wording_level(), "L2")

        l1 = self._configure(exclusive_actual=True, mode_label="loose",
                            system_mix_rate=48000, int_pipeline=False,
                            sample_fmt="f32", device_dtype="float32")
        self.assertEqual(l1.wording_level(), "L1")

        l0 = self._configure(exclusive_actual=False, mode_label="shared",
                            system_mix_rate=48000, int_pipeline=False,
                            sample_fmt="f32")
        self.assertEqual(l0.wording_level(), "L0")

        gained = self._configure(exclusive_actual=True, mode_label="strict",
                                system_mix_rate=48000, int_pipeline=True,
                                gain_active=True, gain_db=-6.0)
        self.assertEqual(gained.wording_level(), "L1",
                         "有软件增益就不能再声称 L2（L2 要求 DSP 全旁路）")

    def test_forbidden_claims_downgrade(self) -> None:
        from seqaudio.player.signal_path import TEXT_NO_SOFTWARE_CHANGE, assert_wording

        for banned in ("位完美已验证", "24bit 直通 DAC", "逐样本一致", "bit-perfect",
                       "bit perfect", "比特完美"):
            with self.subTest(text=banned):
                result = assert_wording(banned)
                self.assertNotIn(banned, result, f"{banned!r} 未被降级")
                self.assertEqual(result, TEXT_NO_SOFTWARE_CHANGE)

        self.assertEqual(assert_wording("未做软件改动"), "未做软件改动")

    def test_wording_never_returns_l3(self) -> None:
        from seqaudio.player.signal_path import SignalPath

        for path in (SignalPath.blank(), self._configure()):
            self.assertIn(path.wording_level(), ("L0", "L1", "L2"))


# ---------------------------------------------------------------- T04 设置面板


class TestSettingsPanelExclusive(unittest.TestCase):
    """T04：三值模式、严格联动、非 WASAPI 置灰、能力矩阵、往返同步、i18n。

    每个用例都**新建面板**：面板有状态（模式会联动整数通路），共享实例会让
    用例相互污染。
    """

    @classmethod
    def setUpClass(cls) -> None:
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from seqaudio.app import create_app

        cls.app = create_app([])

    def setUp(self) -> None:
        from seqaudio.ui.settings_panel import SettingsPanel

        self.settings = Settings()
        self.panel = SettingsPanel(self.settings)

    def _select_wasapi(self) -> None:
        index = self.panel.backend_combo.findData("wasapi")
        if index < 0:
            self.skipTest("后端下拉里没有 wasapi")
        self.panel.backend_combo.setCurrentIndex(index)
        self.app.processEvents()

    def _select_device(self, name_substring: str) -> None:
        """把「输出设备」下拉切到名字含该子串的设备（数据 = 设备名）。

        生产环境由 `PlayerBridge` 调 ``set_output_devices()`` 填这个下拉；
        离屏用例里没有桥接，得自己喂一遍，否则下拉里只有「系统默认设备」。
        """
        if self.panel.device_combo.count() <= 1:
            self.panel.set_output_devices(
                [device.to_dict() for device in dev_mod.list_output_devices(
                    self.panel._current_backend_key())]
            )
            self.app.processEvents()
        for row in range(self.panel.device_combo.count()):
            text = self.panel.device_combo.itemText(row)
            data = str(self.panel.device_combo.itemData(row) or "")
            if name_substring in text or name_substring in data:
                self.panel.device_combo.setCurrentIndex(row)
                self.app.processEvents()
                return
        self.skipTest(f"设备下拉里没有「{name_substring}」")

    def test_three_way_mode_group(self) -> None:
        for name in ("mode_shared", "mode_exclusive", "mode_strict"):
            self.assertTrue(hasattr(self.panel, name), f"缺少控件 {name}")
        self.assertIn("整数", self.panel.mode_strict.toolTip())

    def test_strict_locks_int_pipeline(self) -> None:
        self._select_wasapi()
        device = find_wasapi_device("Realtek(R) Audio")
        if device is None or not dev_mod.supports_rate(
            device, 44100, 2, mode="exclusive_strict"
        ):
            self.skipTest("本机没有支持严格独占的设备")
        self._select_device("Realtek(R) Audio")
        self.panel.mode_strict.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.panel.int_pipeline_check.isChecked(),
                        "严格模式必须自动勾上整数通路")
        self.assertFalse(self.panel.int_pipeline_check.isEnabled(),
                         "严格模式下整数通路应锁定")

        self.panel.mode_exclusive.setChecked(True)
        self.app.processEvents()
        self.assertTrue(self.panel.int_pipeline_check.isEnabled(),
                        "退回宽松后整数通路应恢复可编辑")

    def test_non_wasapi_backend_disables_exclusive(self) -> None:
        self._select_wasapi()
        self.assertTrue(self.panel.mode_exclusive.isEnabled())
        index = self.panel.backend_combo.findData("mme")
        if index < 0:
            self.skipTest("后端下拉里没有 mme")
        self.panel.backend_combo.setCurrentIndex(index)
        self.app.processEvents()
        self.assertFalse(self.panel.mode_exclusive.isEnabled(),
                         "非 WASAPI 后端时「独占」必须置灰")
        self.assertFalse(self.panel.mode_strict.isEnabled())

    def test_capability_table_matches_probe(self) -> None:
        if not HAS_DEVICE:
            self.skipTest("没有可用输出设备")
        self._select_wasapi()
        self.panel.mode_exclusive.setChecked(True)
        self.app.processEvents()
        self.panel._refresh_capability()
        self.app.processEvents()
        table = self.panel.capability_table
        self.assertGreater(table.rowCount(), 0, "能力矩阵没有渲染任何行")
        self.assertEqual(table.columnCount(), 4, "表头应是「采样率 / 共享 / 独占 / 严格」")

        device = find_wasapi_device("Realtek(R) Audio")
        if device is None:
            self.skipTest("没有可比对的设备")
        cap = dev_mod.probe_capabilities(device, channels=2)
        rows = {}
        for row in range(table.rowCount()):
            rows[table.item(row, 0).text()] = [
                table.item(row, col).text() for col in (1, 2, 3)
            ]
        if "96 kHz" in rows:
            expected = "✓" if cap.supports(96000, "exclusive") else "✗"
            self.assertEqual(rows["96 kHz"][1], expected,
                             "能力矩阵与探测结论不一致")

    def test_strict_downgrades_when_device_lacks_it(self) -> None:
        """本机 5 台 WASAPI 里只有 Realtek 扬声器支持严格独占：选到别的设备时，
        面板必须自动降级为「独占」并说明，而不是让用户起播才失败。"""
        self._select_wasapi()
        candidates = [
            device for device in dev_mod.list_output_devices("wasapi")
            if not dev_mod.probe_capabilities(device, channels=2).strict_usable
        ]
        if not candidates:
            self.skipTest("本机所有 WASAPI 设备都支持严格独占，构不成对照")
        target = candidates[0]
        self._select_device(target.name)
        self.panel.mode_strict.setChecked(True)
        self.app.processEvents()
        self.assertIs(self.panel._current_output_mode(), OutputMode.EXCLUSIVE,
                      "设备不支持严格时必须自动降级为宽松独占")
        # 降级提示落在 capability_hint，但它随后可能被「本设备实测：…」摘要覆盖，
        # 这条只断言行为（模式不能停在严格），文案问题记在测试报告的观察项里。

    def test_roundtrip_and_legacy_mirror(self) -> None:
        settings = Settings()
        settings.playback_output_mode = "exclusive_strict"
        self.panel.load_from(settings)
        self.app.processEvents()
        self.assertTrue(self.panel.mode_strict.isChecked(),
                        "load_from 应把三值模式还原到面板")

        self.panel.mode_shared.setChecked(True)
        self.app.processEvents()
        self.panel.apply_to(settings)
        self.assertEqual(settings.playback_output_mode, "shared")
        self.assertFalse(settings.playback_exclusive, "旧布尔字段应镜像回 False")

        settings.playback_output_mode = "exclusive"
        self.panel.load_from(settings)
        self.app.processEvents()
        self.panel.apply_to(settings)
        self.assertTrue(settings.playback_exclusive,
                        "选宽松独占时旧布尔字段应镜像为 True")

    def test_i18n_keys_present(self) -> None:
        from seqaudio.ui import i18n

        original = i18n.current()
        try:
            for key in ("dev_mode_shared", "dev_mode_exclusive",
                        "dev_mode_exclusive_strict", "dev_hint_asio_missing"):
                for locale in i18n.locale_names():
                    i18n.set_locale(locale)
                    self.assertTrue(i18n.tr(key), f"{locale} 缺 {key}")
        finally:
            i18n.set_locale(original)


# ---------------------------------------------------------------- 独立验证


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
class TestIndependentVerification(unittest.TestCase):
    """team-lead 指定的独立验证（自己出证据，不依赖工程师的测试）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq17_"))
        cls.source = synth_flac(cls.tmp / "44k.flac", seconds=8.0, rate=44100)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_1_probe_false_positive_keeps_exclusive_by_changing_rate(self) -> None:
        """① 探测假阳性 → 保住独占、换率重试（本机真实发生的一级兜底）。

        dev18 的 44.1k 独占：``check_output_settings`` 说可用、开流报 -9996。
        引擎不应进 ERROR，也不应急着退回共享，而是**在同一独占模式下换 48000**。
        """
        device = find_wasapi_device("Nahimic")
        if device is None:
            self.skipTest("本机没有 Nahimic 设备（该假阳性只在这台机器上观测到）")
        if not dev_mod.supports_rate(device, 44100, 2, mode="exclusive"):
            self.skipTest("当前该设备 44.1k 独占探测就不通过，走的是另一条分支")

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_ramp_ms = 2

        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.queue import PlayQueue

        engine = PlayerEngine(settings)
        try:
            engine.set_queue(PlayQueue([make_item(self.source, duration=8.0)]))
            started = engine.play()
            time.sleep(0.8)
            engine.poll()
            snapshot = engine.snapshot()

            self.assertTrue(started, f"起播失败：{snapshot['error']}")
            self.assertIs(engine.state, PlayerState.PLAYING, "不得进 ERROR")
            self.assertGreater(int(snapshot["output"]["frames_out"]), 0, "没有出声")
            self.assertTrue(snapshot["exclusive"], "这一级兜底应保住独占")
            self.assertEqual(snapshot["output_mode"], "exclusive")
            self.assertIn(snapshot["rate"], (44100, 48000))
            self.assertTrue(snapshot["mode_note"] or snapshot["fallback"],
                            "换率重试必须留痕（mode_note / fallback）")

            output_stage = next(s for s in snapshot["signal_path"] if s["key"] == "output")
            self.assertIs(StageStatus(output_stage["status"]), StageStatus.FALLBACK,
                          "开流被拒后换率成功，output 级必须是 FALLBACK")
            self.assertEqual(engine._spec.sample_rate, snapshot["rate"],
                             "解码率与开流率不一致 = 静默变调")
            self.assertEqual(snapshot["counters"]["underruns"], 0)
        finally:
            engine.shutdown()

    def test_1b_second_start_skips_failed_rate(self) -> None:
        """① 后半：第二次起播直接规划可用率，不再先试被拒的 44.1k。"""
        device = find_wasapi_device("Nahimic")
        if device is None:
            self.skipTest("本机没有 Nahimic 设备")

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_ramp_ms = 2

        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.queue import PlayQueue

        engine = PlayerEngine(settings)
        engine.set_queue(PlayQueue([make_item(self.source, duration=8.0)]))
        try:
            engine.play()
            time.sleep(0.5)
            first_rate = engine.snapshot()["rate"]
            engine.stop()

            planned: list[tuple[int, int]] = []
            real_plan = dev_mod.plan_output

            def spy(device_arg, want, *, channels=0, mode=OutputMode.SHARED, **kwargs):
                plan = real_plan(device_arg, want, channels=channels, mode=mode, **kwargs)
                planned.append((int(want), int(plan.rate)))
                return plan

            with mock.patch.object(dev_mod, "plan_output", spy):
                self.assertTrue(engine.play(), "第二次起播应成功")
                time.sleep(0.4)
            self.assertTrue(planned, "没有观察到规划调用")
            self.assertEqual(planned[0][1], first_rate,
                             f"第二次应直接规划 {first_rate}，实际 {planned}")
        finally:
            engine.shutdown()

    def test_1c_exclusive_impossible_degrades_to_shared(self) -> None:
        """① 第二级兜底：独占彻底打不开 → 退回共享出声，且留痕。

        用 mock 让**任何**独占开流都失败（设备侧真实场景：独占被系统保留）。
        """
        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.output import AudioOutput
        from seqaudio.player.queue import PlayQueue

        device = find_wasapi_device("Realtek(R) Audio") or dev_mod.pick_default_device()
        real_prepare = AudioOutput.prepare
        attempts: list[tuple[int, str]] = []

        def patched(self, rate, channels=0, **kwargs):
            mode = normalize_output_mode(kwargs.get("mode") or OutputMode.SHARED)
            attempts.append((int(rate), mode.value))
            if mode.is_exclusive:
                raise dev_mod.DeviceError("该设备不支持独占模式")
            return real_prepare(self, rate, channels, **kwargs)

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_exclusive_fallback = "auto"
        settings.playback_ramp_ms = 2

        engine = PlayerEngine(settings)
        try:
            engine.set_queue(PlayQueue([make_item(self.source, duration=8.0)]))
            with mock.patch.object(AudioOutput, "prepare", patched):
                started = engine.play()
                time.sleep(0.8)
                engine.poll()
                snapshot = engine.snapshot()
            attempts_first = list(attempts)
            attempts.clear()
            with mock.patch.object(AudioOutput, "prepare", patched):
                self.assertTrue(engine.stop() is not False)
                self.assertTrue(engine.play(), "第二次起播应成功")
                time.sleep(0.3)
            attempts_second = list(attempts)

            self.assertTrue(started, f"降级共享后仍应出声：{snapshot['error']}")
            self.assertIs(engine.state, PlayerState.PLAYING, "不该进 ERROR")
            self.assertFalse(snapshot["exclusive"], "没拿到独占就不能报 True")
            self.assertEqual(snapshot["output_mode"], "shared")
            self.assertEqual(snapshot["output_mode_requested"], "exclusive")
            self.assertGreater(int(snapshot["output"]["frames_out"]), 0, "没有出声")

            output_stage = next(s for s in snapshot["signal_path"] if s["key"] == "output")
            self.assertIs(StageStatus(output_stage["status"]), StageStatus.FALLBACK)

            # 第一次起播试过独占（≥1 次），第二次必须直接一步到位
            self.assertGreater(len(attempts_first), 1,
                               f"第一次应逐级降级，实际只试了 {attempts_first}")
            self.assertEqual(len(attempts_second), 1,
                             f"第二次不该重复试错，实际 {attempts_second}")
            self.assertEqual(attempts_second[0][1], "shared")
        finally:
            engine.shutdown()

    def test_7_skip_set_keyed_by_rate_and_mode(self) -> None:
        """⑧ **skip 集必须按 (设备, 请求率, 请求模式) 三维记键**。

        场景：dev18 的 44.1k 独占开流失败（探测假阳性），降级到 48000 独占成功。
        紧接着在同一设备播 **48k** 文件 —— 48k 独占本来是好的，必须走真独占、
        信号路径不得出现 FALLBACK。

        若 skip 集只按设备索引记（缺陷），第二次会把 level 1 一并跳过，
        于是 48k 也被降级：`exclusive=False` + output 级 FALLBACK。
        """
        device = find_wasapi_device("Nahimic")
        if device is None:
            self.skipTest("本机没有 Nahimic 设备（该假阳性只在这台机器上观测到）")
        if not dev_mod.supports_rate(device, 44100, 2, mode="exclusive"):
            self.skipTest("当前该设备 44.1k 独占探测就不通过，构不成假阳性场景")
        if not dev_mod.supports_rate(device, 48000, 2, mode="exclusive"):
            self.skipTest("该设备 48k 独占不可用，无法验证「不该被污染」")

        source44 = synth_flac(self.tmp / "skip44.flac", seconds=6.0, rate=44100)
        source48 = synth_flac(self.tmp / "skip48.flac", seconds=6.0, rate=48000)

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_ramp_ms = 2

        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.queue import PlayQueue

        engine = PlayerEngine(settings)
        try:
            engine.set_queue(PlayQueue([make_item(source44, duration=6.0)]))
            self.assertTrue(engine.play(), "44.1k 起播失败")
            time.sleep(0.6)
            first = engine.snapshot()
            self.assertIs(engine.state, PlayerState.PLAYING)

            engine.stop()
            time.sleep(0.2)
            engine.play(make_item(source48, duration=6.0, rate=48000))
            time.sleep(0.6)
            engine.poll()
            second = engine.snapshot()

            self.assertTrue(second["exclusive"],
                            f"播 48k 时丢了独占 —— skip 集被 44.1k 的失败污染了"
                            f"（note={second['mode_note']!r}）")
            self.assertEqual(second["rate"], 48000, "48k 源必须按 48000 开流")
            output_stage = next(
                s for s in second["signal_path"] if s["key"] == "output"
            )
            self.assertIs(StageStatus(output_stage["status"]), StageStatus.PASSTHROUGH,
                          f"48k 独占本应原样达成，却标了 {output_stage['status']}"
                          f"（{output_stage['detail'][:60]}）")
            self.assertFalse(second["fallback"], "48k 不该被判为回退")
            self.assertIn(first["rate"], (44100, 48000), "第一次应已降级到可用率")
        finally:
            engine.shutdown()

    def test_7b_skip_set_still_skips_same_triple(self) -> None:
        """同一 (设备, 44.1k, exclusive) 第二次必须跳过已知失败层级。

        即：修成三维键之后，「同设备同率同模式仍能跳过」不能丢 —— 这是 skip 集
        存在的意义（第二次起播不再重演两次失败开流）。
        """
        device = find_wasapi_device("Nahimic")
        if device is None:
            self.skipTest("本机没有 Nahimic 设备")

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_ramp_ms = 2

        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.output import AudioOutput
        from seqaudio.player.queue import PlayQueue

        real_prepare = AudioOutput.prepare
        attempts: list[tuple[int, str]] = []

        def counting(self, rate, channels=0, **kwargs):
            mode = normalize_output_mode(kwargs.get("mode") or OutputMode.SHARED)
            attempts.append((int(rate), mode.value))
            return real_prepare(self, rate, channels, **kwargs)

        engine = PlayerEngine(settings)
        engine.set_queue(PlayQueue([make_item(self.source, duration=8.0)]))
        try:
            with mock.patch.object(AudioOutput, "prepare", counting):
                engine.play()
                time.sleep(0.5)
                first = list(attempts)
                engine.stop()
                time.sleep(0.2)
                attempts.clear()
                engine.play()
                time.sleep(0.4)
                second = list(attempts)

            self.assertGreater(len(first), 1,
                               f"第一次应逐级降级，实际 {first}")
            self.assertEqual(len(second), 1,
                             f"第二次应直接命中缓存，实际 {second}")
        finally:
            engine.shutdown()

    def test_2_exclusive_true_means_real_exclusive(self) -> None:
        """② 实际值不说谎：真拿到独占时 exclusive=True 且率等于源率。"""
        device = find_wasapi_device("Realtek(R) Audio")
        if device is None:
            self.skipTest("本机没有「Realtek(R) Audio」WASAPI 设备")
        if not dev_mod.supports_rate(device, 44100, 2, mode="exclusive"):
            self.skipTest("该设备当前不支持 44.1k 独占")

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_ramp_ms = 2

        snapshot = play_once(settings, make_item(self.source, title="excl"))
        self.assertTrue(snapshot["exclusive"], "拿到了独占就必须报 True")
        self.assertEqual(snapshot["output_mode"], "exclusive")
        self.assertEqual(snapshot["rate"], 44100,
                         "独占下必须跟随源采样率（这正是 v1.6 的坑）")
        self.assertEqual(int(snapshot["system_mix_rate"]), 48000,
                         "系统混音率应如实上报，作为「已绕开」的证据")
        output_stage = next(s for s in snapshot["signal_path"] if s["key"] == "output")
        self.assertIs(StageStatus(output_stage["status"]), StageStatus.PASSTHROUGH)

    def test_6_legacy_config_migrates(self) -> None:
        """⑥ 老配置：``playback_exclusive=True`` → ``output_mode == "exclusive"``。"""
        tmp = Path(tempfile.mkdtemp(prefix="seq17_cfg_"))
        try:
            legacy = tmp / "config.json"
            legacy.write_text('{"language": "zh", "playback_exclusive": true}',
                              encoding="utf-8")
            settings = Settings.load(legacy)
            self.assertEqual(settings.playback_output_mode, "exclusive",
                             "老布尔值必须迁移成三值模式，不能静默回 shared")
            self.assertTrue(settings.playback_exclusive)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_6b_pending_choice_is_visible(self) -> None:
        """⑥ 后半：``fallback=ask`` 时降级必须留待确认标记（Q9 不许静默变更）。"""
        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.output import AudioOutput
        from seqaudio.player.queue import PlayQueue

        device = find_wasapi_device("Realtek(R) Audio") or dev_mod.pick_default_device()
        patched = refuse_exclusive_prepare(
            AudioOutput.prepare, lambda: dev_mod.DeviceError("该设备不支持独占模式")
        )

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_exclusive_fallback = "ask"
        settings.playback_ramp_ms = 2

        engine = PlayerEngine(settings)
        try:
            engine.set_queue(PlayQueue([make_item(self.source, duration=8.0)]))
            with mock.patch.object(AudioOutput, "prepare", patched):
                self.assertTrue(engine.play())
                time.sleep(0.6)
                snapshot = engine.snapshot()
            self.assertTrue(snapshot.get("exclusive_choice_pending"),
                            "ask 策略下必须留下待确认提示，否则就是静默降级")
        finally:
            engine.shutdown()


# ---------------------------------------------------------------- 冒烟 E1–E6


class TestExclusiveSmoke(unittest.TestCase):
    """§12.1 的 E1–E6；能自动化的都做了，E3 标人工。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq17_smoke_"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @unittest.skipUnless(FFMPEG is not None and HAS_DEVICE, "缺 ffmpeg 或没有输出设备")
    def test_e1_44100_exclusive_real(self) -> None:
        """E1：44.1k FLAC + 独占 → 有声、率 44100、output 级显示独占。"""
        device = find_wasapi_device("Realtek(R) Audio") or find_wasapi_device("Realtek")
        if device is None or not dev_mod.supports_rate(device, 44100, 2, mode="exclusive"):
            self.skipTest("本机没有支持 44.1k 独占的 WASAPI 设备")
        source = synth_flac(self.tmp / "e1.flac", seconds=8.0, rate=44100)

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_ramp_ms = 2

        snapshot = play_once(settings, make_item(source, duration=8.0), wait=0.8)
        self.assertEqual(snapshot["rate"], 44100)
        self.assertTrue(snapshot["exclusive"])
        output_stage = next(s for s in snapshot["signal_path"] if s["key"] == "output")
        self.assertIn("独占", output_stage["detail"])
        self.assertEqual(snapshot["counters"]["underruns"], 0)

    @unittest.skipUnless(FFMPEG is not None and HAS_DEVICE, "缺 ffmpeg 或没有输出设备")
    def test_e2_96k_unlocked_by_exclusive(self) -> None:
        """E2：96k 在共享下回退、在独占下直接 96k（同一设备对比）。"""
        device = find_wasapi_device("Realtek(R) Audio")
        if device is None:
            self.skipTest("本机没有 dev21 类设备")
        if dev_mod.supports_rate(device, 96000, 2, mode="shared"):
            self.skipTest("该设备共享模式已支持 96k，构不成对照")
        if not dev_mod.supports_rate(device, 96000, 2, mode="exclusive"):
            self.skipTest("该设备独占也不支持 96k")
        source = synth_flac(self.tmp / "e2.flac", seconds=8.0, rate=96000)

        def play(mode: str) -> dict:
            settings = Settings()
            settings.playback_output_backend = "wasapi"
            settings.playback_device = str(device.index)
            settings.playback_output_mode = mode
            settings.playback_ramp_ms = 2
            return play_once(settings, make_item(source, duration=8.0, rate=96000,
                                                 bit_depth=24), wait=0.6)

        shared = play("shared")
        exclusive = play("exclusive")
        self.assertNotEqual(shared["rate"], 96000, "共享模式应回退（本机实测如此）")
        self.assertEqual(exclusive["rate"], 96000,
                         "独占必须直接上 96k —— 本版最重要的产品事实")
        self.assertTrue(exclusive["exclusive"])

    @unittest.skip(SKIP_MANUAL)
    def test_e3_exclusive_silences_other_apps(self) -> None:
        """E3（人工）：独占时浏览器无声、有「独占」徽标、停播后恢复。

        判定步骤：① 开浏览器播视频确认有声；② 本程序选独占并起播，确认浏览器
        静音且播放条/状态栏显示「独占」；③ 停止播放，确认浏览器声音自动恢复。
        机器无法稳定造出「另一个程序正占着设备」的场景，故留人工。
        """

    @unittest.skipUnless(FFMPEG is not None and HAS_DEVICE, "缺 ffmpeg 或没有输出设备")
    def test_e4_device_busy_degrades(self) -> None:
        """E4：独占开流报设备忙 → auto 策略降级共享并出声。"""
        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.output import AudioOutput
        from seqaudio.player.queue import PlayQueue

        source = synth_flac(self.tmp / "e4.flac", seconds=6.0, rate=44100)
        device = find_wasapi_device("Realtek(R) Audio") or dev_mod.pick_default_device()
        patched = refuse_exclusive_prepare(
            AudioOutput.prepare, lambda: dev_mod.DeviceBusyError("设备已被其它程序独占")
        )

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "exclusive"
        settings.playback_exclusive_fallback = "auto"
        settings.playback_ramp_ms = 1

        engine = PlayerEngine(settings)
        try:
            engine.set_queue(PlayQueue([make_item(source, duration=6.0)]))
            with mock.patch.object(AudioOutput, "prepare", patched):
                started = engine.play()
            time.sleep(0.5)
            self.assertTrue(started, "auto 策略下设备忙应降级共享成功")
            self.assertIsNot(engine.state, PlayerState.ERROR)
            self.assertFalse(engine.snapshot()["exclusive"])
        finally:
            engine.shutdown()

    @unittest.skipUnless(FFMPEG is not None and HAS_DEVICE, "缺 ffmpeg 或没有输出设备")
    def test_e6_multichannel_downmix_or_explicit_error(self) -> None:
        """E6：5.1 文件——要么下混后出声，要么给出**提到声道**的明确错误。

        本机 7.1 设备只接受部分声道数（随 Windows 扬声器配置变化），所以这里
        断言的是「不静默变单声道、错误可行动」，不是某台设备的固定结论。
        """
        device = find_wasapi_device("Realtek(R) Audio")
        if device is None:
            self.skipTest("没有可用于多声道验证的 WASAPI 设备")
        if not dev_mod.supports_rate(device, 48000, 2, mode="shared"):
            self.skipTest("该设备当前连 2ch 都打不开，无法验证下混")
        source = synth_flac(self.tmp / "e6.flac", seconds=6.0, rate=48000, channels=6)

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        settings.playback_output_mode = "shared"
        settings.playback_downmix_stereo = True
        settings.playback_ramp_ms = 2

        snapshot = play_once(settings, make_item(source, duration=6.0, rate=48000,
                                                 channels=6), wait=0.8)
        self.assertEqual(snapshot["state"], PlayerState.PLAYING.value,
                         f"开下混后 5.1 应能播：{snapshot['error']}")
        self.assertEqual(snapshot["channels"], 2, "开了下混就必须是 2 声道")
        self.assertGreater(int(snapshot["output"]["frames_out"]), 0, "没有出声")
        self.assertEqual(snapshot["counters"]["underruns"], 0)


# ---------------------------------------------------------------- 探测可信度


class TestProbeCredibility(unittest.TestCase):
    def test_matrix_file_is_consumable(self) -> None:
        matrix = ROOT / "docs" / "1.7-独占能力矩阵.json"
        if not matrix.exists():
            self.skipTest("尚未生成能力矩阵（先跑 tools/probe_exclusive.py --verify-open）")
        data = json.loads(matrix.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["devices"]), 1)
        for device in data["devices"]:
            for mode in ("shared", "exclusive", "strict"):
                self.assertEqual(set(device["modes"][mode]),
                                 {str(rate) for rate in data["rates"]})

    @unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
    def test_probe_repeatable(self) -> None:
        device = find_wasapi_device("Realtek(R) Audio") or dev_mod.pick_default_device()
        first = dev_mod.probe_capabilities(device, channels=2).to_dict()
        second = dev_mod.probe_capabilities(device, channels=2).to_dict()
        self.assertEqual(first["rates"], second["rates"])


# ---------------------------------------------------------------- 打包冒烟


def _pick_smoke_device():
    """挑一台**真能开 44100 独占流**的 WASAPI 设备。

    冒烟固定走 WASAPI（唯一能判独占的后端），且独占档要求跑源采样率 44100。
    本机默认输出可能是 8 声道环绕，WASAPI 双声道开流会被设备直接拒绝；
    只挑到「能开共享」的设备也不够——那些设备往往不支持 44100 独占，
    独占冒烟会悄悄跑成 48000。所以这里按**最终判据**挑：真实开一次
    44100 独占流，成功才算候选。
    """
    from seqaudio.player.output import AudioOutput
    from seqaudio.player.ringbuffer import PcmRingBuffer

    try:
        candidates = dev_mod.list_output_devices("wasapi")
    except Exception:  # noqa: BLE001 - 枚举失败就当没有
        return None
    for item in candidates:
        ring = PcmRingBuffer.from_seconds(0.2, 44100, 2, "float32")
        output = AudioOutput(ring, device=item.index, backend="wasapi", mode="exclusive")
        try:
            output.prepare(44100, 2, mode="exclusive")
            return item
        except Exception:  # noqa: BLE001 - 这台不行，换下一台
            continue
        finally:
            try:
                output.stop()
            except Exception:  # noqa: BLE001
                pass
    return None


@unittest.skipUnless(PACKAGED_EXE.exists(), "没有便携版 exe（先跑 build.py）")
class TestPackagedSmoke(unittest.TestCase):
    """独立验证 ⑦：打包 exe 三模式冒烟。"""

    @classmethod
    def setUpClass(cls) -> None:
        if FFMPEG is None:
            raise unittest.SkipTest("ffmpeg 不可用，无法生成素材")
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq17_pkg_"))
        cls.source = synth_flac(cls.tmp / "pkg44k.flac", seconds=8.0, rate=44100)
        cls.device_info = _pick_smoke_device()
        cls.device = cls.device_info.name if cls.device_info is not None else ""
        if not cls.device:
            raise unittest.SkipTest("本机没有支持 44100 独占的 WASAPI 设备")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _run(self, mode: str) -> dict:
        report_path = Path(tempfile.gettempdir()) / "seqaudio_audio_smoke.json"
        report_path.unlink(missing_ok=True)
        command = [str(PACKAGED_EXE), "--audio-smoke", str(self.source), f"--mode={mode}"]
        if self.device:
            command.append(f"--device={self.device}")
        proc = subprocess.run(
            command,
            capture_output=True, timeout=240,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(proc.returncode, 0,
                         f"{mode} 冒烟退出码 {proc.returncode}（设备={self.device or '系统默认'}）："
                         f"{proc.stdout.decode('utf-8', 'replace')[:200]}")
        return json.loads(report_path.read_text(encoding="utf-8"))

    def test_three_modes(self) -> None:
        shared = self._run("shared")
        self.assertEqual(shared["output_mode"], "shared")
        self.assertFalse(shared["exclusive"])
        self.assertEqual(shared["underruns"], 0)
        self.assertIsNotNone(shared["wording_level"])

        exclusive = self._run("exclusive")
        self.assertEqual(exclusive["output_mode"], "exclusive")
        self.assertTrue(exclusive["exclusive"])
        self.assertEqual(exclusive["rate"], 44100,
                         "独占模式必须跑源采样率，不是 48000")
        self.assertEqual(exclusive["underruns"], 0)
        self.assertIn(exclusive["wording_level"], ("L1", "L2"))

        strict = self._run("exclusive_strict")
        self.assertTrue(strict["exclusive"], "严格模式这一档也必须拿到独占")
        self.assertEqual(strict["rate"], 44100, "严格模式必须跑源采样率")
        self.assertEqual(strict["underruns"], 0)

        # 严格是**设备特性**（本机 5 台 WASAPI 只有 Realtek 扬声器支持）。
        # 这里按**冒烟实际使用的设备**判断，不能用系统默认设备 —— 默认设备
        # 与冒烟设备可能不是同一台，混着判会得出相反结论。
        supports_strict = dev_mod.supports_rate(
            self.device_info.index, 44100, 2, mode="exclusive_strict"
        )
        if supports_strict:
            self.assertEqual(strict["output_mode"], "exclusive_strict")
            self.assertEqual(strict["wording_level"], "L2")
        else:
            self.assertEqual(strict["output_mode"], "exclusive",
                             "该设备不支持严格时应降级为宽松独占")
            self.assertTrue(strict["mode_note"], "降级必须留原因")


class _GrabbedDeviceOutput:
    """第一次开流成功；暂停释放设备后再开一律失败（模拟浏览器/会议抢设备）。"""

    open_calls = 0

    def __init__(self, ring, hook) -> None:
        self.ring = ring
        self.hook = hook
        self.rate = 0
        self.channels = int(ring.channels)
        self.started = False

    def prepare(self, rate, channels=0, *, mode=None, rate_decided=False):  # noqa: ANN001
        type(self).open_calls += 1
        if type(self).open_calls > 1:
            raise dev_mod.DeviceBusyError("Invalid device [PaErrorCode -9996]")
        self.rate = int(rate)
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
        return OutputMode.EXCLUSIVE

    @property
    def exclusive_actual(self) -> bool:
        return True

    def snapshot(self) -> dict:
        return {
            "is_open": self.started, "rate": self.rate, "want_rate": self.rate,
            "fallback": False, "channels": self.channels, "dtype": str(self.ring.dtype),
            "device": "grabbed", "device_index": -1, "backend": "wasapi",
            "exclusive": True, "mode": "exclusive", "system_mix_rate": 48000,
            "blocksize": 1024, "latency_ms": 0.0, "underruns": 0, "frames_out": 0,
            "pa_flags": 0, "cb_errors": 0, "cb_error": "", "paused": not self.started,
            "volume_db": 0.0, "error": "",
        }


class TestPauseReleaseFailure(unittest.TestCase):
    """Q3 默认 release_on_pause=True 后的新失败路径（发版前最后三项之一）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_pause_"))
        cls.source = synth_flac(cls.tmp / "44k.flac", seconds=6.0, rate=44100)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_device_grabbed_while_paused_enters_error_and_keeps_position(self) -> None:
        from seqaudio.player.engine import PlayerEngine
        from seqaudio.player.queue import PlayQueue

        _GrabbedDeviceOutput.open_calls = 0
        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_output_mode = "exclusive"
        settings.playback_release_on_pause = True
        settings.playback_ramp_ms = 2

        engine = PlayerEngine(
            settings,
            output_factory=lambda ring, device, hook: _GrabbedDeviceOutput(ring, hook),
        )
        try:
            engine.set_queue(PlayQueue([make_item(self.source, duration=6.0)]))
            self.assertTrue(engine.play(), f"首次起播失败：{engine.snapshot()['error']}")
            time.sleep(0.3)
            # 替身输出不会自己跑回调，手动消费一块，让位置指针往前走
            import numpy as np

            buf = np.zeros((1024, 2), dtype=engine._ring.dtype)
            deadline = time.monotonic() + 3.0
            while engine._ring.available < 1024 and time.monotonic() < deadline:
                time.sleep(0.01)
            engine._ring.read_into(buf)
            self.assertTrue(engine.pause(), "独占暂停应释放设备")
            frozen = engine.position_ms()
            self.assertGreater(frozen, 0, "暂停前应有可冻结的位置")

            self.assertFalse(engine.resume(), "设备被抢后恢复必须失败，不能假装成功")
            self.assertIs(engine.state, PlayerState.ERROR, "失败必须进 ERROR")
            text = str(engine.snapshot()["error"])
            self.assertIn("设备已被其它程序占用", text)
            self.assertIn("位置已保留", text)
            self.assertEqual(engine.position_ms(), frozen, "恢复失败后位置必须原样保留")
            # 不得静默改成共享、也不得跳到下一首
            self.assertEqual(engine.snapshot()["output_mode"], "exclusive")
            self.assertEqual(len(engine._queue), 1)
        finally:
            engine.shutdown()


class TestModeHintNotFlooding(unittest.TestCase):
    """运行日志不得被同一句「后端不支持独占」刷屏（用户截图反馈）。"""

    def test_same_backend_logs_once_across_snapshots(self) -> None:
        from seqaudio.player.engine import PlayerEngine

        settings = Settings()
        settings.playback_output_backend = "mme"
        settings.playback_output_mode = "exclusive"
        engine = PlayerEngine(settings)
        try:
            messages: list[str] = []
            with mock.patch(
                "seqaudio.player.engine.log.info",
                side_effect=lambda msg, *args, **kwargs: messages.append(str(msg)),
            ):
                for _ in range(30):
                    snap = engine.snapshot()
                    self.assertEqual(snap["output_mode"], "shared")
            self.assertEqual(
                sum("不支持独占" in text for text in messages), 1,
                f"同一后端/模式只应提示一次，实际 {messages}",
            )

            # 用户切回 WASAPI 再切回来：属于新的决定，应当再提示一次
            settings.playback_output_backend = "wasapi"
            engine.snapshot()
            settings.playback_output_backend = "mme"
            with mock.patch(
                "seqaudio.player.engine.log.info",
                side_effect=lambda msg, *args, **kwargs: messages.append(str(msg)),
            ):
                engine.snapshot()
            self.assertEqual(sum("不支持独占" in text for text in messages), 2)
        finally:
            engine.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
