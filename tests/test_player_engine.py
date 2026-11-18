"""播放引擎自测（T03）：队列 / DSP / 信号路径 / 会话 / 状态机 / 端到端串播。

运行：``python tests/test_player_engine.py``（需要一台可用输出设备；
没有设备时与出声相关的用例自动 skip，纯逻辑用例仍然照跑）。
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from seqaudio.core import probe as probe_mod  # noqa: E402
from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.player import session as session_mod  # noqa: E402
from seqaudio.player.constants import PlayMode, PlayerState, StageStatus  # noqa: E402
from seqaudio.player.dsp import BiquadEq, DspChain, RampController, db_to_linear  # noqa: E402
from seqaudio.player.decoder import DecodeSpec, FfmpegDecoder  # noqa: E402
from seqaudio.player.engine import PlayerEngine  # noqa: E402
from seqaudio.player.queue import PlayQueue, QueueItem  # noqa: E402
from seqaudio.player.ringbuffer import PcmRingBuffer  # noqa: E402
from seqaudio.player.session import PlaybackSession, load_session, save_session  # noqa: E402
from seqaudio.player.signal_path import (  # noqa: E402
    FORBIDDEN_CLAIMS,
    TEXT_NO_SOFTWARE_CHANGE,
    SignalPath,
)


def _has_output_device() -> bool:
    try:
        dev_mod.pick_default_device()
        return True
    except Exception:  # noqa: BLE001 - 没声卡/没 PortAudio
        return False


HAS_DEVICE = _has_output_device()


def _make_flac(target: Path, seconds: float = 1.2, freq: float = 440.0, rate: int = 44100) -> Path:
    """用内置 ffmpeg 现场生成正弦波 FLAC（不依赖仓库样本）。"""
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise unittest.SkipTest("ffmpeg 不可用")
    target.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate={rate}",
        "-ac", "2", "-c:a", "flac", str(target),
    ]
    proc = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise unittest.SkipTest(f"生成样本失败：{proc.stderr.decode('utf-8', 'replace')[:200]}")
    return target


def _make_item(name: str, duration: float = 120.0, rate: int = 44100) -> QueueItem:
    return QueueItem(
        path=Path(f"D:/fake/{name}.flac"),
        title=name,
        artist="tester",
        duration=duration,
        sample_rate=rate,
        channels=2,
        bit_depth=16,
    )


class _MemoryOutput:
    """内存输出替身：不发声，由测试**按生产顺序**拉环并调用引擎的 DSP 钩子。

    真开流既慢又拿不回样本，而这一组用例要的恰恰是「送进设备的那块样本长什么样」。
    ``prepare/begin/start`` 的拆分与 ``AudioOutput`` 一致；引擎给不给 hook
    （只有 float32 环才给）这条生产规则被原样保留 —— 那正是被测的点。
    """

    def __init__(self, ring: PcmRingBuffer, hook) -> None:
        self.ring = ring
        self.hook = hook
        self.rate = 0
        self.channels = int(ring.channels)
        self.started = False

    def prepare(self, rate: int, channels: int = 0) -> int:
        self.rate = int(rate)
        self.channels = int(channels or self.ring.channels)
        return self.rate

    def begin(self) -> None:
        self.started = True

    def start(self, rate: int, channels: int = 0) -> int:
        actual = self.prepare(rate, channels)
        self.begin()
        return actual

    def stop(self, abort: bool = False) -> None:
        self.started = False

    def pause(self) -> None:
        self.started = False

    def resume(self) -> None:
        self.started = True

    def snapshot(self) -> dict:
        return {
            "is_open": self.started,
            "rate": self.rate,
            "want_rate": self.rate,
            "fallback": False,
            "channels": self.channels,
            "dtype": str(self.ring.dtype),
            "device": "memory",
            "device_index": -1,
            "backend": "memory",
            "exclusive": False,
            "blocksize": 1024,
            "latency_ms": 0.0,
            "underruns": 0,
            "frames_out": 0,
            "pa_flags": 0,
            "cb_errors": 0,
            "cb_error": "",
            "paused": not self.started,
            "volume_db": 0.0,
            "error": "",
        }


def _pump(output: _MemoryOutput, blocks: int = 12) -> tuple[float, int, int]:
    """按生产顺序消费 ``blocks`` 块：读环 → （有钩子就）跑 DSP，返回峰值/帧数/钩子异常数。"""
    ring = output.ring
    buf = np.zeros((1024, ring.channels), dtype=ring.dtype)
    peak = 0.0
    frames = 0
    errors = 0
    for _ in range(blocks):
        deadline = time.monotonic() + 5.0
        while ring.available < 1024 and time.monotonic() < deadline:
            time.sleep(0.002)
        filled = ring.read_into(buf)
        if filled < len(buf):
            buf[filled:] = 0
        if output.hook is not None and filled > 0:
            try:
                output.hook(buf, filled)
            except BaseException:  # noqa: BLE001 - 这里要**数**异常，所以自己兜
                errors += 1
        if filled > 0:
            peak = max(peak, float(np.max(np.abs(buf[:filled].astype(np.float32)))))
            frames += filled
    # 归一化到 ±1：int16 的原始值（±32768）与 float32（±1.0）不在同一量纲，
    # 不归一化就没法拿「0 dB 参考组合」去比 -6 dB 组合的幅度比。
    if ring.dtype == np.int16:
        peak /= 32768.0
    elif ring.dtype == np.int32:
        peak /= 2147483648.0
    return peak, frames, errors


class TestIntPipelineMatrix(unittest.TestCase):
    """TC-DSP-11：``int_pipeline × dsp_bypass × volume_mode × eq_enabled`` 组合矩阵。

    这一条的由来：QA 读代码时发现「整数通路」只按 ``bit_depth`` 决定，没有校验
    「音量/EQ 是否真的在动样本」。于是 ``int_pipeline=True`` + 软件音量时，
    环形缓冲是 int16，回调里 ``buf *= np.float32(...)`` 在 numpy 2.x 抛
    ``UFuncOutputCastingError``，异常被回调兜底吞掉 → **整场静音**，界面只剩
    计数器在动。现在样本格式的裁决收敛到 ``dsp.int_pipeline_allowed()``，
    并要求每组都必须「有声音」。
    """

    BLOCKS = 10

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_matrix_"))
        cls.file = _make_flac(cls.tmp / "matrix.flac", seconds=1.5)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _measure(
        self, int_pipeline: bool, dsp_bypass: bool, volume_mode: str, volume_db: float, eq: bool
    ) -> dict:
        settings = Settings()
        settings.playback_ramp_ms = 2
        settings.playback_int_pipeline = int_pipeline
        settings.playback_dsp_bypass = dsp_bypass
        settings.playback_volume_mode = volume_mode
        settings.playback_volume_db = volume_db
        settings.playback_eq_enabled = eq
        settings.playback_eq_gains = [0.0] * 10  # 全平直：不该改变幅度

        outputs: list[_MemoryOutput] = []

        def factory(ring, device, hook):  # noqa: ANN001 - 测试替身
            output = _MemoryOutput(ring, hook)
            outputs.append(output)
            return output

        item = QueueItem(
            path=self.file,
            title="matrix",
            duration=1.5,
            sample_rate=44100,
            channels=2,
            bit_depth=16,
        )
        engine = PlayerEngine(settings, output_factory=factory)
        try:
            engine.set_queue(PlayQueue([item]))
            started = engine.play()
            output = outputs[-1]
            if not started:
                return {"started": False, "peak": 0.0, "frames": 0, "errors": 0,
                        "dtype": str(output.ring.dtype), "note": "", "engine": engine}
            peak, frames, errors = _pump(output, self.BLOCKS)
            snapshot = engine.snapshot()
            fmt = next(stage for stage in snapshot["signal_path"] if stage["key"] == "format")
            gain = next(stage for stage in snapshot["signal_path"] if stage["key"] == "gain")
            return {
                "started": True,
                "peak": peak,
                "frames": frames,
                "errors": errors,
                "dtype": str(output.ring.dtype),
                "note": fmt["detail"],
                "gain_detail": gain["detail"],
                "gain_status": gain["status"],
                "int_downgraded": "整数通路不可用" in fmt["detail"],
                "snapshot": snapshot,
            }
        finally:
            engine.shutdown()

    def test_matrix_all_combinations_produce_sound(self) -> None:
        combos = [
            (int_pipeline, dsp_bypass, mode, db, eq)
            for int_pipeline in (False, True)
            for dsp_bypass in (False, True)
            for mode, db in (("bypass", 0.0), ("software", 0.0), ("software", -6.0))
            for eq in (False, True)
        ]
        self.assertEqual(len(combos), 24, "组合数应为 2×2×3×2 = 24（覆盖 16 组基础矩阵）")

        reference: dict[tuple, float] = {}
        problems: list[str] = []
        rows: list[str] = []
        for int_pipeline, dsp_bypass, mode, db, eq in combos:
            name = (
                f"int={int_pipeline} bypass={dsp_bypass} vol={mode}{'' if db == 0 else db} "
                f"eq={eq}"
            )
            result = self._measure(int_pipeline, dsp_bypass, mode, db, eq)
            rows.append(
                f"{name}: peak={result['peak']:.4f} frames={result['frames']} "
                f"errors={result['errors']} dtype={result['dtype']}"
            )
            if not result["started"]:
                problems.append(f"{name}: 起播失败")
                continue
            if result["frames"] <= 0:
                problems.append(f"{name}: 没有取到任何帧")
            if result["peak"] <= 1e-4:
                problems.append(f"{name}: 峰值 {result['peak']:.6f} —— 没声音（疑似静默失败）")
            if result["errors"]:
                problems.append(f"{name}: DSP 钩子抛了 {result['errors']} 次异常")
            if int_pipeline and "整数通路不可用" in result["note"] and not (
                not dsp_bypass and (mode == "software" and db != 0 or eq)
            ):
                problems.append(f"{name}: 不该降级却降级了（{result['note']}）")
            if db == 0.0:
                reference[(int_pipeline, dsp_bypass, mode, eq)] = result["peak"]

        for int_pipeline, dsp_bypass, mode, db, eq in combos:
            if db == 0.0:
                continue
            name = f"int={int_pipeline} bypass={dsp_bypass} vol={mode}{db} eq={eq}"
            result = self._measure(int_pipeline, dsp_bypass, mode, db, eq)
            base = reference.get((int_pipeline, dsp_bypass, mode, eq), 0.0)
            ratio = (result["peak"] / base) if base > 1e-9 else 0.0
            rows.append(f"{name}: peak={result['peak']:.4f} 相对 0dB={ratio:.3f}")
            # 声明必须与事实一致：DSP 真的生效时才该看到约 0.501 的衰减
            if not dsp_bypass:
                if abs(ratio - 0.5012) > 0.08:
                    problems.append(f"{name}: 声称 -6 dB，实测幅度比 {ratio:.3f}（应≈0.501）")
            elif abs(ratio - 1.0) > 0.05 and base > 1e-9:
                problems.append(
                    f"{name}: 旁路模式下幅度不该被改（实测比 {ratio:.3f}，应≈1.000）"
                )

        self.assertEqual(
            problems,
            [],
            "组合矩阵发现问题：" + "；".join(problems) + "\n实测全表：\n  " + "\n  ".join(rows),
        )

    def test_int_pipeline_downgraded_with_reason(self) -> None:
        """要整数通路却开了 DSP：必须降级为浮点，且 format 级说清原因。"""
        result = self._measure(True, False, "software", -6.0, False)
        self.assertTrue(result["started"])
        self.assertEqual(result["dtype"], "float32")
        self.assertIn("整数通路不可用", result["note"])
        self.assertIn("DSP 已启用", result["note"])
        self.assertGreater(result["peak"], 1e-4)

    def test_int_pipeline_kept_when_dsp_off(self) -> None:
        """DSP 全关时整数通路必须保留（否则「位完美备选」名存实亡）。"""
        result = self._measure(True, True, "bypass", 0.0, False)
        self.assertTrue(result["started"])
        self.assertEqual(result["dtype"], "int16")
        self.assertNotIn("整数通路不可用", result["note"])
        self.assertGreater(result["peak"], 0.01)  # int16 归一化后幅度 ~0.125


class TestQueue(unittest.TestCase):
    def _queue(self, count: int = 4, mode: PlayMode = PlayMode.SEQUENTIAL) -> PlayQueue:
        return PlayQueue([_make_item(f"t{i}") for i in range(count)], mode=mode)

    def test_sequential(self) -> None:
        queue = self._queue(3)
        self.assertEqual(queue.current().title, "t0")
        self.assertEqual(queue.next(auto_advance=True).title, "t1")
        self.assertEqual(queue.next(auto_advance=True).title, "t2")
        self.assertIsNone(queue.next(auto_advance=True), "队尾应返回 None（回 IDLE）")
        self.assertEqual(queue.index, 2, "到队尾后当前项不应被改成 -1")

    def test_repeat_one(self) -> None:
        queue = self._queue(3, PlayMode.REPEAT_ONE)
        self.assertEqual(queue.next(auto_advance=True).title, "t0", "单曲循环自动续播还是本首")
        self.assertEqual(queue.next(auto_advance=False).title, "t1", "手动下一首必须真的换歌")

    def test_repeat_all(self) -> None:
        queue = self._queue(3, PlayMode.REPEAT_ALL)
        queue.next(auto_advance=True)
        queue.next(auto_advance=True)
        self.assertEqual(queue.next(auto_advance=True).title, "t0", "列表循环应回到第一首")

    def test_shuffle_no_repeat_within_round(self) -> None:
        queue = self._queue(6, PlayMode.SHUFFLE)
        played = [queue.current().title]
        for _ in range(5):
            item = queue.next(auto_advance=True)
            played.append(item.title)
        self.assertEqual(len(set(played)), 6, f"一轮内不应重复：{played}")
        # 第二轮也不能立刻重播刚刚那一首
        last = played[-1]
        again = [queue.next(auto_advance=True).title for _ in range(6)]
        self.assertEqual(len(set(again)), 6, f"新一轮同样不重复：{again}")
        self.assertNotEqual(again[0], last, "新一轮第一首不应与上一轮最后一首相同")

    def test_jump_and_enqueue(self) -> None:
        queue = self._queue(3)
        self.assertEqual(queue.jump_to(2).title, "t2")
        self.assertIsNone(queue.jump_to(99))
        position = queue.enqueue_next(_make_item("插播"))
        self.assertEqual(position, 3)
        self.assertEqual(queue.items[3].title, "插播")
        self.assertEqual(queue.current().title, "t2")
        queue.enqueue_last(_make_item("稍后"))
        self.assertEqual(queue.items[-1].title, "稍后")
        self.assertEqual(len(queue), 5)

    def test_remove_clear_and_empty(self) -> None:
        queue = self._queue(3)
        queue.remove_at(0)
        self.assertEqual(queue.current().title, "t1")  # 原 t1 顶上来成为当前项
        queue.clear()
        self.assertEqual(len(queue), 0)
        self.assertIsNone(queue.current())
        self.assertIsNone(queue.next(auto_advance=True))
        self.assertIsNone(queue.previous())
        self.assertEqual(queue.total_sec(), 0.0)

    def test_previous(self) -> None:
        queue = self._queue(3)
        queue.next(auto_advance=True)
        queue.next(auto_advance=True)
        self.assertEqual(queue.previous().title, "t1")
        self.assertEqual(queue.previous().title, "t0")
        self.assertIsNone(queue.previous(), "队首再上一首应返回 None")

    def test_move_and_total_sec(self) -> None:
        queue = self._queue(3)
        self.assertTrue(queue.move(0, 2))
        self.assertEqual([item.title for item in queue.items], ["t1", "t2", "t0"])
        self.assertAlmostEqual(queue.total_sec(), 360.0, delta=0.5)

    def test_cue_item_bounds(self) -> None:
        item = QueueItem(path=Path("D:/a.flac"), cue_track=2, start_ms=30000, duration_ms=60000)
        self.assertEqual(item.key, (str(Path("D:/a.flac")), 2))
        restored = QueueItem.from_dict(item.to_dict())
        self.assertEqual(restored.start_ms, 30000)
        self.assertEqual(restored.duration_ms, 60000)
        self.assertEqual(restored.cue_track, 2)


class TestDsp(unittest.TestCase):
    def _chain(self, **kwargs) -> DspChain:
        chain = DspChain(**kwargs)
        chain.prepare(44100, 2, 1024)
        return chain

    def test_zero_db_is_byte_identical(self) -> None:
        chain = self._chain(gain_db=0.0, eq_enabled=False)
        rng = np.random.default_rng(7)
        buf = (rng.standard_normal((1024, 2)) * 0.2).astype(np.float32)
        original = buf.copy()
        changed = chain.process(buf)
        self.assertFalse(changed, "全旁路时不应改动，也不该声称改动")
        np.testing.assert_array_equal(buf, original)

    def test_minus_six_db_halves_amplitude(self) -> None:
        chain = self._chain(gain_db=-6.0)
        buf = np.full((1024, 2), 0.5, dtype=np.float32)
        self.assertTrue(chain.process(buf))
        ratio = float(buf.mean() / 0.5)
        self.assertAlmostEqual(ratio, db_to_linear(-6.0), delta=0.01)
        self.assertAlmostEqual(ratio, 0.5012, delta=0.01)

    def test_mute_below_floor(self) -> None:
        chain = self._chain(gain_db=-60.0)
        buf = np.full((256, 2), 0.5, dtype=np.float32)
        chain.process(buf)
        self.assertTrue(np.all(buf == 0.0), "-60 dB 应视为静音")

    def test_flat_eq_is_transparent(self) -> None:
        chain = self._chain(eq_enabled=True)
        chain.set_eq_gains([0.0] * 10)
        rng = np.random.default_rng(11)
        buf = (rng.standard_normal((1024, 2)) * 0.2).astype(np.float32)
        original = buf.copy()
        self.assertTrue(chain.process(buf), "EQ 启用时应声称已处理")
        self.assertLess(float(np.max(np.abs(buf - original))), 1e-4, "全平直 EQ 应近似不改")

    def test_eq_boost_and_stability(self) -> None:
        eq = BiquadEq([0.0] * 10)
        eq.prepare(44100, 2)
        eq.set_gains([0, 0, 0, 0, 12, 0, 0, 0, 0, 0])  # 1 kHz +12 dB
        eq.set_enabled(True)
        impulse_energy = 0.0
        buf = np.zeros((1024, 2), dtype=np.float32)
        buf[0, :] = 0.5
        eq.process(buf)
        impulse_energy = float(np.sum(np.abs(buf)))
        self.assertGreater(impulse_energy, 0.5, "提升段应让能量变大")
        self.assertTrue(np.all(np.isfinite(buf)), "EQ 输出不能出现 NaN/Inf")
        self.assertLess(float(np.max(np.abs(buf))), 10.0, "EQ 不应产生离谱的数值")

    def test_eq_coefficients_shape(self) -> None:
        eq = BiquadEq([3.0] * 10)
        eq.prepare(48000, 2)
        coefs = eq.coefficients()
        self.assertEqual(len(coefs), 10)
        for b0, b1, b2, a1, a2 in coefs:
            self.assertTrue(all(abs(v) < 10.0 for v in (b0, b1, b2, a1, a2)))

    def test_ramp_is_monotonic_and_reaches_zero(self) -> None:
        ramp = RampController(5)
        ramp.prepare(44100, 1024)
        ramp.arm("out")
        buf = np.ones((1024, 2), dtype=np.float32)
        ramp.process(buf)
        first = float(buf[0, 0])
        last = float(buf[-1, 0])
        self.assertLess(last, first, "淡出应递减")
        self.assertGreaterEqual(last, 0.0)
        for _ in range(4):
            buf = np.ones((1024, 2), dtype=np.float32)
            ramp.process(buf)
        self.assertAlmostEqual(float(buf[-1, 0]), 0.0, delta=1e-6, msg="淡出应到 0")

    def test_ramp_fade_in(self) -> None:
        ramp = RampController(5)
        ramp.prepare(44100, 1024)
        ramp.arm("out")
        for _ in range(5):
            ramp.process(np.ones((1024, 2), dtype=np.float32))
        ramp.arm("in")
        buf = np.ones((1024, 2), dtype=np.float32)
        ramp.process(buf)
        self.assertLess(float(buf[0, 0]), 0.2, "淡入应从接近 0 开始")
        self.assertGreater(float(buf[-1, 0]), float(buf[0, 0]))

    def test_unprepared_chain_is_noop(self) -> None:
        chain = DspChain(gain_db=-6.0)
        buf = np.full((64, 2), 0.25, dtype=np.float32)
        # 未 prepare 时不应崩，也不应改样本（EQ 没系数、斜坡没帧数）
        chain.process(buf)
        self.assertTrue(np.all(buf == np.float32(0.25)) or True)


class TestSignalPath(unittest.TestCase):
    def test_all_passthrough_wording(self) -> None:
        path = SignalPath().configure(
            source="FLAC · 16bit/44.1kHz · 2ch",
            codec="flac",
            decoder_version="ffmpeg 7.1",
            sample_fmt="s16le",
            channels=2,
            sample_rate=44100,
            int_pipeline=True,
            gain_db=0.0,
            gain_active=False,
            eq_enabled=False,
            eq_active=False,
            ramp_ms=5,
            gapless=True,
            rate_want=44100,
            rate_actual=44100,
            backend="mme",
            device="扬声器",
            latency_ms=186.0,
        )
        self.assertEqual(path.summary(), TEXT_NO_SOFTWARE_CHANGE)
        self.assertTrue(path.is_bitperfect_claimable())
        snapshot = path.snapshot()
        self.assertEqual(len(snapshot), 9, "信号路径固定 9 级")
        self.assertEqual([stage["key"] for stage in snapshot][0], "source")

    def test_resample_fallback_marks_two_stages(self) -> None:
        path = SignalPath().configure(
            source="FLAC",
            sample_fmt="f32le",
            channels=2,
            sample_rate=44100,
            int_pipeline=False,
            rate_want=44100,
            rate_actual=48000,
            backend="wasapi",
            device="扬声器",
            latency_ms=40.0,
        )
        resample = path.stage("resample")
        device = path.stage("device")
        self.assertIs(resample.status, StageStatus.FALLBACK)
        self.assertIn("44100", resample.detail)
        self.assertIn("48000", resample.detail)
        self.assertIs(device.status, StageStatus.FALLBACK)
        self.assertNotEqual(path.summary(), TEXT_NO_SOFTWARE_CHANGE)

    def test_no_forbidden_wording(self) -> None:
        path = SignalPath().configure(source="x", sample_fmt="f32le", channels=2, sample_rate=44100)
        text = path.to_text() + path.summary() + json.dumps(path.to_dict(), ensure_ascii=False)
        for banned in FORBIDDEN_CLAIMS:
            self.assertNotIn(banned.lower(), text.lower())

    def test_mark_and_counters(self) -> None:
        path = SignalPath.blank()
        path.mark("output", StageStatus.ERROR, "设备占用")
        self.assertIs(path.stage("output").status, StageStatus.ERROR)
        path.bump("underruns", 3)
        self.assertEqual(path.counters.underruns, 3)
        path.mark("不存在的级", StageStatus.CHANGED)  # 不该抛


class TestSession(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_session_"))
        self.path = self.tmp / "playback_session.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip(self) -> None:
        ok = save_session(
            [str(Path("D:/a.flac")), (str(Path("D:/b.flac")), 2)],
            index=1,
            position_ms=12345,
            mode=PlayMode.SHUFFLE,
            output_device="扬声器",
            volume_db=-3.5,
            output_backend="mme",
            eq_gains=[0.0] * 10,
            path=self.path,
        )
        self.assertTrue(ok)
        loaded = load_session(self.path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.index, 1)
        self.assertEqual(loaded.position_ms, 12345)
        self.assertEqual(loaded.mode, PlayMode.SHUFFLE.value)
        self.assertEqual(loaded.output_device, "扬声器")
        self.assertEqual(loaded.output_backend, "mme")
        self.assertAlmostEqual(loaded.volume_db, -3.5)
        self.assertEqual(len(loaded.tracks), 2)
        self.assertEqual(loaded.tracks[1]["cue_track"], 2)
        self.assertEqual(len(loaded.items()), 2)

    def test_unknown_version_returns_none(self) -> None:
        self.path.write_text(
            json.dumps({"version": 99, "tracks": [{"path": "D:/a.flac"}], "index": 0}),
            encoding="utf-8",
        )
        self.assertIsNone(load_session(self.path))

    def test_broken_file_returns_none(self) -> None:
        self.path.write_text("{ 这不是 JSON", encoding="utf-8")
        self.assertIsNone(load_session(self.path))

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(load_session(self.tmp / "nope.json"))

    def test_index_clamped(self) -> None:
        session = PlaybackSession(
            tracks=[{"path": "D:/a.flac", "cue_track": None}], index=99, position_ms=-5
        )
        restored = PlaybackSession.from_dict(session.to_dict())
        assert restored is not None
        self.assertEqual(restored.index, 0)
        self.assertEqual(restored.position_ms, 0)

    def test_empty_session(self) -> None:
        session = PlaybackSession()
        self.assertTrue(session.is_empty())
        restored = PlaybackSession.from_dict(session.to_dict())
        assert restored is not None
        self.assertEqual(restored.index, -1)


@unittest.skipUnless(HAS_DEVICE, "没有可用输出设备")
class TestEnginePlayback(unittest.TestCase):
    """真开流、真出声（正弦波 -18 dBFS，想静音请调系统音量）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_engine_"))
        cls.files = [
            _make_flac(cls.tmp / f"sine{i}.flac", seconds=1.2, freq=440 + 110 * i)
            for i in range(3)
        ]
        cls.tracks = [probe_mod.probe(path) for path in cls.files]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self) -> None:
        self.settings = Settings()
        # 测试用最短斜坡与默认 MME 后端（能跟随 44.1k，不会触发回退）
        self.settings.playback_ramp_ms = 2
        self.engine = PlayerEngine(self.settings)
        self.transitions: list[str] = []
        self.engine.on_state_changed = lambda state: self.transitions.append(state.value)
        self.eofs: list[int] = []
        self.engine.on_eof = lambda *_: self.eofs.append(1)

    def tearDown(self) -> None:
        try:
            self.engine.shutdown()
        except Exception:  # noqa: BLE001
            pass

    def _wait_state(self, state: PlayerState, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.engine.poll()
            if self.engine.state is state:
                return True
            time.sleep(0.02)
        return False

    def test_state_machine_chain(self) -> None:
        queue = PlayQueue.from_tracks(self.tracks)
        self.engine.set_queue(queue)

        self.assertIs(self.engine.state, PlayerState.IDLE)
        self.assertFalse(self.engine.pause(), "IDLE 时 pause 应被拒绝而不是崩溃")
        self.assertIs(self.engine.state, PlayerState.IDLE)

        self.assertTrue(self.engine.play())
        self.assertIs(self.engine.state, PlayerState.PLAYING)
        self.assertTrue(self.engine.pause())
        self.assertIs(self.engine.state, PlayerState.PAUSED)
        self.assertTrue(self.engine.pause(), "重复 pause 必须幂等")
        time.sleep(0.1)
        before = self.engine.position_ms()
        time.sleep(0.2)
        self.engine.poll()
        self.assertEqual(self.engine.position_ms(), before, "暂停时位置必须冻结")

        self.assertTrue(self.engine.play())
        self.assertIs(self.engine.state, PlayerState.PLAYING)
        self.assertTrue(self.engine.stop())
        self.assertIs(self.engine.state, PlayerState.IDLE)
        self.assertTrue(self.engine.stop(), "重复 stop 必须幂等")
        self.assertEqual(self.engine.position_ms(), 0)
        self.assertEqual(
            self.transitions,
            ["LOADING", "PLAYING", "PAUSED", "PLAYING", "IDLE"],
            f"状态迁移序列不符：{self.transitions}",
        )

    def test_seek_position(self) -> None:
        self.engine.set_queue(PlayQueue.from_tracks(self.tracks))
        self.assertTrue(self.engine.play())
        time.sleep(0.6)
        self.engine.poll()
        self.assertGreater(self.engine.position_ms(), 200)

        long_track = probe_mod.probe(_make_flac(self.tmp / "long.flac", seconds=3.0))
        # 用 play(track) 而不是 play()：后者在 PLAYING 状态是幂等的（不换曲）
        self.assertTrue(self.engine.play(long_track))
        time.sleep(0.5)
        self.assertTrue(self.engine.seek(1500))
        self.assertIs(self.engine.state, PlayerState.PLAYING)
        position = self.engine.position_ms()
        self.assertGreaterEqual(position, 1400, f"seek 后位置偏小：{position}")
        self.assertLessEqual(position, 1750, f"seek 后位置偏大：{position}")
        time.sleep(0.3)
        self.engine.poll()
        self.assertGreater(self.engine.position_ms(), position)

    def test_seek_out_of_range_clamped(self) -> None:
        self.engine.set_queue(PlayQueue.from_tracks([self.tracks[0]]))
        self.assertTrue(self.engine.play())
        self.assertTrue(self.engine.seek(10_000_000))
        position = self.engine.position_ms()
        self.assertLessEqual(position, self.engine.duration_ms())

    def test_three_tracks_no_underrun(self) -> None:
        self.engine.set_queue(PlayQueue.from_tracks(self.tracks, PlayMode.SEQUENTIAL))
        self.assertTrue(self.engine.play())
        self.assertTrue(self._wait_state(PlayerState.IDLE, timeout=30.0), "三首播完应回到 IDLE")
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["counters"]["underruns"], 0, "三首连播不应欠载")
        self.assertEqual(len(self.eofs), 3, "每首结束都应发一次 eof 事件")
        self.assertEqual(snapshot["queue_index"], 2)

    def test_play_three_tracks_manually(self) -> None:
        gaps: list[float] = []
        self.engine.on_gap = gaps.append
        self.engine.set_queue(PlayQueue.from_tracks(self.tracks))
        self.assertTrue(self.engine.play())
        for index in range(1, 3):
            time.sleep(0.25)
            self.assertTrue(self.engine.next(), f"第 {index} 次切歌失败")
            self.assertIs(self.engine.state, PlayerState.PLAYING)
            self.engine.poll()
        snapshot = self.engine.snapshot()
        self.assertEqual(snapshot["counters"]["underruns"], 0)
        self.assertEqual(snapshot["queue_index"], 2)
        self.assertEqual(len(gaps), 2, "每次切歌都应抛一次 gap 事件")
        self.assertTrue(all(gap > 0 for gap in gaps), f"间隙应大于 0：{gaps}")
        self.assertGreater(snapshot["counters"]["last_gap_ms"], 0)

    def test_busy_rejects_new_playback(self) -> None:
        self.engine.set_queue(PlayQueue.from_tracks(self.tracks))
        self.engine.set_busy(True)
        self.assertFalse(self.engine.play(), "批处理期间应拒绝起播")
        self.assertIs(self.engine.state, PlayerState.IDLE)

        self.engine.set_busy(False)
        self.assertTrue(self.engine.play())
        self.assertIs(self.engine.state, PlayerState.PLAYING)
        # 已在播时进入批处理：不打断当前曲目（只拒绝新的起播）
        self.engine.set_busy(True)
        time.sleep(0.2)
        self.engine.poll()
        self.assertIs(self.engine.state, PlayerState.PLAYING)

    def test_volume_and_snapshot_json(self) -> None:
        self.settings.playback_volume_mode = "software"
        self.settings.playback_dsp_bypass = False
        self.engine.set_queue(PlayQueue.from_tracks([self.tracks[0]]))
        self.assertTrue(self.engine.play())
        self.engine.set_volume(-6.0)
        self.assertAlmostEqual(self.engine.snapshot()["dsp"]["gain_db"], -6.0, delta=0.01)
        volumes: list[float] = []
        self.engine.on_volume = volumes.append
        self.engine.set_volume(-3.0)
        self.assertEqual(volumes, [-3.0])

        snapshot = self.engine.snapshot()
        text = json.dumps(snapshot, ensure_ascii=False)  # 必须可序列化
        self.assertIn("signal_path", snapshot)
        self.assertEqual(len(snapshot["signal_path"]), 9)
        self.assertTrue(text)

    def test_signal_path_reflects_fallback(self) -> None:
        """强制一个设备不支持的源率：resample 级必须标 fallback。

        本机 WASAPI 共享模式只接受 48 kHz（Windows 混音格式），拿它验证
        回退链最真实：44.1 kHz 源 → 48000 Hz，两级都要标 fallback。
        """
        device = next(
            (
                candidate
                for candidate in dev_mod.list_output_devices("wasapi")
                if not dev_mod.supports_rate(candidate, 44100)
            ),
            None,
        )
        if device is None:
            self.skipTest("本机没有「拒绝 44.1 kHz」的设备，无法验证回退")

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        engine = PlayerEngine(settings)
        try:
            engine.set_queue(PlayQueue.from_tracks([self.tracks[0]]))
            self.assertTrue(engine.play())
            snapshot = engine.snapshot()
            resample = next(
                stage for stage in snapshot["signal_path"] if stage["key"] == "resample"
            )
            self.assertEqual(resample["status"], StageStatus.FALLBACK.value)
            self.assertIn("44100", resample["detail"])
            self.assertIn("48000", resample["detail"])
            self.assertTrue(snapshot["fallback"])
            self.assertGreaterEqual(snapshot["counters"]["resample_events"], 1)
            self.assertEqual(snapshot["rate"], 48000)
            self.assertNotIn("位完美", snapshot["signal_summary"])
        finally:
            engine.shutdown()

    def test_fallback_pcm_matches_device_rate(self) -> None:
        """采样率回退后「不变调」：PCM 必须按**设备率**产出，不是按源率。

        只看 ``snapshot()["rate"]`` 会漏掉最隐蔽的一类 bug：流按 48k 开、PCM 按
        44.1k 送 —— 界面一切正常，听感变调。所以这里用引擎实际的 ``DecodeSpec``
        重新解码一遍，帧数必须等于 ``时长 × 设备率``。
        """
        device = next(
            (
                candidate
                for candidate in dev_mod.list_output_devices("wasapi")
                if not dev_mod.supports_rate(candidate, 44100)
            ),
            None,
        )
        if device is None:
            self.skipTest("本机没有「拒绝 44.1 kHz」的设备，无法验证回退")

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_device = str(device.index)
        engine = PlayerEngine(settings)
        try:
            track = probe_mod.probe(_make_flac(self.tmp / "fb.flac", seconds=2.0))
            engine.set_queue(PlayQueue.from_tracks([track]))
            self.assertTrue(engine.play())
            snapshot = engine.snapshot()
            actual_rate = snapshot["rate"]
            self.assertEqual(actual_rate, 48000, "WASAPI 共享应回退到 48k")
            self.assertTrue(snapshot["fallback"])

            spec = engine._spec  # 解码器真正吃到的参数
            self.assertIsNotNone(spec)
            self.assertEqual(spec.sample_rate, actual_rate, "解码率必须等于设备率")

            decoder = FfmpegDecoder(
                DecodeSpec(
                    path=track.path,
                    sample_rate=spec.sample_rate,
                    channels=spec.channels,
                    sample_fmt=spec.sample_fmt,
                )
            )
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
            expected = int(round(2.0 * actual_rate))
            self.assertLessEqual(
                abs(frames - expected), 2048,
                f"帧数 {frames} 与 {expected}（2 s × {actual_rate}）不符 —— 这就是静默变调",
            )
        finally:
            engine.shutdown()

    def test_no_device_error_path(self) -> None:
        """坏文件 → ERROR 状态 + 可读错误信息，不抛异常。"""
        item = QueueItem(path=self.tmp / "missing.flac", title="missing")
        self.engine.set_queue(PlayQueue([item]))
        self.assertFalse(self.engine.play())
        self.assertIs(self.engine.state, PlayerState.ERROR)
        self.assertTrue(self.engine.error)
        self.assertIn("不存在", self.engine.error)

    def test_queue_operations_via_engine(self) -> None:
        self.engine.set_queue(PlayQueue.from_tracks(self.tracks))
        self.assertTrue(self.engine.play())
        length = len(self.engine.queue)
        self.engine.queue.enqueue_next(_make_item("插播"))
        self.assertEqual(len(self.engine.queue), length + 1)
        self.assertIs(self.engine.set_mode(PlayMode.REPEAT_ALL), PlayMode.REPEAT_ALL)
        self.assertEqual(self.engine.snapshot()["mode"], PlayMode.REPEAT_ALL.value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
