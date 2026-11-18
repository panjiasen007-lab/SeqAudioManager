"""播放内核自测（T02）：环形缓冲 / 设备探测 / 解码器 / 端到端输出。

运行：``python tests/test_player_core.py``（不需要 Qt）。

这几项测试会**真的**打开一次输出设备并播 2 s 正弦波（架构文档 T02 验收
第①条要求的端到端验证）。音量固定 0 dB 直通、源信号幅度只有 -18 dBFS，
想静音就把系统音量调小，不要改测试。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.player import ringbuffer as rb_mod  # noqa: E402
from seqaudio.player.constants import (  # noqa: E402
    BLOCK_FRAMES_DEFAULT,
    DSD_PCM_RATE_DEFAULT,
    PREFILL_BLOCKS,
    PlayerState,
)
from seqaudio.player.decoder import (  # noqa: E402
    DecodeError,
    DecodeSpec,
    FfmpegDecoder,
    probe_codec,
)
from seqaudio.player.output import AudioOutput  # noqa: E402
from seqaudio.player.ringbuffer import PcmRingBuffer  # noqa: E402

_SINE_SEC = 2.0
_SINE_RATE = 44100
_SINE_CH = 2
_SINE_BYTES_PER_FRAME = 2 * _SINE_CH  # s16 × 2 声道


def _make_sine_flac(target: Path, seconds: float = _SINE_SEC, rate: int = _SINE_RATE) -> Path:
    """用 ffmpeg 现场生成一个正弦波 FLAC（不依赖仓库里的样本文件）。"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise unittest.SkipTest("ffmpeg 不可用，跳过解码相关测试")
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi",
        "-i", f"sine=frequency=440:duration={seconds}:sample_rate={rate}",
        "-ac", str(_SINE_CH),
        "-c:a", "flac",
        str(target),
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise unittest.SkipTest(f"生成样本失败：{proc.stderr.decode('utf-8', 'replace')}")
    return target


