"""第 2 轮补充用例：把第 1 轮「部分完成」的 5 项尽量脚本化（对应用例见各 docstring）。

运行：``python tests/test_player_round2.py``（不需要声卡；全部走内存输出替身，
只有 5.1 直通那一条会查设备能力，查不到就按"如实回退"判定）。

覆盖：
* TC-DSP-08  A/B 快照切换：不重建解码器、不跳位置、幅度按预期变化
* TC-SES-01  会话端到端：播到 30 s → 存 → 销毁引擎 → 新引擎恢复 → 不自动播放
* TC-OUT-19  左右声道分离（客观 RMS，不靠听）
* TC-OUT-20  5.1 源：设备支持则直通，不支持则**明确报错或明确下混**，不许静默变单声道
* TC-DSP-06  爆音客观替代：切歌/暂停/seek 处相邻样本最大跳变 ≤ 0.5 FS
* TC-ENG-06  同规格切歌实测间隙（弹性项，只记录并如实判定）
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import numpy as np  # noqa: E402

from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.player.constants import PlayMode, PlayerState  # noqa: E402
from seqaudio.player.engine import PlayerEngine  # noqa: E402
from seqaudio.player.queue import PlayQueue, QueueItem  # noqa: E402
from seqaudio.player.session import PlaybackSession  # noqa: E402
from test_player_behaviour import FakeOutput  # noqa: E402

FFMPEG = find_ffmpeg()
#: 相邻样本跳变阈值（-6 dBFS 满幅阶跃即 1.0；>0.5 视为可疑爆音）
CLICK_THRESHOLD = 0.5


def _ff(args: list[str]) -> None:
    assert FFMPEG is not None
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise unittest.SkipTest(f"ffmpeg 失败：{proc.stderr.decode('utf-8', 'replace')[:200]}")


def synth_sine(target: Path, seconds: float = 1.5, freq: float = 1000.0,
               rate: int = 44100) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    _ff([
        "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate={rate}",
        "-ac", "2", "-c:a", "flac", str(target),
    ])
    return target


def synth_left_only(target: Path, seconds: float = 1.5, rate: int = 44100) -> Path:
    """左声道 440 Hz、右声道全静音（用 aevalsrc 精确到样本）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    _ff([
        "-f", "lavfi", "-i",
        f"aevalsrc=0.5*sin(2*PI*440*t)|0:d={seconds}:s={rate}",
        "-c:a", "flac", str(target),
    ])
    return target


