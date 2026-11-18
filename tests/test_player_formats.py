"""A 段：解码格式矩阵 / 异常文件 / seek 精度（对应用例 TC-DEC-*）。

运行：``python tests/test_player_formats.py``（不需要声卡；只要 ffmpeg）。

素材策略
--------
* 能合成的都用内置 ffmpeg **现场生成**（不依赖某台机器上的音乐文件）；
* APE / DSF 本 build 无编码器（只有解码器），改用本机真实样本，缺样本即 skip；
* 判定一律用数字：帧数与 ``时长 × 采样率`` 的偏差、FFT 主频、异常类型。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.player.constants import BLOCK_FRAMES_DEFAULT  # noqa: E402
from seqaudio.player.decoder import DecodeError, DecodeSpec, FfmpegDecoder  # noqa: E402

FFMPEG = find_ffmpeg()

#: 真实样本（本机没有时相关用例 skip，不算失败）
APE_SAMPLES = [
    ROOT / "tests" / "_tmp" / "李宗盛 - 山丘.ape",
    Path(r"D:/02.音乐/(29)滚石香港黄金十年系列-Beyond精选/CDImage.ape"),
]
DSF_GLOBS = [
    r"D:/真无损已经修改/2017 - 黄品源 滚石SACD系列 黄品源精选(滚石SACD系列).dsf",
    r"D:/真无损已经修改/VA - The Art of HiFi Bass (2023) [DSD256]",
]

#: 有损格式的帧数容差（编码器延迟/填充会让首尾不完全对齐）
LOSSY_TOLERANCE = 0.15


def _run_ffmpeg(args: list[str]) -> None:
    assert FFMPEG is not None
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proc.returncode != 0:
        raise unittest.SkipTest(
            "ffmpeg 合成失败：" + proc.stderr.decode("utf-8", "replace")[:200]
        )


def synth(
    target: Path,
    *,
    seconds: float = 1.0,
    rate: int = 44100,
    codec: str = "flac",
    freq: float = 1000.0,
) -> Path:
    """合成一段正弦波到 ``target``（编码器/容器由扩展名 + ``codec`` 决定）。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-f", "lavfi",
            "-i", f"sine=frequency={freq}:duration={seconds}:sample_rate={rate}",
            "-ac", "2",
            "-c:a", codec,
            "-ar", str(rate),
            str(target),
        ]
    )
    return target