class TestRingBuffer(unittest.TestCase):
    def test_basic_write_read(self) -> None:
        ring = PcmRingBuffer(8, channels=2, dtype="int16")
        block = np.arange(10, dtype=np.int16).reshape(5, 2)
        self.assertEqual(ring.write(block), 5)
        self.assertEqual(ring.available, 5)
        self.assertEqual(ring.free, 3)
        self.assertEqual(ring.frames_written, 5)

        out = np.zeros((5, 2), dtype=np.int16)
        self.assertEqual(ring.read_into(out), 5)
        np.testing.assert_array_equal(out, block)
        self.assertEqual(ring.available, 0)
        self.assertEqual(ring.free, 8)
        self.assertEqual(ring.frames_read, 5)

    def test_short_write_and_zero_write(self) -> None:
        ring = PcmRingBuffer(4, channels=1, dtype="float32")
        self.assertEqual(ring.write(np.ones((6, 1), dtype=np.float32)), 4)
        self.assertEqual(ring.partial_writes, 1)
        self.assertEqual(ring.write(np.ones((1, 1), dtype=np.float32)), 0)
        self.assertEqual(ring.starved_writes, 1)

    def test_wraparound(self) -> None:
        ring = PcmRingBuffer(5, channels=1, dtype="int16")
        # 三轮「写满-读空」，每轮数据不同，确保回绕后取到的是新数据
        for round_index in range(3):
            block = np.arange(5, dtype=np.int16) + round_index * 100
            self.assertEqual(ring.write(block.reshape(-1, 1)), 5)
            out = np.zeros((5, 1), dtype=np.int16)
            self.assertEqual(ring.read_into(out), 5)
            np.testing.assert_array_equal(out.reshape(-1), block)

        # 跨界写：先写 3，读 3，再写 5（后半段会绕过数组尾部）
        ring.reset()
        first = np.array([[1], [2], [3]], dtype=np.int16)
        ring.write(first)
        out = np.zeros((3, 1), dtype=np.int16)
        ring.read_into(out)
        second = np.arange(1, 6, dtype=np.int16).reshape(5, 1)
        self.assertEqual(ring.write(second), 5)
        result = np.zeros((5, 1), dtype=np.int16)
        self.assertEqual(ring.read_into(result), 5)
        np.testing.assert_array_equal(result, second)

    def test_read_partial_and_empty(self) -> None:
        ring = PcmRingBuffer(8, channels=2, dtype="int16")
        out = np.zeros((4, 2), dtype=np.int16)
        self.assertEqual(ring.read_into(out), 0)  # 空环读 0 帧，不阻塞、不抛
        ring.write(np.ones((2, 2), dtype=np.int16))
        self.assertEqual(ring.read_into(out), 2)

    def test_reset(self) -> None:
        ring = PcmRingBuffer.from_seconds(1.0, 1000, channels=2, dtype="int16")
        self.assertEqual(ring.capacity, 1000)
        ring.write(np.ones((300, 2), dtype=np.int16))
        self.assertEqual(ring.available, 300)
        ring.clear()
        self.assertEqual(ring.available, 0)
        self.assertEqual(ring.frames_written, 0)
        self.assertEqual(ring.frames_read, 0)

    def test_no_lock_in_implementation(self) -> None:
        """硬约束：环形缓冲不许用 threading.Lock。"""
        source = Path(rb_mod.__file__).read_text(encoding="utf-8")
        # 注意：不能直接断言 "threading" 不出现 —— 模块 docstring 里正是用
        # 「为什么不用 threading.Lock」来解释设计，断言会误伤自己的注释。
        self.assertNotIn("import threading", source)
        self.assertNotIn("Lock(", source)
        self.assertNotIn("with self._lock", source)

    def test_spsc_concurrent(self) -> None:
        """一条生产者 + 一条消费者：写入总量必须与读出总量一致且有序。"""
        ring = PcmRingBuffer(256, channels=1, dtype="int32")
        total = 20000
        received: list[np.ndarray] = []

        def producer() -> None:
            value = 0
            while value < total:
                chunk = np.arange(value, min(value + 64, total), dtype=np.int32).reshape(-1, 1)
                written = ring.write(chunk)
                value += written

        def consumer() -> None:
            out = np.zeros((64, 1), dtype=np.int32)
            got = 0
            while got < total:
                read = ring.read_into(out)
                if read:
                    received.append(out[:read].copy())
                    got += read
                else:
                    time.sleep(0.0005)

        threads = [threading.Thread(target=producer), threading.Thread(target=consumer)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        data = np.concatenate(received).reshape(-1)
        self.assertEqual(len(data), total)
        np.testing.assert_array_equal(data, np.arange(total, dtype=np.int32))


class TestDevices(unittest.TestCase):
    def test_list_output_devices(self) -> None:
        items = dev_mod.list_output_devices()
        self.assertGreaterEqual(len(items), 1, "至少应有一台输出设备")
        for device in items:
            self.assertGreater(device.channels, 0)

    def test_default_device_supports_44100(self) -> None:
        device = dev_mod.pick_default_device()
        self.assertTrue(dev_mod.supports_rate(device, 44100))
        self.assertTrue(dev_mod.supports_rate(device, 48000))

    def test_backend_resolution(self) -> None:
        for key in ("wasapi", "mme", "directsound"):
            index = dev_mod.resolve_backend(key)
            self.assertGreaterEqual(index, 0, f"{key} 后端应被识别")
        self.assertEqual(dev_mod.resolve_backend(""), -1)
        self.assertEqual(dev_mod.resolve_backend("no_such_backend"), -1)

    def test_pick_rate_fallback(self) -> None:
        device = dev_mod.pick_default_device()
        # 192 kHz 不是每台设备都支持；pick_rate 必须给出可用率并标明是否回退
        rate, fallback = dev_mod.pick_rate(device, 192000)
        self.assertGreater(rate, 0)
        self.assertTrue(dev_mod.supports_rate(device, rate))
        self.assertEqual(fallback, rate != 192000)
        # 打不开的设备必须抛 RateUnsupportedError，而不是静默返回一个假值
        ghost = dev_mod.DeviceInfo(
            index=-999, name="ghost", channels=2, default_samplerate=44100.0
        )
        with self.assertRaises(dev_mod.RateUnsupportedError):
            dev_mod.pick_rate(ghost, 44100)

    def test_constants_are_int(self) -> None:
        self.assertEqual(DSD_PCM_RATE_DEFAULT, 176400)
        self.assertEqual(PlayerState.PLAYING.value, "PLAYING")


class TestDecoder(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_player_"))
        cls.flac = _make_sine_flac(cls.tmp / "sine2s.flac")

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _spec(self, **kwargs) -> DecodeSpec:
        base = dict(
            path=self.flac,
            sample_rate=_SINE_RATE,
            channels=_SINE_CH,
            sample_fmt="s16",
            block_frames=BLOCK_FRAMES_DEFAULT,
        )
        base.update(kwargs)
        return DecodeSpec(**base)

    def test_probe_codec(self) -> None:
        version, codec = probe_codec(self.flac)
        self.assertTrue(version.startswith("ffmpeg"), version)
        self.assertEqual(codec, "flac")

    def test_pull_mode_total_bytes(self) -> None:
        decoder = FfmpegDecoder(self._spec())
        decoder.start()
        try:
            frames = 0
            blocks = 0
            while True:
                block = decoder.read_block()
                if block is None:
                    break
                self.assertEqual(block.shape[1], _SINE_CH)
                frames += int(block.shape[0])
                blocks += 1
            self.assertGreater(blocks, 0)
        finally:
            decoder.stop()
        expected = int(_SINE_SEC * _SINE_RATE)
        self.assertLessEqual(
            abs(frames - expected), BLOCK_FRAMES_DEFAULT,
            f"解码帧数 {frames} 与期望 {expected} 相差超过一个块",
        )
        self.assertEqual(decoder.returncode, 0)

    def test_start_sec_seek(self) -> None:
        decoder = FfmpegDecoder(self._spec(start_ms=1000))
        decoder.start()
        try:
            frames = 0
            while True:
                block = decoder.read_block()
                if block is None:
                    break
                frames += int(block.shape[0])
        finally:
            decoder.stop()
        expected = int(1.0 * _SINE_RATE)
        self.assertLessEqual(
            abs(frames - expected), BLOCK_FRAMES_DEFAULT,
            f"seek 到 1.0 s 后帧数 {frames}，期望约 {expected}",
        )

    def test_push_mode_fills_ring(self) -> None:
        ring = PcmRingBuffer.from_seconds(3.0, _SINE_RATE, channels=_SINE_CH, dtype="int16")
        decoder = FfmpegDecoder(self._spec(), ring=ring)
        decoder.start()
        try:
            deadline = time.monotonic() + 10.0
            while time.monotonic() < deadline and ring.available < 44100:
                time.sleep(0.01)
            self.assertGreaterEqual(ring.available, 44100, "推模式应把数据写进环")
            self.assertGreater(decoder.position_sec, 0.5)
            self.assertTrue(decoder.is_alive() or decoder.at_eof)
        finally:
            decoder.stop()
        self.assertEqual(decoder.dropped_frames, 0)

    def test_sample_format_matrix(self) -> None:
        """四种样本格式都要真能出 PCM。

        这一条是踩过坑的回归测试：架构文档第十节把 ``-f`` 的管道格式名和
        ``-acodec`` 的编解码器名混在一张表里，照抄会拼出 ``-acodec f32le``，
        ffmpeg 直接拒绝启动 —— 而浮点恰好是**默认**通路，等于整个播放功能
        静音，却只在真实文件上才暴露（正弦样本测试当时用的是 s16）。
        """
        expected = {"s16": "int16", "s24": "int32", "s32": "int32", "f32": "float32"}
        for fmt, dtype_name in expected.items():
            decoder = FfmpegDecoder(self._spec(sample_fmt=fmt))
            decoder.start()
            frames = 0
            seen_dtype = ""
            try:
                while True:
                    block = decoder.read_block()
                    if block is None:
                        break
                    frames += int(block.shape[0])
                    seen_dtype = block.dtype.name
            finally:
                decoder.stop()
            self.assertEqual(seen_dtype, dtype_name, f"{fmt} 的 numpy dtype 应为 {dtype_name}")
            self.assertLessEqual(
                abs(frames - int(_SINE_SEC * _SINE_RATE)), BLOCK_FRAMES_DEFAULT,
                f"{fmt} 通路解码帧数 {frames} 不符",
            )
            self.assertEqual(decoder.returncode, 0, f"{fmt} 通路 ffmpeg 应正常退出")

    def test_build_args_codec_names(self) -> None:
        self.assertIn("pcm_f32le", self._spec(sample_fmt="f32").build_args("ffmpeg"))
        self.assertIn("f32le", self._spec(sample_fmt="f32").build_args("ffmpeg"))
        self.assertIn("pcm_s32le", self._spec(sample_fmt="s24").build_args("ffmpeg"))

    def test_pause_feeding_holds_data(self) -> None:
        """暂停播放时解码线程必须「等」而不是「丢」。"""
        ring = PcmRingBuffer.from_seconds(3.0, _SINE_RATE, channels=_SINE_CH, dtype="int16")
        decoder = FfmpegDecoder(self._spec(), ring=ring)
        decoder.start()
        try:
            decoder.pause_feeding()          # 立刻关闸
            time.sleep(0.05)
            before = ring.available
            time.sleep(0.15)
            self.assertEqual(ring.available, before, "暂停期间不应继续写入")
            self.assertEqual(decoder.dropped_frames, 0, "暂停不应导致丢帧")
            decoder.resume_feeding()
            deadline = time.monotonic() + 5.0
            while ring.available <= before and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertGreater(ring.available, before, "恢复后应继续喂数据")
        finally:
            decoder.stop()

    def test_missing_file_raises(self) -> None:
        decoder = FfmpegDecoder(self._spec(path=self.tmp / "nope.flac"))
        with self.assertRaises(DecodeError):
            decoder.start()
        decoder.stop()  # 未启动时 stop 也必须安全

    def test_build_args_contract(self) -> None:
        spec = self._spec(start_ms=1500, duration_ms=2000)
        args = spec.build_args("ffmpeg")
        joined = " ".join(args)
        self.assertIn("-map 0:a:0", joined)
        self.assertIn("-f s16le", joined)
        self.assertIn("-acodec pcm_s16le", joined)
        self.assertEqual(args[-1], "-")
        # -ss 必须在 -i 之后（输出侧精确 seek）
        self.assertGreater(args.index("-ss"), args.index("-i"))
        self.assertIn("1.500", joined)
        self.assertIn("2.000", joined)

    def test_from_track_reads_cue_bounds(self) -> None:
        from types import SimpleNamespace

        entry = SimpleNamespace(start_ms=30000, end_ms=60000)
        track = SimpleNamespace(
            path=self.flac,
            info=SimpleNamespace(
                fmt="FLAC", sample_rate=96000, bit_depth=24, channels=2, dsd_rate=None
            ),
            cue_track_entry=entry,
        )
        settings = SimpleNamespace(
            playback_follow_source_rate=True,
            playback_dsd_pcm_rate=176400,
            playback_downmix_stereo=False,
            playback_int_pipeline=False,
            playback_bit_perfect=False,
            playback_block_frames=1024,
        )
        spec = DecodeSpec.from_track(track, settings)
        self.assertEqual(spec.start_ms, 30000)
        self.assertEqual(spec.duration_ms, 30000)
        self.assertEqual(spec.sample_rate, 96000)
        self.assertEqual(spec.channels, 2)
        self.assertEqual(spec.codec, "flac")
        self.assertEqual(spec.sample_fmt, "f32")

        # 整数通路：位深如实映射
        settings.playback_int_pipeline = True
        self.assertEqual(DecodeSpec.from_track(track, settings).sample_fmt, "s24")
        # 多声道下混
        track.info.channels = 6
        settings.playback_int_pipeline = False
        settings.playback_downmix_stereo = True
        self.assertEqual(DecodeSpec.from_track(track, settings).channels, 2)


class TestEndToEnd(unittest.TestCase):
    """解码 → 环形缓冲 → PortAudio 实际出声。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_player_e2e_"))
        # 3 s 素材、只验证前 2 s：播到「数据耗尽」时回调必然欠载一次（这是
        # 引擎该在 EOF 时停流的事，T03），不该由 T02 的断言语义背锅。
        cls.flac = _make_sine_flac(cls.tmp / "sine3s.flac", seconds=3.0)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _ring(self) -> PcmRingBuffer:
        # 与生产一致：RING_SECONDS = 2.0
        ring = PcmRingBuffer.from_seconds(
            2.0, _SINE_RATE, channels=_SINE_CH, dtype="int16"
        )
        return ring

    def test_play_two_seconds(self) -> None:
        ring = self._ring()
        device = dev_mod.pick_default_device()
        output = AudioOutput(ring, device=device.index, backend="", blocksize=BLOCK_FRAMES_DEFAULT)
        spec = DecodeSpec(
            path=self.flac,
            sample_rate=_SINE_RATE,
            channels=_SINE_CH,
            sample_fmt="s16",
            block_frames=BLOCK_FRAMES_DEFAULT,
        )
        decoder = FfmpegDecoder(spec, ring=ring)

        started = time.monotonic()
        decoder.start()
        try:
            while (
                ring.available < PREFILL_BLOCKS * BLOCK_FRAMES_DEFAULT
                and time.monotonic() - started < 10.0
            ):
                time.sleep(0.002)
            prefill_ms = (time.monotonic() - started) * 1000.0
            self.assertGreaterEqual(ring.available, PREFILL_BLOCKS * BLOCK_FRAMES_DEFAULT)

            rate = output.start(_SINE_RATE, _SINE_CH)
            self.assertEqual(rate, _SINE_RATE, "默认设备应支持 44.1 kHz，不应回退")

            target_frames = int(_SINE_SEC * _SINE_RATE)
            deadline = time.monotonic() + 20.0
            while output.frames_out < target_frames and time.monotonic() < deadline:
                time.sleep(0.005)
            snap = output.snapshot()
        finally:
            output.stop()
            decoder.stop()

        self.assertGreaterEqual(snap["frames_out"], target_frames)
        self.assertEqual(snap["rate"], _SINE_RATE)
        self.assertEqual(snap["underruns"], 0, f"端到端播放不应欠载（起播预填 {prefill_ms:.0f} ms）")
        self.assertEqual(snap["cb_errors"], 0)
        self.assertEqual(decoder.dropped_frames, 0, "实时消费下解码侧不应丢数据")
        self.assertGreater(prefill_ms, 0.0)

    def test_pause_writes_silence_without_underrun(self) -> None:
        ring = self._ring()
        device = dev_mod.pick_default_device()
        output = AudioOutput(ring, device=device.index, backend="", blocksize=BLOCK_FRAMES_DEFAULT)
        ring.write(np.zeros((44100, _SINE_CH), dtype=np.int16))
        try:
            output.start(_SINE_RATE, _SINE_CH)
            output.pause()
            time.sleep(0.1)
            paused = output.snapshot()
            output.resume()
            time.sleep(0.1)
            resumed = output.snapshot()
        finally:
            output.stop()
        self.assertTrue(paused["paused"])
        self.assertFalse(resumed["paused"])
        self.assertGreater(resumed["frames_out"], 0)
        self.assertEqual(resumed["underruns"], 0)

    def test_rate_switch_no_leak(self) -> None:
        ring = self._ring()
        device = dev_mod.pick_default_device()
        output = AudioOutput(ring, device=device.index, backend="", blocksize=BLOCK_FRAMES_DEFAULT)
        before_devices = len(dev_mod.list_output_devices())
        before_threads = threading.active_count()
        rates: list[int] = []
        try:
            for _ in range(3):
                rate = output.start(_SINE_RATE, _SINE_CH)
                rates.append(rate)
                time.sleep(0.05)
                output.stop()
                rate = output.start(96000, _SINE_CH)
                rates.append(rate)
                time.sleep(0.05)
                output.stop()
        finally:
            output.stop()
        self.assertEqual(rates[0], _SINE_RATE)
        self.assertEqual(rates[1], 96000)
        self.assertFalse(output.is_open)
        self.assertEqual(len(dev_mod.list_output_devices()), before_devices)
        self.assertLessEqual(
            threading.active_count() - before_threads, 2,
            "反复开关流不应遗留线程",
        )

    def test_callback_hook_sees_filled_frames(self) -> None:
        ring = PcmRingBuffer(4096, channels=_SINE_CH, dtype="int16")
        device = dev_mod.pick_default_device()
        seen: list[int] = []

        def hook(buf: np.ndarray, filled: int) -> None:
            seen.append(int(filled))

        output = AudioOutput(
            ring, device=device.index, backend="", blocksize=BLOCK_FRAMES_DEFAULT, hook=hook
        )
        ring.write(np.ones((4096, _SINE_CH), dtype=np.int16))
        try:
            output.start(_SINE_RATE, _SINE_CH)
            time.sleep(0.1)
        finally:
            output.stop()
        self.assertTrue(seen, "钩子应被回调调用")
        self.assertTrue(all(count > 0 for count in seen))


class TestFoundation(unittest.TestCase):
    """T01 的验收项：配置字段、会话路径、i18n、无 Qt 边界、版本号。"""

    #: 架构文档第七节的 34 个新字段与默认值（逐项核对，改了设计必须同步这里）
    EXPECTED_DEFAULTS: dict[str, object] = {
        "playback_enabled": True,
        "playback_bar_visible": True,
        "playback_signal_panel_visible": False,
        # T03 决策：默认后端改为 MME（WASAPI 共享只吃 48k，会强制重采样）
        "playback_output_backend": "mme",
        "playback_exclusive": False,
        "playback_device": "",
        "playback_follow_source_rate": True,
        "playback_dsd_pcm_rate": 176400,
        "playback_bit_perfect": False,
        "playback_volume_mode": "bypass",
        "playback_volume_db": 0.0,
        "playback_block_frames": 1024,
        "playback_buffer_ms": 80,
        "playback_dsp_bypass": True,
        "playback_eq_enabled": False,
        "playback_eq_gains": [0.0] * 10,
        "playback_eq_preset": "flat",
        "playback_eq_snapshot_a": [0.0] * 10,
        "playback_eq_snapshot_b": [0.0] * 10,
        "playback_gapless": True,
        "playback_crossfade_sec": 0.0,
        "playback_ramp_ms": 5,
        "playback_mode": "sequential",
        "playback_downmix_stereo": False,
        "playback_int_pipeline": False,
        "playback_restore_session": True,
        "playback_restore_position": True,
        "playback_lyrics_offset_ms": 0,
        "playback_writeback_tags": False,
        "playback_exclusive_notice_shown": False,
        "playback_mini_bar": False,
        "library_roots": [],
        "library_autoload": True,
        "library_recent_sort": True,
    }

    def test_settings_defaults(self) -> None:
        import dataclasses

        from seqaudio.core.config import Settings

        settings = Settings()
        for name, expected in self.EXPECTED_DEFAULTS.items():
            self.assertTrue(hasattr(settings, name), f"Settings 缺少字段 {name}")
            self.assertEqual(getattr(settings, name), expected, f"{name} 默认值不符")
        playback = [f.name for f in dataclasses.fields(Settings) if f.name.startswith("playback_")]
        library = [f.name for f in dataclasses.fields(Settings) if f.name.startswith("library_")]
        self.assertEqual(len(playback), 31, f"playback_* 应为 31 个，实际 {len(playback)}")
        self.assertEqual(len(library), 3, f"library_* 应为 3 个，实际 {len(library)}")
        # 可变默认值必须是独立对象（否则两个实例会共享同一个 list）
        self.assertIsNot(Settings().playback_eq_gains, Settings().playback_eq_gains)
        self.assertIsNot(Settings().library_roots, Settings().library_roots)

    def test_old_config_loads_with_new_defaults(self) -> None:
        from seqaudio.core.config import Settings

        tmp = Path(tempfile.mkdtemp(prefix="seq_cfg_"))
        try:
            legacy = tmp / "config.json"
            legacy.write_text(
                '{"language": "en", "conflict_mode": "强制覆盖", "unknown_key": 1}',
                encoding="utf-8",
            )
            settings = Settings.load(legacy)
            self.assertEqual(settings.language, "en")
            self.assertEqual(settings.playback_output_backend, "mme")
            self.assertEqual(settings.playback_follow_source_rate, True)
            self.assertEqual(settings.playback_dsp_bypass, True)
            self.assertEqual(settings.library_roots, [])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_session_path(self) -> None:
        from seqaudio.core import paths

        target = paths.session_path()
        self.assertEqual(target.name, "playback_session.json")
        self.assertEqual(target.parent, paths.data_dir())
        self.assertTrue(target.parent.exists())

    def test_i18n_all_locales(self) -> None:
        from seqaudio.ui import i18n

        original = i18n.current()
        try:
            keys = ("play_play", "play_pause", "play_next", "sig_panel", "pl_queue", "lyr_panel")
            for key in keys:
                for locale in i18n.locale_names():
                    i18n.set_locale(locale)
                    text = i18n.tr(key)
                    self.assertTrue(text and text != key, f"{locale} 缺 {key} 的译文")
            i18n.set_locale("en")
            self.assertEqual(i18n.tr("play_play"), "Play")
            # tr_text 通路（界面直接写简体原文）也要能翻
            self.assertEqual(i18n.tr_text("播放"), "Play")
            self.assertEqual(i18n.tr_text("已旁路"), "Bypassed")
            self.assertEqual(i18n.tr_text("未做软件改动"), "No software change")
        finally:
            i18n.set_locale(original)

    def test_player_package_has_no_qt(self) -> None:
        code = (
            "import sys; sys.path.insert(0, r'%s');"
            "import seqaudio.player as p;"
            "print('PySide6' in sys.modules)"
        ) % (ROOT / "src")
        proc = subprocess.run(
            [sys.executable, "-c", code],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        out = proc.stdout.decode("utf-8", "replace").strip().splitlines()
        self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
        self.assertEqual(out[-1], "False", "player 包不得 import PySide6")

    def test_package_versions_agree(self) -> None:
        import seqaudio

        self.assertEqual(seqaudio.__version__, "1.6.0")
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('version = "1.6.0"', pyproject)
        meta = (ROOT / "installer" / "_meta.py").read_text(encoding="utf-8")
        self.assertIn('VERSION = "1.6.0"', meta)

    def test_t03_exports_resolve(self) -> None:
        """T03 落地后，包出口的惰性名字必须都能取到（且仍然不碰 Qt）。"""
        import seqaudio.player as player

        for name in (
            "PlayerEngine",
            "PlayerSnapshot",
            "PlayerError",
            "PlayQueue",
            "QueueItem",
            "SignalPath",
            "SignalStage",
            "Counters",
            "PlaybackSession",
            "GainStage",
            "BiquadEq",
        ):
            self.assertTrue(hasattr(player, name), f"包出口缺少 {name}")
        # 未实现的名字仍要给出明确 AttributeError
        with self.assertRaises(AttributeError):
            _ = player.NoSuchPlayerThing

    def test_ringbuffer_accepts_single_channel_1d(self) -> None:
        ring = PcmRingBuffer(4, channels=1, dtype="int16")
        self.assertEqual(ring.write(np.array([1, 2, 3], dtype=np.int16)), 3)
        out = np.zeros(3, dtype=np.int16)
        self.assertEqual(ring.read_into(out), 3)
        np.testing.assert_array_equal(out, np.array([1, 2, 3], dtype=np.int16))
        with self.assertRaises(ValueError):
            ring.write(np.ones((2, 2), dtype=np.int16))


if __name__ == "__main__":
    unittest.main(verbosity=2)