def synth_51(target: Path, seconds: float = 1.5, rate: int = 48000) -> Path:
    """5.1（6 声道）：中置 1 kHz，其余含 LFE 给低幅噪声，便于看下混是否发生。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    _ff([
        "-f", "lavfi", "-i",
        f"aevalsrc=0|0|0.5*sin(2*PI*1000*t)|0|0|0:d={seconds}:s={rate}",
        "-c:a", "flac", str(target),
    ])
    return target


def item_for(path: Path, *, duration: float = 1.5, channels: int = 2,
             rate: int = 44100, bit_depth: int = 16, title: str = "t") -> QueueItem:
    return QueueItem(
        path=path, title=title, artist="qa", duration=duration,
        sample_rate=rate, channels=channels, bit_depth=bit_depth,
    )


def settings_for(**overrides) -> Settings:
    settings = Settings()
    settings.playback_output_backend = "mme"
    settings.playback_ramp_ms = 2
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def make_engine(settings: Settings, sink: dict) -> PlayerEngine:
    def factory(ring, device, hook):
        output = FakeOutput(ring, device, hook)
        sink["output"] = output
        return output

    return PlayerEngine(settings, output_factory=factory)


def rms_since(output, chunk_mark: int) -> float:
    """只统计 ``chunk_mark`` 之后送出的样本（避免把切换前的响段算进来）。"""
    chunks = output.captured[chunk_mark:]
    if not chunks:
        return 0.0
    return rms(np.concatenate(chunks, axis=0))


def rms(block: np.ndarray) -> float:
    if block.size == 0:
        return 0.0
    data = block.astype(np.float64)
    if block.dtype == np.int16:
        data = data / 32768.0
    elif block.dtype == np.int32:
        data = data / 2147483648.0
    return float(np.sqrt(np.mean(np.square(data))))


# ---------------------------------------------------------------- TC-DSP-08


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestAbSwitch(unittest.TestCase):
    """TC-DSP-08：A/B 切换不中断、不重建解码器、不跳位置，幅度随配置变。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_ab_"))
        # 1 kHz 正弦：EQ 在 1 kHz 段 +6 dB 应让 RMS 约翻倍，便于量化
        cls.file = synth_sine(cls.tmp / "ab.flac", seconds=3.0, freq=1000.0)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_volume_ab_switch(self) -> None:
        """A（0 dB）→ B（-6 dB）→ A：全程 PLAYING，解码器对象不变。"""
        settings = settings_for(
            playback_dsp_bypass=False,
            playback_volume_mode="software",
            playback_eq_enabled=False,
        )
        sink: dict = {}
        engine = make_engine(settings, sink)
        transitions: list[str] = []
        engine.on_state_changed = lambda state: transitions.append(state.value)
        try:
            engine.set_queue(PlayQueue([item_for(self.file, duration=3.0)]))
            self.assertTrue(engine.play())
            time.sleep(0.5)
            decoder_a = engine._decoder
            output = sink["output"]
            mark_a = len(output.captured)
            time.sleep(0.4)
            before = rms_since(output, mark_a)

            mark_b = len(output.captured)
            engine.set_volume(-6.0)
            time.sleep(0.5)  # 让切换后的块攒够，窗口里不含旧增益
            after = rms_since(output, mark_b)

            self.assertIs(engine._decoder, decoder_a, "A/B 切换不该重建解码器")
            self.assertIs(engine.state, PlayerState.PLAYING, "A/B 切换不该中断播放")
            self.assertEqual(transitions, ["LOADING", "PLAYING"], "A/B 切换不该有状态抖动")
            ratio = after / before
            self.assertAlmostEqual(ratio, 0.5012, delta=0.02,
                                   msg=f"切到 -6 dB 后幅度比 {ratio:.3f}")
            self.assertEqual(sink["output"].cb_errors, 0)

            mark_c = len(output.captured)
            engine.set_volume(0.0)
            time.sleep(0.4)
            back = rms_since(output, mark_c)
            self.assertGreater(back, after * 1.5, "切回 0 dB 后幅度应回升")
        finally:
            engine.shutdown()

    def test_eq_snapshot_ab_switch(self) -> None:
        """EQ 的 A/B 快照（两者只差 1 kHz 段）来回切：不重建、不改位置。"""
        settings = settings_for(
            playback_dsp_bypass=False,
            playback_volume_mode="bypass",
            playback_eq_enabled=True,
            playback_eq_gains=[0.0] * 31,
        )
        sink: dict = {}
        engine = make_engine(settings, sink)
        snapshot_a = [0.0] * 31
        # 1 kHz 测试音要对准 31 段表的下标 17（1/3 倍频程 20 Hz 起）
        snapshot_b = [0.0] * 17 + [6.0] + [0.0] * 13
        try:
            engine.set_queue(PlayQueue([item_for(self.file, duration=3.0)]))
            engine.set_eq_gains(snapshot_a)
            self.assertTrue(engine.play())
            time.sleep(0.5)
            decoder = engine._decoder
            output = sink["output"]
            mark_a = len(output.captured)
            time.sleep(0.4)
            quiet = rms_since(output, mark_a)
            position_before = engine.position_ms()

            mark_b = len(output.captured)
            engine.set_eq_gains(snapshot_b)
            # 重叠保留法的历史窗口是 7168 帧（44.1k 下 162 ms），
            # 切换后要等它换完再测，否则测到的是过渡态。
            time.sleep(0.7)
            loud = rms_since(output, mark_b)

            self.assertIs(engine._decoder, decoder, "EQ 快照切换不该重建解码器")
            self.assertIs(engine.state, PlayerState.PLAYING)
            self.assertGreaterEqual(engine.position_ms(), position_before,
                                    "切换后位置不该回跳")
            self.assertGreater(loud / quiet, 1.4,
                               f"1 kHz +6 dB 应明显变响（实测比 {loud / quiet:.2f}）")
            self.assertEqual(sink["output"].cb_errors, 0)
        finally:
            engine.shutdown()


