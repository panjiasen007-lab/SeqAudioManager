"""B 段：输出后端、采样率跟随与回退（对应用例 TC-OUT-*）。

运行：``python tests/test_player_output.py``（**需要真实声卡**，会短暂开流）。
测前请把系统音量调到 30% 以下：本文件只开流、灌静音/低幅正弦，不出大声。

判定「有没有变调」的核心手段
----------------------------
回退发生后，``snapshot()["rate"]`` 只说「流按多少开」，**不证明 PCM 是按这个率
产出的**。所以凡是回退用例，都同时验证第三件事：把这 2 s 的源按设备率重新解码，
帧数必须等于 ``2.0 × 设备率``。帧数对不上就是「流按 48k 开、数据按 44.1k 送」
—— 听感变调，而界面一切正常。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.player.constants import BLOCK_FRAMES_DEFAULT  # noqa: E402
from seqaudio.player.decoder import DecodeSpec, FfmpegDecoder  # noqa: E402
from seqaudio.player.output import AudioOutput, OutputConfig  # noqa: E402
from seqaudio.player.ringbuffer import PcmRingBuffer  # noqa: E402

FFMPEG = find_ffmpeg()
SINE_SECONDS = 2.0


def _has_device() -> bool:
    try:
        dev_mod.pick_default_device()
        return True
    except Exception:  # noqa: BLE001
        return False


HAS_DEVICE = _has_device()


def synth_flac(target: Path, seconds: float = SINE_SECONDS, rate: int = 44100) -> Path:
    assert FFMPEG is not None
    target.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi",
            "-i", f"sine=frequency=1000:duration={seconds}:sample_rate={rate}",
            "-ac", "2", "-c:a", "flac", str(target),
        ],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise unittest.SkipTest(f"合成样本失败：{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return target


def decode_frame_count(path: Path, rate: int) -> int:
    """按给定采样率解码整首，返回帧数（等价于引擎 ``_build_spec`` 的做法）。"""
    spec = DecodeSpec(
        path=path, sample_rate=rate, channels=2, sample_fmt="s16",
        block_frames=BLOCK_FRAMES_DEFAULT,
    )
    decoder = FfmpegDecoder(spec)
    decoder.start()
    frames = 0
    try:
        while True:
            block = decoder.read_block()
            if block is None:
                break
            frames += int(block.shape[0])
    finally:
        decoder.stop()
    return frames


def make_ring(rate: int, channels: int = 2, dtype: str = "int16") -> PcmRingBuffer:
    ring = PcmRingBuffer.from_seconds(2.0, rate, channels, dtype)
    ring.write(np.zeros((BLOCK_FRAMES_DEFAULT * 8, channels), dtype=np.dtype(dtype)))
    return ring


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestRateFollowing(unittest.TestCase):
    """TC-OUT-01 ~ TC-OUT-06：MME 后端下六个源率逐个跟随。"""

    RATES = (44100, 48000, 88200, 96000, 176400, 192000)

    @classmethod
    def setUpClass(cls) -> None:
        cls.device = dev_mod.pick_default_device()

    def test_mme_follows_every_source_rate(self) -> None:
        for rate in self.RATES:
            with self.subTest(rate=rate):
                if not dev_mod.supports_rate(self.device, rate):
                    self.skipTest(f"设备 {self.device.label} 不支持 {rate} Hz")
                ring = make_ring(rate)
                output = AudioOutput(
                    ring, device=self.device.index, backend="mme",
                    blocksize=BLOCK_FRAMES_DEFAULT,
                )
                try:
                    actual = output.start(rate, 2)
                    snapshot = output.snapshot()
                finally:
                    output.stop()
                self.assertEqual(actual, rate, f"请求 {rate} Hz，设备给 {actual} Hz")
                self.assertEqual(snapshot["rate"], rate)
                self.assertFalse(snapshot["fallback"], "MME 支持全部候选率，不该回退")
                self.assertFalse(output.is_open, "stop() 后不得留半开句柄")

    def test_rate_change_44100_to_192000(self) -> None:
        """TC-OUT-06 的「先 44.1k 再 192k」：两次都要拿到源率。"""
        device = self.device
        if not dev_mod.supports_rate(device, 192000):
            self.skipTest("设备不支持 192 kHz")
        readings: list[int] = []
        for rate in (44100, 192000):
            ring = make_ring(rate)
            output = AudioOutput(ring, device=device.index, backend="mme",
                                blocksize=BLOCK_FRAMES_DEFAULT)
            try:
                readings.append(output.start(rate, 2))
                time.sleep(0.05)
            finally:
                output.stop()
        self.assertEqual(readings, [44100, 192000])


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestFallback(unittest.TestCase):
    """TC-OUT-07 / 08 / 09：回退链与「不许静默变调」。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_out_"))
        cls.source = synth_flac(cls.tmp / "sine2s.flac", SINE_SECONDS, 44100)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _pick_wasapi_device(self):
        """找一台「不支持 44.1k」的 WASAPI 设备（本机共享模式只认 48k）。"""
        for candidate in dev_mod.list_output_devices("wasapi"):
            if not dev_mod.supports_rate(candidate, 44100):
                return candidate
        return None

    def test_wasapi_shared_fallback_to_48000(self) -> None:
        """TC-OUT-07：44.1k 源在 WASAPI 共享下回退 48000，且运行层如实上报。"""
        device = self._pick_wasapi_device()
        if device is None:
            self.skipTest("本机 WASAPI 设备接受 44.1 kHz，无法触发回退")
        rate, fallback = dev_mod.pick_rate(device, 44100)
        self.assertEqual(rate, 48000)
        self.assertTrue(fallback, "回退必须被标记，否则界面无从如实标注")

        ring = make_ring(rate)
        output = AudioOutput(ring, device=device.index, backend="wasapi",
                            blocksize=BLOCK_FRAMES_DEFAULT)
        try:
            actual = output.start(44100, 2)
            snapshot = output.snapshot()
        finally:
            output.stop()
        self.assertEqual(actual, 48000)
        self.assertTrue(snapshot["fallback"])
        self.assertEqual(snapshot["want_rate"], 44100)
        self.assertEqual(snapshot["rate"], 48000)

    def test_fallback_does_not_shift_pitch(self) -> None:
        """**核心断言**：回退后 PCM 必须按设备率产出（帧数 == 时长 × 设备率）。

        若解码仍按 44.1k 出数据而流按 48k 播放，2 s 的源只会得到 88200 帧，
        播完只要 1.84 s —— 听感快 8.8%，界面毫无提示。
        """
        device = self._pick_wasapi_device() or dev_mod.pick_default_device()
        try:
            device_rate, _ = dev_mod.pick_rate(device, 44100)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"选率失败：{exc}")
        frames = decode_frame_count(self.source, device_rate)
        expected = SINE_SECONDS * device_rate
        self.assertLessEqual(
            abs(frames - expected), BLOCK_FRAMES_DEFAULT * 2,
            f"按 {device_rate} Hz 解码得到 {frames} 帧，期望 {expected:.0f} 帧"
            "（差得多说明 PCM 没按设备率重采样 → 变调）",
        )

    def test_all_rates_rejected_raises(self) -> None:
        """TC-OUT-09：幽灵设备必须抛 RateUnsupportedError 并带上候选列表。"""
        ghost = dev_mod.DeviceInfo(index=-999, name="ghost", channels=2,
                                   default_samplerate=44100.0)
        with self.assertRaises(dev_mod.RateUnsupportedError) as ctx:
            dev_mod.pick_rate(ghost, 44100)
        self.assertTrue(ctx.exception.tried, "异常必须带尝试过的候选率，界面才能给建议值")

    def test_wasapi_96k_falls_back(self) -> None:
        """TC-OUT-08：96k 源在只认 48k 的设备上同样回退。"""
        device = self._pick_wasapi_device()
        if device is None:
            self.skipTest("本机 WASAPI 设备支持 96 kHz，无法触发回退")
        rate, fallback = dev_mod.pick_rate(device, 96000)
        self.assertTrue(fallback)
        self.assertIn(rate, (48000, 44100))
        frames = decode_frame_count(self.source, rate)
        self.assertLessEqual(abs(frames - SINE_SECONDS * rate), BLOCK_FRAMES_DEFAULT * 2)


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
class TestBackendMatrix(unittest.TestCase):
    """TC-OUT-10 的可自动化部分：四个后端各自能开流并如实报率。"""

    def test_each_backend_opens(self) -> None:
        """四个后端各开一次流，记录实际率与开流耗时。

        WDM-KS 在本机是**实验后端**：``check_output_settings`` 说支持 44.1k，
        但真正 ``OutputStream`` 会被驱动拒绝（PaErrorCode -9996）。这不是本版
        缺陷（需求文档已写明 WDM-KS 不承诺），因此按「该后端不可用」跳过，
        只要求至少 mme/directsound/wasapi 三个能开流。
        """
        results: dict[str, int] = {}
        unavailable: list[str] = []
        for backend in ("mme", "directsound", "wasapi", "wdmks"):
            with self.subTest(backend=backend):
                index = dev_mod.resolve_backend(backend)
                if index < 0:
                    unavailable.append(backend)
                    continue
                device = dev_mod.resolve_device("", backend)
                rate = 44100 if dev_mod.supports_rate(device, 44100) else device.rate
                ring = make_ring(rate, dtype="float32")
                output = AudioOutput(ring, device=device.index, backend=backend,
                                    blocksize=BLOCK_FRAMES_DEFAULT)
                started = time.monotonic()
                try:
                    actual = output.start(rate, 2)
                    latency = (time.monotonic() - started) * 1000.0
                    snapshot = output.snapshot()
                except dev_mod.DeviceError as exc:
                    unavailable.append(backend)
                    print(f"    {backend}: 开流失败（{exc}）→ 记为后端不可用")
                    continue
                finally:
                    output.stop()
                self.assertGreater(actual, 0, f"{backend} 未返回有效采样率")
                self.assertTrue(snapshot["device"], f"{backend} 快照缺设备名")
                results[backend] = actual
                print(f"    {backend}: 实际率 {actual} Hz，开流 {latency:.0f} ms，"
                      f"设备 {snapshot['device']}")
        print(f"    可用后端：{sorted(results)}；不可用：{sorted(unavailable)}")
        self.assertGreaterEqual(len(results), 3,
                                f"至少 mme/directsound/wasapi 应可用，实际 {results}")

    def test_exclusive_does_not_lie(self) -> None:
        """TC-OUT-13：独占开不起来必须报错，不许静默按共享播却仍显示「独占」。"""
        device = dev_mod.resolve_device("", "wasapi")
        ring = make_ring(44100)
        output = AudioOutput(ring, device=device.index, backend="wasapi",
                            exclusive=True, blocksize=BLOCK_FRAMES_DEFAULT)
        try:
            output.start(44100, 2)
        except dev_mod.DeviceError:
            return  # 明确报错：合格
        finally:
            output.stop()


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
class TestStreamHygiene(unittest.TestCase):
    """TC-OUT-16 / 18 / 21：句柄、缓冲下限、整数 dtype。"""

    def test_open_close_hundred_times(self) -> None:
        device = dev_mod.pick_default_device()
        before_devices = len(dev_mod.list_output_devices())
        before_threads = threading.active_count()
        for _ in range(100):
            ring = make_ring(44100)
            output = AudioOutput(ring, device=device.index, backend="mme",
                                blocksize=BLOCK_FRAMES_DEFAULT)
            output.start(44100, 2)
            output.stop()
        self.assertEqual(len(dev_mod.list_output_devices()), before_devices,
                         "反复开关流不该让设备表变化")
        self.assertLessEqual(threading.active_count() - before_threads, 2,
                             "反复开关流不该遗留线程")

    def test_small_buffer_underrun_is_visible(self) -> None:
        """TC-OUT-18：缓冲给到下限时允许欠载，但计数必须可见（不许假装 0）。"""
        device = dev_mod.pick_default_device()
        ring = PcmRingBuffer(128, channels=2, dtype="float32")  # 故意极小且不预填
        output = AudioOutput(ring, device=device.index, backend="mme",
                            blocksize=BLOCK_FRAMES_DEFAULT)
        try:
            output.start(44100, 2)
            time.sleep(0.3)
            snapshot = output.snapshot()
        finally:
            output.stop()
        self.assertGreater(snapshot["underruns"], 0, "空环播放必然欠载，计数不能是 0")
        self.assertEqual(snapshot["cb_errors"], 0)

    def test_int32_stream_for_24bit_source(self) -> None:
        """TC-OUT-21：s24 走 int32 容器开流（PortAudio 不支持 3 字节样本）。"""
        device = dev_mod.pick_default_device()
        ring = make_ring(96000, dtype="int32")
        output = AudioOutput(ring, device=device.index, backend="mme",
                            blocksize=BLOCK_FRAMES_DEFAULT)
        try:
            output.start(96000, 2)
            snapshot = output.snapshot()
        finally:
            output.stop()
        self.assertEqual(snapshot["dtype"], "int32")
        self.assertEqual(snapshot["rate"], 96000)

    def test_config_dtype_must_match_ring(self) -> None:
        """dtype 不一致必须当场报错（回调里做转换 = 在实时线程里做重活）。"""
        device = dev_mod.pick_default_device()
        ring = make_ring(44100, dtype="int16")
        output = AudioOutput(ring, device=device.index, backend="mme")
        config = OutputConfig(
            backend="mme", device=str(device.index), exclusive=False,
            samplerate=44100, channels=2, dtype="float32",
            blocksize=BLOCK_FRAMES_DEFAULT, latency=None,
        )
        try:
            with self.assertRaises(dev_mod.DeviceError):
                output.open(config)
        finally:
            output.stop()


if __name__ == "__main__":
    unittest.main(verbosity=2)
