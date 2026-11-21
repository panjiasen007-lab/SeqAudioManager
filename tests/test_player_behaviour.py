"""C/D/E 段：状态机、DSP 组合矩阵、配置与会话（对应用例 TC-ENG-* / TC-DSP-* / TC-SES-*）。

运行：``python tests/test_player_behaviour.py``（**不需要声卡**）。

为什么自带一个 ``FakeOutput``
-----------------------------
本文件要跑 16 组「整数通路 × DSP」组合，每组都要**测实际送到设备的样本**。
真开流既慢又无法取回样本，所以用内存替身：它按真实速率从环形缓冲取块、
**完全按生产回调的顺序**调用引擎给的 hook、并保存取到的样本用于算 RMS。
引擎决定「要不要给 hook」的逻辑（``dtype == float32`` 才给）因此被原样保留 ——
这正是要验的那个点。
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
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player.constants import PlayMode, PlayerState, StageStatus  # noqa: E402
from seqaudio.player.engine import PlayerEngine  # noqa: E402
from seqaudio.player.queue import PlayQueue, QueueItem  # noqa: E402
from seqaudio.player.session import (  # noqa: E402
    SCHEMA_VERSION,
    PlaybackSession,
    save_session,
)
from seqaudio.player.signal_path import TEXT_INT_PIPELINE_UNAVAILABLE  # noqa: E402

FFMPEG = find_ffmpeg()


# ---------------------------------------------------------------- 测试替身


class FakeOutput:
    """``AudioOutput`` 的内存替身：按真实速率取块，回调顺序与生产一致。"""

    def __init__(self, ring, device=None, hook=None, *, block: int = 1024) -> None:
        self.ring = ring
        self.hook = hook
        self._block = int(block)
        self._device = getattr(device, "label", "") if device is not None else ""
        self._backend = getattr(device, "backend_key", "") if device is not None else ""
        self._rate = 0
        self._channels = int(ring.channels)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._paused = threading.Event()
        self._lock = threading.Lock()

        self.frames_out = 0
        self.underruns = 0
        self.cb_errors = 0
        self.last_error = ""
        self.captured: list[np.ndarray] = []

    # ---- AudioOutput 契约 ----
    def start(self, rate: int, channels: int = 0) -> int:
        self._rate = int(rate)
        if channels:
            self._channels = int(channels)
        self._stop.clear()
        self._paused.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self._rate

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=2.0)

    def pause(self) -> None:
        self._paused.set()

    def resume(self) -> None:
        self._paused.clear()

    def device_format(self) -> dict:
        return {"rate": self._rate, "channels": self._channels,
                "dtype": str(self.ring.dtype), "device": self._device}

    def snapshot(self) -> dict:
        return {
            "is_open": self._thread is not None,
            "rate": self._rate,
            "want_rate": self._rate,
            "fallback": False,
            "channels": self._channels,
            "dtype": str(self.ring.dtype),
            "device": self._device or "FakeOutput",
            "device_index": 0,
            "backend": self._backend or "mme",
            "exclusive": False,
            "blocksize": self._block,
            "latency_ms": 10.0,
            "underruns": self.underruns,
            "frames_out": self.frames_out,
            "pa_flags": 0,
            "cb_errors": self.cb_errors,
            "cb_error": self.last_error,
            "paused": self._paused.is_set(),
            "volume_db": 0.0,
            "error": "",
        }

    # ---- 内部 ----
    def _run(self) -> None:
        buf = np.zeros((self._block, self._channels), dtype=self.ring.dtype)
        period = self._block / float(self._rate or 44100)
        deadline = time.monotonic()
        while not self._stop.is_set():
            if self._paused.is_set():
                time.sleep(0.005)
                deadline = time.monotonic()
                continue
            filled = self.ring.read_into(buf)
            if filled < self._block:
                self.underruns += 1
                buf[filled:] = 0
            hook = self.hook
            if hook is not None and filled > 0:
                try:
                    hook(buf, filled)
                except BaseException as exc:  # noqa: BLE001 - 与生产回调同样兜底
                    self.cb_errors += 1
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    buf.fill(0)
            if filled > 0:
                with self._lock:
                    self.captured.append(buf[:filled].copy())
            self.frames_out += self._block
            deadline += period
            delay = deadline - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            else:
                deadline = time.monotonic()

    # ---- 读数 ----
    def playable_samples(self) -> np.ndarray:
        """取已「送出」的样本，统一转成 float32 ``(frames, channels)``。"""
        with self._lock:
            chunks = list(self.captured)
        if not chunks:
            return np.zeros((0, self._channels), dtype=np.float32)
        data = np.concatenate(chunks, axis=0)
        if data.dtype == np.int16:
            return data.astype(np.float32) / 32768.0
        if data.dtype == np.int32:
            return data.astype(np.float32) / 2147483648.0
        return data.astype(np.float32, copy=False)

    def rms(self) -> float:
        data = self.playable_samples()
        if data.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(data))))


# ---------------------------------------------------------------- 素材


def synth_flac(target: Path, seconds: float = 1.5, rate: int = 44100) -> Path:
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


def make_engine(settings: Settings, sink: dict) -> PlayerEngine:
    """建一个把输出换成 FakeOutput 的引擎；``sink`` 收到替身实例。"""

    def factory(ring, device, hook):
        output = FakeOutput(ring, device, hook)
        sink["output"] = output
        return output

    return PlayerEngine(settings, output_factory=factory)


def item_for(
    path: Path, title: str = "t", bit_depth: int = 16, duration: float = 1.5
) -> QueueItem:
    return QueueItem(
        path=path, title=title, artist="qa", duration=duration,
        sample_rate=44100, channels=2, bit_depth=bit_depth,
    )


# ---------------------------------------------------------------- D 段矩阵


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestIntPipelineDspMatrix(unittest.TestCase):
    """TC-DSP-11：``int_pipeline × dsp_bypass × volume_mode × eq_enabled`` 16 组。

    每组都要求：**有声音**（RMS > 1e-6）、``cb_errors == 0``、``underruns == 0``，
    并且**信号路径声明的事必须真的发生**（面板说衰减 6 dB，实测就得低 6 dB）。
    """

    PLAY_SECONDS = 0.8

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_matrix_"))
        cls.file = synth_flac(cls.tmp / "m.flac", seconds=1.5, rate=44100)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _play(self, *, int_pipeline: bool, dsp_bypass: bool,
              volume_mode: str, eq_enabled: bool, bit_depth: int = 16) -> dict:
        settings = Settings()
        settings.playback_output_backend = "mme"
        settings.playback_ramp_ms = 2
        settings.playback_int_pipeline = int_pipeline
        settings.playback_dsp_bypass = dsp_bypass
        settings.playback_volume_mode = volume_mode
        settings.playback_volume_db = -6.0 if volume_mode == "software" else 0.0
        settings.playback_eq_enabled = eq_enabled
        settings.playback_eq_gains = [0.0] * 31  # 全平直：不该改变幅度

        sink: dict = {}
        engine = make_engine(settings, sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.file, bit_depth=bit_depth)]))
            self.assertTrue(engine.play(), "起播失败（组合见 name 参数）")
            # 暖机 0.3 s：Python 替身的启动抖动会制造 1~2 次假欠载，
            # 产品级的真欠载由 test_player_core 的真设备用例负责。
            time.sleep(0.3)
            engine.poll()
            warm = int(engine.snapshot()["counters"]["underruns"])
            time.sleep(self.PLAY_SECONDS)
            engine.poll()
            snapshot = engine.snapshot()
            output: FakeOutput = sink["output"]
            underruns_in_window = int(snapshot["counters"]["underruns"]) - warm
            gain_stage = next(
                stage for stage in snapshot["signal_path"] if stage["key"] == "gain"
            )
            return {
                "rms": output.rms(),
                "cb_errors": output.cb_errors,
                "cb_error": output.last_error,
                "underruns": underruns_in_window,
                "dtype": snapshot["signal_path"][2]["detail"],
                "gain_status": gain_stage["status"],
                "gain_detail": gain_stage["detail"],
                "state": snapshot["state"],
            }
        finally:
            engine.shutdown()

    def test_matrix(self) -> None:
        baseline = self._play(
            int_pipeline=False, dsp_bypass=True, volume_mode="bypass", eq_enabled=False
        )
        self.assertGreater(baseline["rms"], 1e-6, "基线都没声音，后面无从比较")
        reference = baseline["rms"]

        problems: list[str] = []
        for int_pipeline in (False, True):
            for dsp_bypass in (True, False):
                for volume_mode in ("bypass", "software"):
                    for eq_enabled in (False, True):
                        name = (f"int_pipeline={int_pipeline} dsp_bypass={dsp_bypass} "
                                f"volume={volume_mode} eq={eq_enabled}")
                        with self.subTest(name=name):
                            result = self._play(
                                int_pipeline=int_pipeline, dsp_bypass=dsp_bypass,
                                volume_mode=volume_mode, eq_enabled=eq_enabled,
                            )
                            # ① 必须出声
                            if result["rms"] < 1e-6:
                                problems.append(f"{name}: 静音（RMS={result['rms']:.2e}）")
                                continue
                            # ② 回调不许报错、不许欠载
                            if result["cb_errors"]:
                                problems.append(
                                    f"{name}: 回调报错 {result['cb_errors']} 次"
                                    f"（{result['cb_error']}）"
                                )
                            if result["underruns"]:
                                problems.append(f"{name}: 欠载 {result['underruns']} 次")

                            ratio = result["rms"] / reference
                            # 软件音量是否改样本，只由 volume_mode 决定，与 dsp_bypass
                            # 无关（引擎 set_volume 从不看 bypass）。v1.8 修的就是
                            # "以为 bypass 会挡音量"这个错误模型：它让 int16 缓冲里
                            # 做了浮点乘，回调抛异常后整场静音。
                            # 本矩阵里 software 档固定 -6 dB（见 _play），必然改样本。
                            software_gain = volume_mode == "software"
                            # ④ 「整数通路被 DSP 挤掉」必须在格式级说清原因，
                            #    否则用户以为自己在跑位完美路径。
                            will_process = software_gain or (not dsp_bypass and eq_enabled)
                            expect_downgrade = int_pipeline and will_process
                            detail = result["dtype"]
                            if expect_downgrade:
                                if TEXT_INT_PIPELINE_UNAVAILABLE not in detail:
                                    problems.append(
                                        f"{name}: 整数通路已被降级，格式级却只写 "
                                        f"『{detail}』，没说原因"
                                    )
                            elif int_pipeline:
                                if "s16" not in detail.lower():
                                    problems.append(
                                        f"{name}: DSP 全关时应保留整数通路，实际格式级 "
                                        f"『{detail}』"
                                    )
                                elif TEXT_INT_PIPELINE_UNAVAILABLE in detail:
                                    problems.append(
                                        f"{name}: 整数通路保留着，却标注『不可用』"
                                    )
                            gain_claimed = result["gain_status"] == StageStatus.CHANGED.value
                            # ③ 信号路径说改了，就必须真的改了
                            if gain_claimed:
                                if abs(ratio - 0.5012) > 0.05:
                                    problems.append(
                                        f"{name}: 面板称『{result['gain_detail']}』，"
                                        f"实测幅度比 {ratio:.3f}（应≈0.501）→ 声明与事实不符"
                                    )
                            elif software_gain:
                                problems.append(
                                    f"{name}: 软件音量 -6 dB 已启用，面板却标 "
                                    f"{result['gain_status']}（{result['gain_detail']}）"
                                )
                            else:
                                if abs(ratio - 1.0) > 0.03:
                                    problems.append(
                                        f"{name}: 全旁路时幅度比 {ratio:.3f}，应≈1.0"
                                    )

        self.assertEqual(problems, [], "组合矩阵发现问题：\n  " + "\n  ".join(problems))

    def test_int_pipeline_dtype_is_actually_int(self) -> None:
        """整数通路必须真的走整数 dtype（否则「整数通路」只是文案）。"""
        result = self._play(
            int_pipeline=True, dsp_bypass=True, volume_mode="bypass", eq_enabled=False
        )
        self.assertIn("s16", result["dtype"].lower(), f"格式级详情：{result['dtype']}")

    def test_float_path_by_default(self) -> None:
        """TC-DSP-05：默认（非整数通路）走浮点，信号路径全 passthrough。"""
        result = self._play(
            int_pipeline=False, dsp_bypass=True, volume_mode="bypass", eq_enabled=False
        )
        self.assertIn("f32", result["dtype"].lower(), f"格式级详情：{result['dtype']}")
        self.assertEqual(result["gain_status"], StageStatus.BYPASSED.value)


# ---------------------------------------------------------------- C 段状态机


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestIllegalCalls(unittest.TestCase):
    """TC-ENG-10：非法/重复调用必须幂等（不崩、不抛、状态不越界）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_illegal_"))
        cls.files = [synth_flac(cls.tmp / f"i{i}.flac", 1.2) for i in range(2)]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _engine(self) -> tuple[PlayerEngine, dict]:
        settings = Settings()
        settings.playback_output_backend = "mme"
        settings.playback_ramp_ms = 1
        settings.playback_mode = PlayMode.SEQUENTIAL.value
        sink: dict = {}
        return make_engine(settings, sink), sink

    def test_idle_illegal_calls(self) -> None:
        engine, _ = self._engine()
        try:
            for _ in range(3):
                self.assertFalse(engine.pause(), "IDLE 时 pause 应返回 False")
                self.assertFalse(engine.seek(1000), "IDLE 时 seek 应返回 False")
                self.assertFalse(engine.next(), "IDLE 时 next 应返回 False")
                self.assertFalse(engine.previous(), "IDLE 时 previous 应返回 False")
                self.assertTrue(engine.stop(), "IDLE 时 stop 应幂等成功")
            self.assertIs(engine.state, PlayerState.IDLE)
            self.assertEqual(engine.position_ms(), 0)
            self.assertEqual(engine.queue.index, -1)
        finally:
            engine.shutdown()

    def test_empty_queue_calls(self) -> None:
        engine, _ = self._engine()
        try:
            self.assertFalse(engine.play(), "空队列 play 应返回 False")
            self.assertIs(engine.state, PlayerState.IDLE)
            self.assertIsNone(engine.queue.next(auto_advance=True))
            self.assertIsNone(engine.queue.previous())
            self.assertIsNone(engine.queue.jump_to(0))
            self.assertIsNone(engine.queue.remove_at(0))
        finally:
            engine.shutdown()

    def test_pause_stop_repeat_while_playing(self) -> None:
        engine, _ = self._engine()
        try:
            engine.set_queue(PlayQueue([item_for(self.files[0])]))
            self.assertTrue(engine.play())
            for _ in range(3):
                engine.pause()
            self.assertIs(engine.state, PlayerState.PAUSED)
            for _ in range(3):
                engine.stop()
            self.assertIs(engine.state, PlayerState.IDLE)
            self.assertEqual(engine.position_ms(), 0)
        finally:
            engine.shutdown()

    def test_seek_extremes(self) -> None:
        """负值与超大值都要被钳住，且不崩。

        注意 ``duration`` 必须与文件真实长度一致（1.2 s）：如果元数据把时长
        估长了（R7：无索引 VBR），seek 会落到真实 EOF 之后，此时引擎报的是
        「可能是不支持的格式」—— 这条已知限制单独记在测试报告里，不在这里判红。
        """
        engine, _ = self._engine()
        try:
            engine.set_queue(PlayQueue([item_for(self.files[0], duration=1.2)]))
            self.assertTrue(engine.play())
            self.assertTrue(engine.seek(-5000))
            self.assertGreaterEqual(engine.position_ms(), 0)
            engine.seek(10 ** 9)
            self.assertLessEqual(engine.position_ms(), engine.duration_ms())
            # 拖到最右端等于「到结尾」，此时不该变成「不支持的格式」：
            # 合法结果是停住（IDLE）或继续待在 PLAYING，绝不是 ERROR + 误导文案。
            self.assertIn(engine.state, (PlayerState.PLAYING, PlayerState.IDLE),
                          f"seek 到曲末进入 {engine.state.value}，错误：{engine.error}")
            self.assertNotIn("不支持", engine.error)
        finally:
            engine.shutdown()

    def test_shutdown_twice(self) -> None:
        engine, _ = self._engine()
        engine.set_queue(PlayQueue([item_for(self.files[0])]))
        engine.play()
        engine.shutdown()
        engine.shutdown()  # 再关一次不许抛

    def test_callbacks_that_raise_do_not_break_playback(self) -> None:
        engine, _ = self._engine()
        engine.on_state_changed = lambda *_: (_ for _ in ()).throw(RuntimeError("ui bug"))
        try:
            engine.set_queue(PlayQueue([item_for(self.files[0])]))
            self.assertTrue(engine.play(), "界面回调抛异常不该影响起播")
            self.assertIs(engine.state, PlayerState.PLAYING)
        finally:
            engine.shutdown()


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestPositionClock(unittest.TestCase):
    """TC-ENG-12 / 13：位置单调、与墙钟一致、暂停冻结、seek 后不跳回。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_pos_"))
        cls.file = synth_flac(cls.tmp / "p.flac", seconds=3.0)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_position_tracks_wall_clock(self) -> None:
        settings = Settings()
        settings.playback_output_backend = "mme"
        settings.playback_ramp_ms = 1
        sink: dict = {}
        engine = make_engine(settings, sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.file, duration=3.0)]))
            self.assertTrue(engine.play())
            started = time.monotonic()
            samples: list[int] = []
            while time.monotonic() - started < 1.2:
                engine.poll()
                samples.append(engine.position_ms())
                time.sleep(0.05)
            elapsed_ms = (time.monotonic() - started) * 1000.0
            self.assertEqual(samples, sorted(samples), "位置不该倒退")
            self.assertLessEqual(
                abs(samples[-1] - elapsed_ms), 300,
                f"位置 {samples[-1]} ms 与墙钟 {elapsed_ms:.0f} ms 偏差过大（时钟漂移）",
            )
        finally:
            engine.shutdown()

    def test_pause_freezes_position(self) -> None:
        settings = Settings()
        settings.playback_output_backend = "mme"
        settings.playback_ramp_ms = 1
        sink: dict = {}
        engine = make_engine(settings, sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.file, duration=3.0)]))
            self.assertTrue(engine.play())
            time.sleep(0.4)
            self.assertTrue(engine.pause())
            time.sleep(0.1)
            frozen = engine.position_ms()
            time.sleep(0.3)
            self.assertEqual(engine.position_ms(), frozen, "暂停时位置必须冻结")
            self.assertTrue(engine.play())
            time.sleep(0.2)
            self.assertGreater(engine.position_ms(), frozen, "恢复后位置应继续走")
        finally:
            engine.shutdown()

    def test_seek_does_not_jump_back(self) -> None:
        settings = Settings()
        settings.playback_output_backend = "mme"
        settings.playback_ramp_ms = 1
        sink: dict = {}
        engine = make_engine(settings, sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.file, duration=3.0)]))
            self.assertTrue(engine.play())
            time.sleep(0.3)
            self.assertTrue(engine.seek(2000))
            positions = []
            for _ in range(8):
                engine.poll()
                positions.append(engine.position_ms())
                time.sleep(0.05)
            self.assertGreaterEqual(min(positions), 1900,
                                    f"seek 到 2000 ms 后位置回跳：{positions}")
            self.assertEqual(positions, sorted(positions), "seek 后位置应继续单调")
        finally:
            engine.shutdown()


# ---------------------------------------------------------------- C 段错误路径


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestEngineBadInput(unittest.TestCase):
    """TC-DEC-25 / 26 / 27 的引擎层断言：坏输入必须进 ERROR 且有可读原因。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_bad_engine_"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _play_file(self, path: Path) -> tuple[bool, dict]:
        settings = Settings()
        settings.playback_output_backend = "mme"
        settings.playback_ramp_ms = 1
        sink: dict = {}
        engine = make_engine(settings, sink)
        try:
            engine.set_queue(PlayQueue([item_for(path)]))
            ok = engine.play()
            time.sleep(0.2)
            engine.poll()
            return ok, engine.snapshot()
        finally:
            engine.shutdown()

    def test_zero_byte_file_goes_to_error(self) -> None:
        empty = self.tmp / "empty.flac"
        empty.write_bytes(b"")
        ok, snapshot = self._play_file(empty)
        self.assertFalse(ok)
        self.assertEqual(snapshot["state"], PlayerState.ERROR.value)
        self.assertTrue(snapshot["error"], "必须给出可读错误原因")

    def test_junk_file_goes_to_error(self) -> None:
        junk = self.tmp / "junk.flac"
        junk.write_bytes(b"not audio\n" * 400)
        ok, snapshot = self._play_file(junk)
        self.assertFalse(ok)
        self.assertEqual(snapshot["state"], PlayerState.ERROR.value)
        self.assertTrue(snapshot["error"])

    def test_missing_file_goes_to_error(self) -> None:
        ok, snapshot = self._play_file(self.tmp / "nope.flac")
        self.assertFalse(ok)
        self.assertEqual(snapshot["state"], PlayerState.ERROR.value)
        self.assertIn("不存在", snapshot["error"])