# ---------------------------------------------------------------- TC-SES-01


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestSessionEndToEnd(unittest.TestCase):
    """TC-SES-01：播到 30 s → 存会话 → 销毁引擎 → 新引擎恢复 → 不自动播放。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_sess_e2e_"))
        cls.long_file = synth_sine(cls.tmp / "long.flac", seconds=40.0)
        cls.files = [synth_sine(cls.tmp / f"s{i}.flac", 2.0) for i in range(3)]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_restore_queue_position_mode_volume(self) -> None:
        sink: dict = {}
        settings = settings_for(playback_volume_mode="software",
                                playback_volume_db=-3.0)
        engine = make_engine(settings, sink)
        session_path = self.tmp / "playback_session.json"
        try:
            queue = PlayQueue(
                [item_for(self.long_file, duration=40.0)]
                + [item_for(path, duration=2.0) for path in self.files],
                mode=PlayMode.REPEAT_ALL,
            )
            engine.set_queue(queue)
            self.assertTrue(engine.play())
            time.sleep(0.4)
            self.assertTrue(engine.seek(30000), "seek 到 30 s 失败")
            engine.set_volume(-3.0)
            time.sleep(0.2)
            position = engine.position_ms()
            self.assertGreaterEqual(position, 29000, f"位置偏小：{position}")

            session = PlaybackSession.capture(engine)
            self.assertTrue(session.save(session_path))
        finally:
            engine.shutdown()

        # 新引擎（模拟重启）
        loaded = PlaybackSession.load(session_path)
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(len(loaded.tracks), 4, "队列长度没恢复")
        self.assertEqual(loaded.index, 0)
        self.assertEqual(loaded.mode, PlayMode.REPEAT_ALL.value)
        self.assertAlmostEqual(loaded.volume_db, -3.0, delta=0.01)
        self.assertLessEqual(abs(loaded.position_ms - 30000), 1000,
                             f"位置恢复 {loaded.position_ms} ms，期望 30000±1000")

        sink2: dict = {}
        engine2 = make_engine(settings_for(playback_volume_mode="software"), sink2)
        try:
            engine2.set_queue(PlayQueue(loaded.items(), mode=loaded.mode))
            self.assertIs(engine2.state, PlayerState.IDLE,
                          "恢复会话后不得自动开始播放")
            self.assertEqual(len(engine2.queue), 4)
            self.assertEqual(engine2.queue.index, 0)
        finally:
            engine2.shutdown()

    def test_restore_positioning_lands_near_saved_point(self) -> None:
        """恢复后从保存位置起播，落点误差 ≤1 s（不是从 0 开始）。"""
        sink: dict = {}
        engine = make_engine(settings_for(), sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.long_file, duration=40.0)]))
            self.assertTrue(engine.play())
            time.sleep(0.3)
            self.assertTrue(engine.seek(20000))
            saved = engine.position_ms()
            self.assertTrue(engine._start_item(engine.current_item, saved),
                            "按保存位置起播失败")
            time.sleep(0.2)
            self.assertGreaterEqual(engine.position_ms(), saved - 200,
                                    "恢复后位置落回曲首了")
        finally:
            engine.shutdown()


# ---------------------------------------------------------------- TC-OUT-19


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestChannelSeparation(unittest.TestCase):
    """TC-OUT-19：左声道 440 Hz / 右声道静音，右声道必须真的是静音。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_ch_"))
        cls.left_only = synth_left_only(cls.tmp / "left.flac", seconds=1.5)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_left_right_isolated(self) -> None:
        sink: dict = {}
        engine = make_engine(settings_for(), sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.left_only)]))
            self.assertTrue(engine.play())
            time.sleep(0.8)
            engine.poll()
            data = sink["output"].playable_samples()
            self.assertGreater(len(data), 4410, "抓到的样本太少")
            left = rms(data[:, 0])
            right = rms(data[:, 1])
            self.assertGreater(left, 0.05, f"左声道应有声（RMS {left:.4f}）")
            self.assertLess(right, max(left * 0.01, 1e-4),
                            f"右声道串音：left={left:.4f} right={right:.4f}")
        finally:
            engine.shutdown()

    def test_stereo_source_keeps_two_channels(self) -> None:
        """双声道源不能被悄悄下混成单声道（两声道内容应不同）。"""
        sink: dict = {}
        engine = make_engine(settings_for(), sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.left_only)]))
            self.assertTrue(engine.play())
            time.sleep(0.6)
            data = sink["output"].playable_samples()
            self.assertEqual(data.shape[1], 2, "输出必须是 2 声道")
            self.assertGreater(abs(rms(data[:, 0]) - rms(data[:, 1])), 0.02,
                               "两声道 RMS 相同，说明被下混了")
        finally:
            engine.shutdown()