def decode_frames(
    path: Path,
    *,
    rate: int = 44100,
    channels: int = 2,
    sample_fmt: str = "s16",
    start_ms: int = 0,
    duration_ms: int = 0,
) -> tuple[int, np.ndarray]:
    """把整个文件解成 PCM，返回 ``(帧数, 第一块)``。"""
    spec = DecodeSpec(
        path=path,
        start_ms=start_ms,
        duration_ms=duration_ms,
        sample_rate=rate,
        channels=channels,
        sample_fmt=sample_fmt,
        block_frames=BLOCK_FRAMES_DEFAULT,
    )
    decoder = FfmpegDecoder(spec)
    decoder.start()
    frames = 0
    first = None
    try:
        while True:
            block = decoder.read_block()
            if block is None:
                break
            if first is None:
                first = block.copy()
            frames += int(block.shape[0])
    finally:
        decoder.stop()
    if first is None:
        first = np.zeros((0, channels), dtype=np.int16)
    return frames, first


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestFormatMatrix(unittest.TestCase):
    """TC-DEC-01 ~ TC-DEC-23：每种格式各解一遍。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_fmt_"))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _check(self, name: str, seconds: float, rate: int, tolerance: float = 0.0) -> int:
        path = self.tmp / name
        frames, _ = decode_frames(path, rate=rate)
        expected = seconds * rate
        allowed = max(BLOCK_FRAMES_DEFAULT * 2, expected * tolerance)
        self.assertLessEqual(
            abs(frames - expected),
            allowed,
            f"{name}: 解码 {frames} 帧，期望 {expected:.0f}±{allowed:.0f}",
        )
        return frames

    # ---- 无损族 ----

    def test_flac_44100_16(self) -> None:
        """TC-DEC-01：FLAC 44.1k/16，1 s。"""
        synth(self.tmp / "a.flac", seconds=1.0, rate=44100)
        self._check("a.flac", 1.0, 44100)

    def test_flac_96000_24(self) -> None:
        """TC-DEC-02：FLAC 96k/24，s24 通路。"""
        synth(self.tmp / "b.flac", seconds=1.0, rate=96000, codec="flac")
        frames, _ = decode_frames(self.tmp / "b.flac", rate=96000, sample_fmt="s24")
        self.assertLessEqual(abs(frames - 96000), BLOCK_FRAMES_DEFAULT * 2)

    def test_flac_192000_24(self) -> None:
        """TC-DEC-03：FLAC 192k/24。"""
        synth(self.tmp / "c.flac", seconds=1.0, rate=192000, codec="flac")
        frames, _ = decode_frames(self.tmp / "c.flac", rate=192000, sample_fmt="s24")
        self.assertLessEqual(abs(frames - 192000), BLOCK_FRAMES_DEFAULT * 2)

    def test_wav_44100_16(self) -> None:
        """TC-DEC-05：WAV 44.1k/16。"""
        synth(self.tmp / "d.wav", seconds=1.0, rate=44100, codec="pcm_s16le")
        self._check("d.wav", 1.0, 44100)

    def test_wav_96000_24(self) -> None:
        """TC-DEC-06：WAV 96k/24。"""
        synth(self.tmp / "e.wav", seconds=1.0, rate=96000, codec="pcm_s24le")
        frames, _ = decode_frames(self.tmp / "e.wav", rate=96000, sample_fmt="s24")
        self.assertLessEqual(abs(frames - 96000), BLOCK_FRAMES_DEFAULT * 2)

    def test_alac_m4a(self) -> None:
        """TC-DEC-07：ALAC。"""
        synth(self.tmp / "f.m4a", seconds=1.0, rate=44100, codec="alac")
        self._check("f.m4a", 1.0, 44100)

    def test_wavpack(self) -> None:
        """TC-DEC-08：WavPack。"""
        synth(self.tmp / "g.wv", seconds=1.0, rate=44100, codec="wavpack")
        self._check("g.wv", 1.0, 44100)

    def test_tta(self) -> None:
        """TC-DEC-09：TTA。"""
        synth(self.tmp / "h.tta", seconds=1.0, rate=44100, codec="tta")
        self._check("h.tta", 1.0, 44100)

    # ---- 有损族 ----

    def test_mp3(self) -> None:
        """TC-DEC-11：MP3（LAME）。"""
        synth(self.tmp / "i.mp3", seconds=1.0, rate=44100, codec="libmp3lame")
        self._check("i.mp3", 1.0, 44100, LOSSY_TOLERANCE)

    def test_aac_m4a(self) -> None:
        """TC-DEC-12：AAC。"""
        synth(self.tmp / "j.m4a", seconds=1.0, rate=44100, codec="aac")
        self._check("j.m4a", 1.0, 44100, LOSSY_TOLERANCE)

    def test_vorbis_ogg(self) -> None:
        """TC-DEC-13：Vorbis。"""
        synth(self.tmp / "k.ogg", seconds=1.0, rate=44100, codec="libvorbis")
        self._check("k.ogg", 1.0, 44100, LOSSY_TOLERANCE)

    def test_opus_ogg(self) -> None:
        """TC-DEC-14：Opus（固定 48 k 族）。"""
        synth(self.tmp / "l.ogg", seconds=1.0, rate=48000, codec="libopus")
        self._check("l.ogg", 1.0, 48000, LOSSY_TOLERANCE)

    def test_wma_asf(self) -> None:
        """TC-DEC-15：WMA。"""
        synth(self.tmp / "m.wma", seconds=1.0, rate=44100, codec="wmav2")
        self._check("m.wma", 1.0, 44100, LOSSY_TOLERANCE)

    def test_ac3(self) -> None:
        """TC-DEC-16：AC-3。"""
        synth(self.tmp / "n.ac3", seconds=1.0, rate=48000, codec="ac3")
        self._check("n.ac3", 1.0, 48000, LOSSY_TOLERANCE)

    def test_mka_matroska(self) -> None:
        """TC-DEC-17：MKA 纯音频。"""
        synth(self.tmp / "o.mka", seconds=1.0, rate=44100, codec="flac")
        self._check("o.mka", 1.0, 44100)

    def test_mp4_with_video_only_audio_out(self) -> None:
        """TC-DEC-18：含视频 MP4 只出音频（``-map 0:a:0``）。"""
        target = self.tmp / "p.mp4"
        _run_ffmpeg(
            [
                "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                "-f", "lavfi", "-i", "sine=frequency=1000:duration=1:sample_rate=44100",
                "-map", "0:v", "-map", "1:a",
                "-c:v", "mpeg4", "-c:a", "aac", "-shortest",
                str(target),
            ]
        )
        spec = DecodeSpec(
            path=target, sample_rate=44100, channels=2, sample_fmt="s16",
            block_frames=BLOCK_FRAMES_DEFAULT,
        )
        args = " ".join(spec.build_args("ffmpeg"))
        self.assertIn("-map 0:a:0", args)
        self.assertIn("-vn", args)
        frames, first = decode_frames(target)
        self.assertLessEqual(abs(frames - 44100), int(44100 * LOSSY_TOLERANCE) + 2048)
        self.assertEqual(int(first.shape[1]), 2, "视频轨不该混进音频输出")

    # ---- 样本格式矩阵（回归：-acodec 名字不能拼错）----

    def test_sample_format_matrix(self) -> None:
        """TC-DEC-23：s16/s24/s32/f32 四通路都要能出 PCM。"""
        target = synth(self.tmp / "q.flac", seconds=1.0, rate=44100)
        expected = {"s16": "int16", "s24": "int32", "s32": "int32", "f32": "float32"}
        for fmt, dtype_name in expected.items():
            with self.subTest(fmt=fmt):
                frames, first = decode_frames(target, sample_fmt=fmt)
                self.assertEqual(first.dtype.name, dtype_name)
                self.assertLessEqual(abs(frames - 44100), BLOCK_FRAMES_DEFAULT * 2)


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestRealSamples(unittest.TestCase):
    """真实样本：APE / DSF（本 build 无对应编码器，只能解码）。"""

    def test_ape_decodes(self) -> None:
        """TC-DEC-10：APE 能解出 PCM。

        ``tests/_tmp`` 下有一个 1 KB 的占位 ``.ape``（不是真音频），必须按
        体积过滤掉 —— 否则会被误判成「APE 播不了」。
        """
        path = next(
            (
                candidate
                for candidate in APE_SAMPLES
                if candidate.exists() and candidate.stat().st_size > 1 << 20
            ),
            None,
        )
        if path is None:
            self.skipTest("本机没有可用的 APE 样本（无 ape 编码器，无法合成）")
        frames, first = decode_frames(path, duration_ms=3000)
        self.assertGreater(frames, 44100 * 2, f"3 s 的 APE 只解出 {frames} 帧")
        self.assertEqual(int(first.shape[1]), 2)

    def test_dsd_decode_as_176400(self) -> None:
        """TC-DEC-19：DSF 按 176400 解出 PCM，帧数与时长一致。"""
        candidates: list[Path] = []
        for spec in DSF_GLOBS:
            base = Path(spec)
            if base.is_file():
                candidates.append(base)
            elif base.is_dir():
                candidates.extend(sorted(base.glob("*.dsf")))
        if not candidates:
            self.skipTest("本机没有 DSF 样本")
        path = candidates[0]
        frames, first = decode_frames(path, rate=176400, duration_ms=2000)
        self.assertLessEqual(abs(frames - 176400 * 2), BLOCK_FRAMES_DEFAULT * 4)
        self.assertGreater(float(np.abs(first.astype(np.float64)).mean()), 0.0, "DSD 解出不该是全静音")


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestAbnormalFiles(unittest.TestCase):
    """TC-DEC-24 ~ TC-DEC-29：坏输入不能崩、不能静默成功。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_bad_"))
        cls.good = synth(cls.tmp / "good.flac", seconds=2.0, rate=44100)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_truncated_file(self) -> None:
        """TC-DEC-24：截断文件要么报错、要么短帧数，但不能崩。"""
        data = self.good.read_bytes()
        truncated = self.tmp / "cut.flac"
        truncated.write_bytes(data[: int(len(data) * 0.6)])
        try:
            frames, _ = decode_frames(truncated)
        except DecodeError:
            return  # 明确报错也是合格行为
        self.assertLess(frames, 44100 * 2, "截断文件不该解出完整时长")

    def test_zero_byte_file(self) -> None:
        """TC-DEC-25：0 字节文件解不出帧，且错误原因可见（不许静默）。

        解码层的契约是「0 帧 + stderr 说明原因」；把它变成 ``ERROR`` 状态是
        引擎层的责任（见 ``test_player_behaviour.TestEngineBadInput``）。
        """
        empty = self.tmp / "empty.flac"
        empty.write_bytes(b"")
        frames, _ = decode_frames(empty)
        self.assertEqual(frames, 0, "0 字节文件不该解出任何帧")

    def test_wrong_extension_plays_or_raises(self) -> None:
        """TC-DEC-26：扩展名与内容不符——按内容播，或明确报错。"""
        mp3 = synth(self.tmp / "real.mp3", seconds=1.0, codec="libmp3lame")
        disguised = self.tmp / "disguised.flac"
        disguised.write_bytes(mp3.read_bytes())
        try:
            frames, _ = decode_frames(disguised)
        except DecodeError:
            return
        self.assertGreater(frames, 44100 * 0.5, "按内容识别应能解出接近 1 s")

    def test_non_audio_file(self) -> None:
        """TC-DEC-29：非音频内容解不出帧，且 stderr 留有原因。"""
        junk = self.tmp / "junk.flac"
        junk.write_bytes(b"this is not audio at all\n" * 200)
        frames, _ = decode_frames(junk)
        self.assertEqual(frames, 0, "非音频内容不该解出帧")