# ---------------------------------------------------------------- E 段配置


class TestConfig(unittest.TestCase):
    """TC-SES-04 / 05：配置默认值与全量往返。"""

    #: 架构文档第七节的 34 个新字段（决策：默认后端 mme）
    EXPECTED = {
        "playback_output_backend": "mme",
        "playback_exclusive": False,
        "playback_follow_source_rate": True,
        "playback_dsd_pcm_rate": 176400,
        "playback_bit_perfect": False,
        "playback_volume_mode": "bypass",
        "playback_dsp_bypass": True,
        "playback_eq_enabled": False,
        "playback_gapless": True,
        "playback_crossfade_sec": 0.0,
        "playback_restore_session": True,
        "playback_restore_position": True,
        "playback_downmix_stereo": False,
        "playback_int_pipeline": False,
        "library_autoload": True,
    }

    def test_decided_defaults(self) -> None:
        """主理人拍板的默认值必须落地（默认后端 = mme）。"""
        settings = Settings()
        for name, expected in self.EXPECTED.items():
            with self.subTest(field=name):
                self.assertEqual(getattr(settings, name), expected,
                                 f"{name} 默认值应为 {expected!r}")

    #: 取值受限的播放字段：往返测试不能用「+1 / +_qa」这种无意义值，
    #: 因为 ``Settings.from_dict`` 会把越界值折回合法值（v1.8 起有意为之：
    #: 手改的配置文件必须收敛，否则会静音或爆音）。这里给每个字段一个
    #: **合法但不同**的替身，测的仍是"存得下、读得回"。
    _VALID_ALTERNATES = {
        "playback_output_mode": "exclusive",
        "playback_volume_mode": "software",
        "playback_dsd_pcm_rate": 352800,
        "playback_ramp_ms": 3,
        "playback_mode": "shuffle",
    }

    def test_roundtrip_all_playback_fields(self) -> None:
        """全部 playback_* + 3 个 library_* 改值后往返一致。"""
        import dataclasses

        tmp = Path(tempfile.mkdtemp(prefix="seq_cfg_"))
        try:
            target = tmp / "config.json"
            settings = Settings()
            fields = [
                field
                for field in dataclasses.fields(Settings)
                if field.name.startswith(("playback_", "library_"))
            ]
            for field in fields:
                current = getattr(settings, field.name)
                if field.name in self._VALID_ALTERNATES:
                    setattr(settings, field.name, self._VALID_ALTERNATES[field.name])
                elif isinstance(current, bool):
                    setattr(settings, field.name, not current)
                elif isinstance(current, int):
                    setattr(settings, field.name, current + 1)
                elif isinstance(current, float):
                    setattr(settings, field.name, current + 1.5)
                elif isinstance(current, str):
                    setattr(settings, field.name, current + "_qa")
                elif isinstance(current, list):
                    if field.name in Settings.EQ_GAIN_FIELDS:
                        # EQ 曲线长度被段数锁死（v1.8 = 31 段）：改值而不是加长，
                        # 否则读回时会被 from_dict 截断成 31（这是有意的归一化）
                        setattr(
                            settings,
                            field.name,
                            [float(index) for index in range(Settings.EQ_BAND_COUNT)],
                        )
                    else:
                        setattr(settings, field.name, list(current) + [1.0])
            settings.save(target)
            loaded = Settings.load(target)
            for field in fields:
                with self.subTest(field=field.name):
                    self.assertEqual(getattr(loaded, field.name),
                                     getattr(settings, field.name))
            self.assertIsNot(loaded.playback_eq_gains, settings.playback_eq_gains,
                             "列表字段不能共享引用")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_legacy_config_gets_new_defaults(self) -> None:
        """TC-SES-04：老配置（没有 playback_*）加载后取新默认值。"""
        tmp = Path(tempfile.mkdtemp(prefix="seq_legacy_"))
        try:
            legacy = tmp / "config.json"
            legacy.write_text(
                '{"language": "en", "conflict_mode": "强制覆盖", "unknown_key": 1}',
                encoding="utf-8",
            )
            settings = Settings.load(legacy)
            self.assertEqual(settings.language, "en")
            self.assertEqual(settings.playback_output_backend, "mme")
            self.assertTrue(settings.playback_restore_session)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- E 段会话