# ---------------------------------------------------------------- TC-OUT-20


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestMultichannel(unittest.TestCase):
    """TC-OUT-20：5.1 源要么直通，要么**明确**下混，不许静默变单声道。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_51_"))
        cls.file = synth_51(cls.tmp / "six.flac", seconds=1.5)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _default_device(self):
        try:
            return dev_mod.pick_default_device()
        except Exception:  # noqa: BLE001
            return None

    def test_six_channel_source_downmix_when_unsupported(self) -> None:
        device = self._default_device()
        supports_six = (
            device is not None
            and dev_mod.supports_rate(device, 48000, channels=6)
        )
        sink: dict = {}
        settings = settings_for(playback_downmix_stereo=not supports_six)
        engine = make_engine(settings, sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.file, channels=6, rate=48000)]))
            started = engine.play()
            time.sleep(0.6)
            engine.poll()
            snapshot = engine.snapshot()

            if supports_six and not settings.playback_downmix_stereo:
                self.assertTrue(started, "设备支持 6 声道，直通应成功")
                self.assertEqual(snapshot["channels"], 6)
                return

            # 下混路径：必须明确变成 2 声道，并且真的有声音
            self.assertTrue(started, f"下混起播失败：{snapshot['error']}")
            self.assertEqual(snapshot["channels"], 2, "下混后必须是 2 声道")
            data = sink["output"].playable_samples()
            self.assertEqual(data.shape[1], 2)
            self.assertGreater(rms(data), 0.01, "下混后不该静音")
        finally:
            engine.shutdown()

    def test_no_silent_mono(self) -> None:
        """6 ch 源在 2 ch 输出上必须仍是 2 声道（左右同时有声）。"""
        sink: dict = {}
        engine = make_engine(settings_for(playback_downmix_stereo=True), sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.file, channels=6, rate=48000)]))
            self.assertTrue(engine.play())
            time.sleep(0.6)
            data = sink["output"].playable_samples()
            self.assertEqual(data.shape[1], 2)
            left, right = rms(data[:, 0]), rms(data[:, 1])
            self.assertGreater(left, 0.005, "左声道静音，可能被下混成单声道后只喂了一边")
            self.assertGreater(right, 0.005, "右声道静音（中置内容应同时进左右）")
        finally:
            engine.shutdown()


# ---------------------------------------------------------------- 爆音客观替代


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestNoClickObjective(unittest.TestCase):
    """TC-DSP-06：切歌/暂停/seek 处的相邻样本最大跳变 ≤0.5 FS。

    这是「爆音」的**客观替代**：真爆音在 44.1k 上就是一个满幅阶跃；
    听感复核仍要人耳做一次，这里只保证"没有可测的硬切"。
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_click_"))
        cls.files = [synth_sine(cls.tmp / f"c{i}.flac", 3.0, freq=440 + 220 * i)
                     for i in range(2)]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _max_jump(data: np.ndarray) -> float:
        if len(data) < 2:
            return 0.0
        return float(np.max(np.abs(np.diff(data, axis=0))))

    def test_no_click_on_track_switch(self) -> None:
        sink: dict = {}
        engine = make_engine(settings_for(), sink)
        try:
            engine.set_queue(PlayQueue([item_for(path, duration=3.0)
                                        for path in self.files]))
            self.assertTrue(engine.play())
            time.sleep(0.6)
            self.assertTrue(engine.next())
            time.sleep(0.6)
            data = sink["output"].playable_samples()
            jump = self._max_jump(data)
            self.assertLessEqual(
                jump, CLICK_THRESHOLD,
                f"切歌处相邻样本最大跳变 {jump:.3f} FS（>0.5 视为可疑爆音）",
            )
        finally:
            engine.shutdown()

    def test_no_click_on_pause_resume(self) -> None:
        sink: dict = {}
        engine = make_engine(settings_for(), sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.files[0], duration=3.0)]))
            self.assertTrue(engine.play())
            time.sleep(0.6)
            self.assertTrue(engine.pause())
            time.sleep(0.2)
            self.assertTrue(engine.play())
            time.sleep(0.4)
            jump = self._max_jump(sink["output"].playable_samples())
            self.assertLessEqual(jump, CLICK_THRESHOLD,
                                 f"暂停/恢复处最大跳变 {jump:.3f} FS")
        finally:
            engine.shutdown()

    def test_no_click_on_seek(self) -> None:
        sink: dict = {}
        engine = make_engine(settings_for(), sink)
        try:
            engine.set_queue(PlayQueue([item_for(self.files[0], duration=3.0)]))
            self.assertTrue(engine.play())
            time.sleep(0.5)
            self.assertTrue(engine.seek(1500))
            time.sleep(0.5)
            jump = self._max_jump(sink["output"].playable_samples())
            self.assertLessEqual(jump, CLICK_THRESHOLD,
                                 f"seek 处最大跳变 {jump:.3f} FS")
        finally:
            engine.shutdown()