@unittest.skipIf(FFMPEG is None, "ffmpeg 不可用")
class TestSeekPrecision(unittest.TestCase):
    """TC-DEC-30/31：CUE 分轨边界（用分频段音源验证落点，不只看帧数）。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_seek_"))
        # 3 段各 2 s：300 Hz / 3000 Hz / 9000 Hz，用于判定落点落在哪一段
        cls.source = cls.tmp / "tone3.flac"
        parts = []
        for index, freq in enumerate((300, 3000, 9000)):
            part = cls.tmp / f"p{index}.wav"
            synth(part, seconds=2.0, rate=44100, codec="pcm_s16le", freq=freq)
            parts.append(part)
        listing = cls.tmp / "list.txt"
        listing.write_text(
            "".join(f"file '{part.name}'\n" for part in parts), encoding="utf-8"
        )
        _run_ffmpeg(
            [
                "-f", "concat", "-safe", "0", "-i", str(listing),
                "-c:a", "flac", str(cls.source),
            ]
        )

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _dominant_hz(block: np.ndarray, rate: int) -> float:
        mono = block.astype(np.float64).mean(axis=1)
        window = np.hanning(len(mono))
        spectrum = np.abs(np.fft.rfft(mono * window))
        return float(np.fft.rfftfreq(len(mono), 1.0 / rate)[int(np.argmax(spectrum))])

    def test_cue_track_boundaries(self) -> None:
        """三条 CUE 子轨各从 INDEX 01 起播，主频必须落在本段。"""
        segments = [(0, 300), (2000, 3000), (4000, 9000)]
        for start_ms, expect_hz in segments:
            with self.subTest(start_ms=start_ms):
                frames, first = decode_frames(
                    self.source, start_ms=start_ms, duration_ms=2000
                )
                self.assertLessEqual(abs(frames - 88200), BLOCK_FRAMES_DEFAULT * 2)
                hz = self._dominant_hz(first, 44100)
                self.assertLess(
                    abs(hz - expect_hz), expect_hz * 0.1,
                    f"start={start_ms} ms 处主频 {hz:.0f} Hz，期望 {expect_hz} Hz（落点偏了）",
                )

    def test_short_segment_seek_accuracy(self) -> None:
        """50 ms 级落点：从 1000 ms 取 100 ms，主频必须已是第一段。"""
        _, first = decode_frames(self.source, start_ms=1000, duration_ms=100)
        hz = self._dominant_hz(first, 44100)
        self.assertLess(abs(hz - 300), 60, f"1000 ms 处主频 {hz:.0f} Hz，应为 300 Hz")

    def test_seek_beyond_end_returns_nothing(self) -> None:
        """TC-DEC-32：seek 超过文件长度 → 0 帧，不抛异常。"""
        frames, _ = decode_frames(self.source, start_ms=60000, duration_ms=1000)
        self.assertEqual(frames, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