class TestSessionBulk(unittest.TestCase):
    """TC-SES-02 / 03 / 06：版本、损坏、500 项往返。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_sess2_"))
        self.path = self.tmp / "playback_session.json"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_unknown_version_returns_none(self) -> None:
        self.path.write_text(
            '{"version": 999, "tracks": [{"path": "D:/a.flac"}]}', encoding="utf-8"
        )
        self.assertIsNone(PlaybackSession.load(self.path))

    def test_corrupt_json_returns_none(self) -> None:
        self.path.write_text("{ 这不是 JSON", encoding="utf-8")
        self.assertIsNone(PlaybackSession.load(self.path))

    def test_empty_file_returns_none(self) -> None:
        self.path.write_text("", encoding="utf-8")
        self.assertIsNone(PlaybackSession.load(self.path))

    def test_five_hundred_items_roundtrip(self) -> None:
        items = [f"D:/music/track{index:04d}.flac" for index in range(500)]
        started = time.monotonic()
        self.assertTrue(
            save_session(items, index=137, position_ms=83456,
                         mode=PlayMode.SHUFFLE, output_device="扬声器",
                         volume_db=-6.0, output_backend="mme", path=self.path)
        )
        elapsed = time.monotonic() - started
        loaded = PlaybackSession.load(self.path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded.tracks), 500)
        self.assertEqual(loaded.index, 137)
        self.assertEqual(loaded.position_ms, 83456)
        self.assertEqual(loaded.mode, PlayMode.SHUFFLE.value)
        self.assertLess(elapsed, 1.0, f"500 项保存耗时 {elapsed:.2f}s")
        self.assertLess(self.path.stat().st_size, 1 << 20)

    def test_version_constant(self) -> None:
        self.assertEqual(SCHEMA_VERSION, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