# ---------------------------------------------------------------- TC-ENG-06


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestSwitchGap(unittest.TestCase):
    """TC-ENG-06：同规格切歌实测间隙（弹性项 FR-PL-003，只如实判定）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_gap_"))
        # 同规格：44.1k/16/2ch
        cls.files = [synth_sine(cls.tmp / f"g{i}.flac", 2.0) for i in range(2)]

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_same_spec_switch_gap_recorded(self) -> None:
        sink: dict = {}
        gaps: list[float] = []
        engine = make_engine(settings_for(), sink)
        engine.on_gap = gaps.append
        try:
            engine.set_queue(PlayQueue([item_for(path, duration=2.0)
                                        for path in self.files]))
            self.assertTrue(engine.play())
            time.sleep(0.5)
            self.assertTrue(engine.next())
            time.sleep(0.3)
            self.assertTrue(gaps, "切歌必须抛 gap 事件（否则无法如实展示间隙）")
            snapshot = engine.snapshot()
            measured = snapshot["counters"]["last_gap_ms"]
            self.assertGreater(measured, 0.0, "间隙必须被记录")
            print(f"    同规格切歌实测间隙：{measured:.0f} ms"
                  f"（弹性目标 ≤5 ms，本期不承诺无缝）")
            # 本期实现明确"同规格也重建"，故只断言"有记录、不崩"，
            # 是否 ≤5 ms 记在报告的弹性项里，不在此判红。
            self.assertLess(measured, 2000.0, f"间隙 {measured} ms 异常大")
        finally:
            engine.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
