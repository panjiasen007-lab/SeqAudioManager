"""DSD DoP 直出与可视（v1.8.1 T04）自测。

覆盖五组用例（对应任务书的 ①–⑤）：

1. DoP 打包：标记交替 + 往返逐位一致（合成位流 + 真实素材参考值）；
2. DSF 解析：chunk size **含** 12 字节头、按块交错、每字节 LSB first；
3. DFF 解析：chunk size 不含头、按字节帧交错、MSB first（合成夹具）；
4. 载体率推导（64/128/256）与「705.6 kHz 不可用 → 回退 PCM」判据
   （注入探测函数，**不碰真实声卡**）；
5. seek 块对齐（±一块，不越界、不错位）。

运行：``python -m unittest tests.test_dsd_dop -q``（**不是** pytest）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 同目录的 make_samples

import numpy as np  # noqa: E402

import make_samples  # noqa: E402
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import engine as engine_mod  # noqa: E402
from seqaudio.player.constants import OutputMode, PlayerState  # noqa: E402
from seqaudio.player.decoder import DecodeSpec  # noqa: E402
from seqaudio.player.devices import DeviceInfo, OutputPlan  # noqa: E402
from seqaudio.player.dsd import (  # noqa: E402
    DOP_MARKER_FIRST,
    DOP_MARKER_SECOND,
    DOP_MARKER_TABLE,
    DOP_RATE_OF,
    DSD_RATE_64,
    DSD_RATE_128,
    DSD_RATE_256,
    DsdDecodeError,
    DsdSource,
    DopPacker,
    dop_int24_bytes,
    dop_label_for,
    dop_marker_ok,
    dop_rate_for,
    dop_unpack,
    dsd_label_for,
    format_rate_khz,
    probe_dsd,
    rev8,
)
from seqaudio.player.engine import PlayerEngine  # noqa: E402
from seqaudio.player.queue import QueueItem  # noqa: E402
from seqaudio.player.signal_path import (  # noqa: E402
    FORBIDDEN_CLAIMS,
    SignalPath,
    assert_wording,
    latency_text,
)

#: 真实 DSF 前 16 字节（黄品源《你的眼泪》DSD64，ch0 位流起点）。
#: 用真实素材的取值当参考系，而不是自己造一个「算得出来就算对」的例子。
REFERENCE_BYTES = (
    102, 153, 153, 105, 105, 150, 105, 105, 150, 105, 150, 102, 150, 150, 102, 102,
)
#: 上述 16 字节 → 8 个 DoP 字，按 DoP 1.1 布局：标记在**最高**字节、
#: 16 bit 载荷占 bit8–23、低 8 位补 0（int32 左对齐）。
#: payload16 依次为 6699 9996 9669 9696 6996 6966 6969 6666。
REFERENCE_WORDS = (
    0x05669900, 0xFA999600, 0x05966900, 0xFA969600,
    0x05699600, 0xFA696600, 0x05696900, 0xFA666600,
)


class _TmpCase(unittest.TestCase):
    """带临时目录的用例基类（所有夹具都写在临时目录里）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


# ============================================================ ① DoP 打包


class TestDopPacker(unittest.TestCase):
    """DoP 帧布局（DoP 1.1）：标记在 MSB、逐样本交替、int32 左对齐。

    三条硬断言（对应规范原文，别让它们被「审计意见」改回去）：

    * ``(word >> 24) & 0xFF`` 逐样本交替 0x05 / 0xFA；
    * ``(word >> 8) & 0xFFFF`` = 该样本的 16 bit DSD 载荷；
    * ``word & 0xFF`` == 0（32 bit 字宽时接收端丢弃低 8 位，故 24 bit 左对齐）。
    """

    def test_reference_words(self) -> None:
        """真实素材的 8 个字必须逐字节重现（回归的硬指标）。"""
        data = np.frombuffer(bytes(REFERENCE_BYTES), dtype=np.uint8).reshape(8, 1, 2)
        words = DopPacker(1, lsb_first=True).pack(data)
        # 按**无符号**比：标记 0xFA 会置 bit31，有符号 int32 看是负数（位模式才是真相）
        unsigned = words.view(np.uint32)[:, 0]
        self.assertEqual(
            [int(value) for value in unsigned], list(REFERENCE_WORDS)
        )

    def test_signed_int32_bit_pattern_is_intentional(self) -> None:
        """标记 0xFA 置 bit31 → 有符号看是负数，这是**位模式**而不是数值错误。

        这条断言是给后来者看的：``paInt32`` 流里只有位模式有意义，接收端会丢弃
        低 8 位再取 24 bit 字。谁要是把负数「修」成正数，就改了 bit31 = 标记位。
        """
        data = np.full((2, 1, 2), 0x00, dtype=np.uint8)
        words = DopPacker(1, lsb_first=True).pack(data)
        self.assertEqual(int(words[0, 0]) & 0xFFFFFFFF, 0x05000000)
        self.assertEqual(int(words[1, 0]) & 0xFFFFFFFF, 0xFA000000)
        self.assertLess(int(words[1, 0]), 0, "0xFA 标记在 int32 里必然是负数")
        self.assertEqual(int(words[1, 0].astype(np.int64) & 0xFFFFFFFF), 0xFA000000)

    def test_marker_alternates_every_sample(self) -> None:
        """标记逐**样本**交替（不是 8×0x05 + 8×0xFA），覆盖 64 个样本。"""
        data = np.full((64, 2, 2), 0x5A, dtype=np.uint8)
        words = DopPacker(2, lsb_first=True).pack(data)
        markers = (words >> 24) & 0xFF
        expected = [DOP_MARKER_FIRST if index % 2 == 0 else DOP_MARKER_SECOND
                    for index in range(64)]
        # 双声道同一帧取同一标记
        self.assertEqual(list(int(value) for value in markers[:, 0]), expected)
        self.assertEqual(list(int(value) for value in markers[:, 1]), expected)
        self.assertTrue(dop_marker_ok(words))
        # 反例：若实现退回「8+8 分组」，第 1 个样本就会是 0x05、第 2 个也是 0x05
        self.assertNotEqual(int(markers[1, 0]), int(markers[0, 0]))
        # 标记表本身也必须逐样本交替
        self.assertEqual(
            DOP_MARKER_TABLE,
            tuple(DOP_MARKER_FIRST if index % 2 == 0 else DOP_MARKER_SECOND
                  for index in range(16)),
        )

    def test_payload_in_bits_8_to_23(self) -> None:
        """``(word >> 8) & 0xFFFF`` 必须等于该样本的 16 bit 载荷，低 8 位为 0。"""
        first, second = 0x66, 0x99
        data = np.array([[[first, second]]], dtype=np.uint8)
        word = int(DopPacker(1, lsb_first=True).pack(data)[0, 0])
        expected_payload = (rev8(first) << 8) | rev8(second)
        self.assertEqual((word >> 8) & 0xFFFF, expected_payload)
        self.assertEqual((word >> 24) & 0xFF, DOP_MARKER_FIRST)
        self.assertEqual(word & 0xFF, 0, "低 8 位必须是填充 0（32bit 字宽会被丢弃）")
        # 落位检查：标记只在最高字节、数据不许跑到高字节
        self.assertEqual(word >> 24, DOP_MARKER_FIRST)
        self.assertEqual((word >> 16) & 0xFF, 0x66, "bit16–23 应是载荷高字节")

    def test_int24_byte_order(self) -> None:
        """int24 打包顺序 = marker, payload_hi, payload_lo（与 24 bit 帧逐字节对应）。"""
        data = np.array([[[0x66, 0x99]]], dtype=np.uint8)
        word = int(DopPacker(1, lsb_first=True).pack(data)[0, 0])
        raw = dop_int24_bytes(np.array([[word]], dtype=np.int32))
        self.assertEqual(raw.shape, (1, 1, 3))
        first, second, third = (int(value) for value in raw[0, 0])
        self.assertEqual(first, DOP_MARKER_FIRST)
        self.assertEqual((first << 16) | (second << 8) | third, word >> 8)

    def test_roundtrip_bit_exact(self) -> None:
        """打包 → 解包必须逐位一致（含随机位流，双声道）。"""
        rng = np.random.default_rng(20250801)
        data = rng.integers(0, 256, size=(512, 2, 2), dtype=np.uint8)
        words = DopPacker(2, lsb_first=True).pack(data)
        back = dop_unpack(words, lsb_first=True)
        self.assertTrue(np.array_equal(back, data))
        self.assertTrue(dop_marker_ok(words))

    def test_msb_first_skips_reversal(self) -> None:
        """DFF 是 MSB first：数据直接拼，不反转。"""
        data = np.array([[[0x66, 0x99]]], dtype=np.uint8)
        word = int(DopPacker(1, lsb_first=False).pack(data)[0, 0])
        self.assertEqual((word >> 8) & 0xFFFF, 0x6699)
        self.assertEqual((word >> 24) & 0xFF, DOP_MARKER_FIRST)
        back = dop_unpack(np.array([[word]], dtype=np.int32), lsb_first=False)
        self.assertEqual(int(back[0, 0, 0]), 0x66)
        self.assertEqual(int(back[0, 0, 1]), 0x99)

    def test_phase_continues_across_blocks(self) -> None:
        """分两次打包与一次打包结果必须相同（标记相位不能每块归零）。"""
        rng = np.random.default_rng(7)
        data = rng.integers(0, 256, size=(20, 2, 2), dtype=np.uint8)
        single = DopPacker(2).pack(data)

        packer = DopPacker(2)
        first = packer.pack(data[:7])
        second = packer.pack(data[7:])
        self.assertTrue(np.array_equal(np.concatenate([first, second]), single))
        self.assertEqual(packer.phase, 20 % 16)

        # 标记周期也随相位走：后续块的第 0 帧不是「永远 0x05」
        packer.reset()
        self.assertEqual(packer.phase, 0)

    def test_pack_bytes_matches_pack(self) -> None:
        """``pack_bytes`` 只是隔行重组，结果必须与 ``pack`` 完全一致。"""
        rng = np.random.default_rng(11)
        pairs = rng.integers(0, 256, size=(9, 2, 2), dtype=np.uint8)
        flat = pairs.transpose(0, 2, 1).reshape(-1, 2)     # (frames*2, ch)
        self.assertTrue(
            np.array_equal(DopPacker(2).pack(pairs), DopPacker(2).pack_bytes(flat))
        )

    def test_shape_validation(self) -> None:
        """形状错了要当场报错，而不是产出一堆噪声。"""
        with self.assertRaises(ValueError):
            DopPacker(2).pack(np.zeros((4, 2), dtype=np.uint8))
        with self.assertRaises(ValueError):
            DopPacker(2).pack(np.zeros((4, 1, 2), dtype=np.uint8))
        with self.assertRaises(ValueError):
            dop_unpack(np.zeros((4, 2, 2), dtype=np.int32))


# ============================================================ ② DSF 解析


class TestDsfProbe(_TmpCase):
    """DSF：小端、chunk size 含 12 字节头、按块交错、LSB first。"""

    def test_header_fields(self) -> None:
        path = make_samples.make_dsf(
            self.dir / "a.dsf", dsd_rate=DSD_RATE_64, channels=2, block_bytes=4096, blocks=4
        )
        fmt = probe_dsd(path)
        self.assertEqual(fmt.container, "dsf")
        self.assertEqual(fmt.dsd_rate, 2822400)
        self.assertEqual(fmt.channels, 2)
        self.assertEqual(fmt.block_bytes, 4096)
        self.assertTrue(fmt.lsb_first)
        self.assertEqual(fmt.sample_count, 4096 * 4 * 8)
        self.assertEqual(fmt.dop_rate, 176400)
        self.assertEqual(fmt.dop_label, "DoP64")
        self.assertAlmostEqual(fmt.duration_sec, (4096 * 4 * 8) / 2822400, places=6)

    def test_data_size_includes_header(self) -> None:
        """``data`` chunk 的 size 必须按「载荷 + 12」解 —— 少减 12 会多读 12 字节噪声。"""
        path = make_samples.make_dsf(self.dir / "b.dsf", block_bytes=128, blocks=3)
        payload_bytes = 128 * 3 * 2
        raw = path.read_bytes()
        self.assertIn(b"data", raw)
        offset = raw.index(b"data")
        declared = int.from_bytes(raw[offset + 4:offset + 12], "little")
        self.assertEqual(declared, payload_bytes + 12)
        fmt = probe_dsd(path)
        self.assertEqual(fmt.data_bytes, payload_bytes)
        self.assertEqual(fmt.data_offset, offset + 12)

    def test_block_interleaved_deinterleave(self) -> None:
        """按块交错要把 ch0/ch1 正确拆开（块内同声道连续，块间才交错）。"""
        block_bytes, blocks = 64, 4
        path = make_samples.make_dsf(
            self.dir / "c.dsf", block_bytes=block_bytes, blocks=blocks
        )
        spec = self._spec(path, block_bytes=block_bytes, blocks=blocks)
        source = DsdSource(spec)
        source.start()
        try:
            block = source.read_block()
            self.assertIsNotNone(block)
            words = np.asarray(block)
            self.assertEqual(words.shape[1], 2)
            self.assertEqual(int(words.dtype.itemsize), 4)
            back = dop_unpack(words, lsb_first=True)
            frames = int(words.shape[0])
            for channel in range(2):
                expected = bytearray()
                for block_index in range(blocks):
                    for byte in make_samples.dsd_channel_bytes(block_index, block_bytes):
                        expected.append((byte + channel * 61) & 0xFF)
                got = back[:frames, channel, :].reshape(-1).tolist()
                self.assertEqual(got, list(expected[: frames * 2]), f"ch{channel} 解交错错位")
        finally:
            source.stop()

    def _spec(self, path: Path, *, block_bytes: int, blocks: int) -> DecodeSpec:
        total_bytes_per_channel = block_bytes * blocks
        return DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            codec="dsf",
            is_dsd=True,
            block_frames=total_bytes_per_channel // 2,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )

    def test_rejects_bad_magic(self) -> None:
        path = self.dir / "bad.dsf"
        path.write_bytes(b"NOPE" + bytes(64))
        with self.assertRaises(DsdDecodeError):
            probe_dsd(path)

    def test_rejects_missing_file(self) -> None:
        with self.assertRaises(DsdDecodeError):
            probe_dsd(self.dir / "nope.dsf")


# ============================================================ ③ DFF 解析


class TestDffProbe(_TmpCase):
    """DFF：大端、chunk size 不含 12 字节头、按字节帧交错、MSB first。"""

    def test_header_fields(self) -> None:
        path = make_samples.make_dff(
            self.dir / "a.dff", dsd_rate=DSD_RATE_128, channels=2, byte_frames=256
        )
        fmt = probe_dsd(path)
        self.assertEqual(fmt.container, "dff")
        self.assertEqual(fmt.dsd_rate, DSD_RATE_128)
        self.assertEqual(fmt.channels, 2)
        self.assertEqual(fmt.block_bytes, 0)          # DFF 不分块
        self.assertFalse(fmt.lsb_first)               # MSB first
        self.assertEqual(fmt.sample_count, 256 * 2 * 8 // 2 * 1)  # 256 帧/声道 × 8 bit
        self.assertEqual(fmt.dop_rate, 352800)

    def test_byte_interleaved_deinterleave(self) -> None:
        """字节帧交错 + MSB first：解出来的字节必须原样（不反转）。"""
        byte_frames = 128
        path = make_samples.make_dff(
            self.dir / "b.dff", channels=2, byte_frames=byte_frames
        )
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            codec="dff",
            is_dsd=True,
            block_frames=32,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        source.start()
        try:
            block = np.asarray(source.read_block())
            self.assertEqual(block.shape[1], 2)
            back = dop_unpack(block, lsb_first=False)
            frames = int(block.shape[0])
            for channel in range(2):
                expected = make_samples.dsd_channel_bytes(channel + 1, byte_frames)
                got = back[:, channel, :].reshape(-1).tolist()
                self.assertEqual(got, list(expected[: frames * 2]), f"ch{channel} 交错错位")
        finally:
            source.stop()

    def test_rejects_non_dsd_form(self) -> None:
        """FRM8 但 form type 不是 'DSD ' 的文件不是 DSD 位流。"""
        path = self.dir / "x.dff"
        path.write_bytes(b"FRM8" + (4).to_bytes(8, "big") + b"DSD ")
        with self.assertRaises(DsdDecodeError):
            probe_dsd(path)


class TestLayoutAcrossContainers(_TmpCase):
    """同一个 DoP 1.1 布局要同时过 DSF（LSB first）与 DFF（MSB first）两条路径。

    四条断言：① 标记逐样本交替（≥64 样本）② 载荷 = ``(word>>8)&0xFFFF``
    且低 8 位为 0 ③ 往返逐位一致（对**源位流**比，不是自比自）④ int24 字节序。
    """

    def _assert_layout(self, words: np.ndarray, expected: list[bytes], lsb_first: bool) -> None:
        words = np.asarray(words)
        frames, channels = int(words.shape[0]), int(words.shape[1])
        self.assertGreaterEqual(frames, 64, "断言所需的样本数不足")

        # ① 标记在最高字节且逐样本交替（各声道同帧同标记）
        markers = (words >> 24) & 0xFF
        expected_markers = [
            DOP_MARKER_FIRST if index % 2 == 0 else DOP_MARKER_SECOND
            for index in range(frames)
        ]
        for channel in range(channels):
            self.assertEqual(
                [int(value) for value in markers[:, channel]], expected_markers
            )

        # ② 低 8 位是填充 0；载荷可被「解包→再打包」原样重现
        self.assertTrue(np.all((words & 0xFF) == 0), "低 8 位必须为 0（int32 左对齐）")
        payload = (words >> 8) & 0xFFFF
        back = dop_unpack(words, lsb_first=lsb_first)
        repacked = DopPacker(channels, lsb_first=lsb_first).pack(back)
        self.assertTrue(
            np.array_equal((repacked >> 8) & 0xFFFF, payload),
            "解包再打包必须还原同一 16 bit 载荷",
        )
        self.assertTrue(dop_marker_ok(words))

        # ③ 往返逐位一致：与源位流比
        for channel in range(channels):
            got = back[:, channel, :].reshape(-1).tolist()
            self.assertEqual(got, list(expected[channel][: len(got)]), f"ch{channel} 往返不一致")

        # ④ int24 打包顺序 = marker, payload_hi, payload_lo
        raw = dop_int24_bytes(words)
        self.assertEqual(raw.shape, (frames, channels, 3))
        for channel in range(channels):
            for index in (0, 1, 63):
                marker, high, low = (int(value) for value in raw[index, channel])
                self.assertEqual(marker, int(markers[index, channel]))
                self.assertEqual((high << 8) | low, int(payload[index, channel]))

    def test_dsf_lsb_first(self) -> None:
        path = make_samples.make_dsf(self.dir / "lay.dsf", block_bytes=64, blocks=2)
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            is_dsd=True,
            block_frames=64,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        source.start()
        try:
            words = np.asarray(source.read_block())
            expected = [
                bytes(
                    (byte + channel * 61) & 0xFF
                    for block_index in range(2)
                    for byte in make_samples.dsd_channel_bytes(block_index, 64)
                )
                for channel in range(2)
            ]
            self._assert_layout(words, expected, lsb_first=True)
        finally:
            source.stop()

    def test_dff_msb_first(self) -> None:
        path = make_samples.make_dff(self.dir / "lay.dff", byte_frames=256)
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            is_dsd=True,
            block_frames=64,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        source.start()
        try:
            words = np.asarray(source.read_block())
            expected = [make_samples.dsd_channel_bytes(channel + 1, 256) for channel in range(2)]
            self._assert_layout(words, expected, lsb_first=False)
        finally:
            source.stop()

    def test_low_byte_must_stay_zero_after_ring_write(self) -> None:
        """环里取出来的字同样必须满足「低 8 位为 0」（防止某处补位写坏）。"""
        path = make_samples.make_dsf(self.dir / "ring.dsf", block_bytes=64, blocks=2)
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            is_dsd=True,
            block_frames=64,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        source.start()
        try:
            words = np.asarray(source.read_block())
            self.assertEqual(int(words.dtype.itemsize), 4)
            self.assertTrue(np.all((words & 0xFF) == 0), "低 8 位必须为 0")
            high = set(int(value) for value in np.unique((words >> 24) & 0xFF))
            self.assertTrue(
                high <= {DOP_MARKER_FIRST, DOP_MARKER_SECOND},
                f"高字节只能是标记，实际 {sorted(high)}",
            )
        finally:
            source.stop()


# ============================================================ ④ 载体率


class TestCarrierRate(unittest.TestCase):
    """载体率推导与「开不出来就回退」的判据（全部用假探测，不碰声卡）。"""

    def test_dop_rate_table(self) -> None:
        self.assertEqual(dop_rate_for(DSD_RATE_64), 176400)
        self.assertEqual(dop_rate_for(DSD_RATE_128), 352800)
        self.assertEqual(dop_rate_for(DSD_RATE_256), 705600)
        self.assertEqual(DOP_RATE_OF[DSD_RATE_64], 176400)
        self.assertEqual(dop_rate_for(0), 0)

    def test_labels(self) -> None:
        self.assertEqual(dsd_label_for(DSD_RATE_64), "DSD64")
        self.assertEqual(dsd_label_for(DSD_RATE_128), "DSD128")
        self.assertEqual(dop_label_for(DSD_RATE_256), "DoP256")
        self.assertEqual(format_rate_khz(176400), "176.4 kHz")
        self.assertEqual(format_rate_khz(352800), "352.8 kHz")
        self.assertEqual(format_rate_khz(705600), "705.6 kHz")
        self.assertEqual(format_rate_khz(48000), "48 kHz")


class TestEngineDopDecision(_TmpCase):
    """``_build_spec`` 的 DoP 裁决：四道闸与回退原因。"""

    def setUp(self) -> None:
        super().setUp()
        self.device = DeviceInfo(
            index=7,
            name="TempoTec USB Audio ASIO Driver",
            channels=2,
            default_samplerate=44100.0,
            is_default=True,
            hostapi_index=3,
            hostapi_name="ASIO",
        )

    # ------------------------------------------------------------ 工具

    def _item(self, path: Path, *, dsd_rate: int) -> QueueItem:
        return QueueItem(
            path=path,
            title=path.stem,
            duration=2.0,
            sample_rate=dsd_rate,
            channels=2,
            bit_depth=1,
            is_dsd=True,
        )

    def _engine(self, *, carrier_ok: bool, exclusive: bool = True) -> PlayerEngine:
        settings = Settings()
        settings.playback_device = "TempoTec USB Audio ASIO Driver"
        if exclusive:
            settings.playback_output_mode = OutputMode.EXCLUSIVE.value
        else:
            settings.playback_output_mode = OutputMode.SHARED.value
        engine = PlayerEngine(settings)
        engine._resolve_device = lambda: self.device
        engine._dop_probe = lambda device, rate, channels, mode: carrier_ok
        self.addCleanup(engine.shutdown)
        return engine

    @staticmethod
    def _plan(device, want, *, channels=0, mode=OutputMode.SHARED, probe=None,
              skip_levels=(), **kwargs) -> OutputPlan:
        """假计划：按请求率原样给出（真设备行为由集成用例覆盖）。"""
        mode_value = OutputMode.EXCLUSIVE
        return OutputPlan(
            rate=int(want),
            mode=mode_value,
            level=1,
            fallback=False,
            requested_rate=int(want),
            requested_mode=mode_value,
            note="",
            supported_rates=(int(want),),
        )

    def _build(self, path: Path, dsd_rate: int, engine: PlayerEngine) -> DecodeSpec:
        with mock.patch.object(engine_mod.devices_mod, "plan_output", self._plan):
            return engine._build_spec(self._item(path, dsd_rate=dsd_rate), 0)

    # ------------------------------------------------------------ 用例

    def test_asio_mode_is_not_downgraded(self) -> None:
        """ASIO 的独占是驱动天然行为，不能被判成「不支持独占」降级成共享。

        早先 ``_output_mode()`` 只放行 WASAPI，ASIO 会返回 SHARED 并打一句
        「当前后端 'asio' 不支持独占」——那种日志与实际行为（output 层对 ASIO
        恒按 EXCLUSIVE 开流）自相矛盾，也会把 DoP 判死。
        """
        engine = self._engine(carrier_ok=True)
        engine.settings.playback_output_mode = OutputMode.EXCLUSIVE.value
        self.assertIs(engine._output_mode(), OutputMode.EXCLUSIVE)

    def test_non_wasapi_backend_still_degrades(self) -> None:
        """MME 这类确实没有独占的后端仍按共享处理（别顺手把别人也放行）。"""
        engine = self._engine(carrier_ok=True)
        engine._resolve_device = lambda: DeviceInfo(
            index=1,
            name="扬声器",
            channels=2,
            default_samplerate=48000.0,
            is_default=True,
            hostapi_index=0,
            hostapi_name="MME",
        )
        engine.settings.playback_output_mode = OutputMode.EXCLUSIVE.value
        self.assertIs(engine._output_mode(), OutputMode.SHARED)

    def test_dsd64_engages_dop(self) -> None:
        path = make_samples.make_dsf(self.dir / "d64.dsf", dsd_rate=DSD_RATE_64, blocks=2)
        engine = self._engine(carrier_ok=True)
        spec = self._build(path, DSD_RATE_64, engine)
        self.assertTrue(spec.dop)
        self.assertEqual(spec.dop_rate, 176400)
        self.assertEqual(spec.sample_rate, 176400)
        self.assertEqual(spec.sample_fmt, "s32")     # DoP 固定整数容器
        self.assertEqual(spec.channels, 2)
        self.assertEqual(engine._dop_note, "")
        self.assertTrue(engine._dop_active)

    def test_dsd128_uses_352800_carrier(self) -> None:
        path = make_samples.make_dsf(
            self.dir / "d128.dsf", dsd_rate=DSD_RATE_128, blocks=2
        )
        engine = self._engine(carrier_ok=True)
        spec = self._build(path, DSD_RATE_128, engine)
        self.assertTrue(spec.dop)
        self.assertEqual(spec.dop_rate, 352800)
        self.assertEqual(spec.sample_rate, 352800)

    def test_dsd256_falls_back_when_705k_unavailable(self) -> None:
        """本机 ASIO 上限 352.8 kHz：DSD256 必须回退，且原因说人话。"""
        path = make_samples.make_dsf(
            self.dir / "d256.dsf", dsd_rate=DSD_RATE_256, blocks=2
        )
        engine = self._engine(carrier_ok=False)
        engine.settings.playback_dsd_pcm_rate = 176400
        spec = self._build(path, DSD_RATE_256, engine)
        self.assertFalse(spec.dop)
        self.assertEqual(spec.dop_rate, 0)
        self.assertEqual(spec.sample_rate, 176400)     # playback_dsd_pcm_rate
        self.assertEqual(spec.sample_fmt, "f32")       # PCM 通路用浮点
        self.assertIn("705.6 kHz", engine._dop_note)
        self.assertIn("设备不支持", engine._dop_note)
        self.assertFalse(engine._dop_active)

    def test_shared_output_refuses_dop(self) -> None:
        """共享模式会被系统混音器重采样 → 不许声称在 DoP。"""
        path = make_samples.make_dsf(self.dir / "shared.dsf", blocks=2)
        engine = self._engine(carrier_ok=True, exclusive=False)
        engine._resolve_device = lambda: DeviceInfo(
            index=3,
            name="扬声器 (Realtek)",
            channels=2,
            default_samplerate=48000.0,
            is_default=True,
            hostapi_index=0,
            hostapi_name="Windows WASAPI",
        )
        spec = self._build(path, DSD_RATE_64, engine)
        self.assertFalse(spec.dop)
        self.assertIn("独占", engine._dop_note)

    def test_shared_output_decodes_dsd_at_natural_rate(self) -> None:
        """非独占自动关闭 DSD 直读（用户拍板）：解码到 DSD 率 ÷ 64 的自然率。

        旧行为把共享模式的 DSD 也强制升到 ``playback_dsd_pcm_rate``（176.4k），
        在 48k 混音的 WASAPI 共享上直接被拒（-9997），部分 MME/S/PDIF 驱动更是
        连环报 -9999/-9985 开流失败。共享模式一律走自然率，交给共享阶梯挑档。
        """
        path = make_samples.make_dsf(self.dir / "shared_natural.dsf", blocks=2)
        engine = self._engine(carrier_ok=True, exclusive=False)
        engine._resolve_device = lambda: DeviceInfo(   # WASAPI 设备（ASIO 不走这道闸）
            index=3,
            name="扬声器 (Realtek)",
            channels=2,
            default_samplerate=48000.0,
            is_default=True,
            hostapi_index=0,
            hostapi_name="Windows WASAPI",
        )
        spec = self._build(path, DSD_RATE_64, engine)
        self.assertFalse(spec.dop)
        self.assertEqual(spec.sample_rate, 44100, "DSD64 ÷ 64 = 44.1k，不是 176.4k")
        self.assertEqual(spec.sample_fmt, "f32")
        self.assertIn("共享模式", engine._dop_note)
        self.assertIn("DSD 直读关闭", engine._dop_note)
        self.assertFalse(engine._dop_active)

    def test_dop_spec_uses_big_blocks(self) -> None:
        """DoP 通路必须用 ``DOP_BLOCK_FRAMES``（16384）：1024 帧 @176.4k 只有
        5.8 ms，预填 2 块才 11.6 ms，是「偶尔卡一下」的直接嫌疑。"""
        path = make_samples.make_dsf(self.dir / "bigblock.dsf", blocks=4)
        engine = self._engine(carrier_ok=True)
        spec = self._build(path, DSD_RATE_64, engine)
        self.assertTrue(spec.dop)
        self.assertEqual(spec.block_frames, engine_mod.DOP_BLOCK_FRAMES)
        self.assertEqual(spec.sample_rate, 176400)

    def test_broken_header_falls_back(self) -> None:
        path = self.dir / "broken.dsf"
        path.write_bytes(b"DSD " + bytes(120))
        engine = self._engine(carrier_ok=True)
        spec = self._build(path, DSD_RATE_64, engine)
        self.assertFalse(spec.dop)
        self.assertIn("位流无法解析", engine._dop_note)

    def test_dop_switch_off_keeps_pcm_path(self) -> None:
        """``playback_dsd_dop=False`` 是用户的明确选择：不探测、不标回退。"""
        path = make_samples.make_dsf(self.dir / "off.dsf", blocks=2)
        engine = self._engine(carrier_ok=True)
        engine.settings.playback_dsd_dop = False
        with mock.patch.object(engine_mod.devices_mod, "plan_output", self._plan):
            spec = engine._build_spec(self._item(path, dsd_rate=DSD_RATE_64), 0)
        self.assertFalse(spec.dop)
        self.assertEqual(engine._dop_note, "")
        self.assertEqual(spec.sample_rate, engine.settings.playback_dsd_pcm_rate)


class TestDopCapabilityQuery(_TmpCase):
    """``dop_capability()``：设置面板点亮/置灰 DoP 开关的唯一依据。

    与起播**同源**（同一套独占总线判定 + 同一个 int32 载体率探测），但零出声：
    不 ``start()``、不建 ring、不切 decoder —— 面板随时可以问。
    """

    def setUp(self) -> None:
        super().setUp()
        self.device = DeviceInfo(
            index=9,
            name="TempoTec USB Audio ASIO Driver",
            channels=2,
            default_samplerate=44100.0,
            is_default=True,
            hostapi_index=3,
            hostapi_name="ASIO",
        )
        self.wasapi = DeviceInfo(
            index=11,
            name="扬声器 (TempoTec USB Audio)",
            channels=2,
            default_samplerate=48000.0,
            is_default=False,
            hostapi_index=2,
            hostapi_name="Windows WASAPI",
        )
        self.mme = DeviceInfo(
            index=12,
            name="扬声器 (TempoTec USB Audio)",
            channels=2,
            default_samplerate=44100.0,
            is_default=False,
            hostapi_index=0,
            hostapi_name="MME",
        )
        self.calls: list[tuple[int, int]] = []

    def _engine(self, *, carrier_ok: bool, exclusive: bool = True) -> PlayerEngine:
        settings = Settings()
        settings.playback_device = "TempoTec USB Audio ASIO Driver"
        settings.playback_output_mode = (
            OutputMode.EXCLUSIVE.value if exclusive else OutputMode.SHARED.value
        )
        engine = PlayerEngine(settings)
        engine._resolve_device = lambda: self.device

        def probe(device, rate, channels, mode) -> bool:
            self.calls.append((int(rate), int(channels)))
            return carrier_ok

        engine._dop_probe = probe
        self.addCleanup(engine.shutdown)
        return engine

    def test_exclusive_with_carrier_reports_available(self) -> None:
        engine = self._engine(carrier_ok=True)
        available, reason = engine.dop_capability()
        self.assertTrue(available)
        self.assertEqual(reason, "", "可用时不该带任何回退理由")

    def test_shared_mode_refuses_with_reason(self) -> None:
        engine = self._engine(carrier_ok=True, exclusive=False)
        # 用 WASAPI 设备：ASIO 的「独占」是驱动天然行为，不受模式开关影响，
        # 拿 ASIO 验共享模式会被引擎有意放行（见 ``test_asio_not_downgraded``）。
        engine._resolve_device = lambda: self.wasapi
        available, reason = engine.dop_capability()
        self.assertFalse(available)
        self.assertIn("独占", reason)
        self.assertEqual(self.calls, [], "共享模式先被否掉，连探测都不该做")

    def test_non_wasapi_backend_refuses_with_reason(self) -> None:
        """MME 没有独占：不许在 MME 下点亮 DoP 开关（点了一定失望）。"""
        engine = self._engine(carrier_ok=True)
        engine._resolve_device = lambda: self.mme
        available, reason = engine.dop_capability()
        self.assertFalse(available)
        self.assertIn("独占", reason)

    def test_asio_is_not_refused_for_being_shared(self) -> None:
        """ASIO 的「独占」是驱动天然行为：模式开关写成 shared 也不该否掉 DoP。"""
        engine = self._engine(carrier_ok=True, exclusive=False)
        available, reason = engine.dop_capability()
        self.assertTrue(available, f"ASIO 被误判为共享：{reason}")
        self.assertEqual(reason, "")

    def test_probe_goes_high_to_low_and_reports_lowest_carrier(self) -> None:
        """探测顺序：最高档先试；全失败时理由给最低那档（176.4 kHz）。"""
        engine = self._engine(carrier_ok=False)
        available, reason = engine.dop_capability()
        self.assertFalse(available)
        self.assertIn("176.4 kHz", reason)
        rates = [rate for rate, _ in self.calls]
        self.assertEqual(rates, sorted(rates, reverse=True), "必须从高档往低档试")
        self.assertEqual(rates[0], 705600, "DSD256 的载体率要先试")
        self.assertEqual(rates[-1], 176400)
        self.assertTrue(
            all(channels == 2 for _, channels in self.calls),
            "面板按 2 声道探测（真实声道数只有文件头知道）",
        )

    def test_query_does_not_start_playback(self) -> None:
        """零出声：问一次能力不能顺手开流、不能改播放状态。"""
        engine = self._engine(carrier_ok=True)
        before = engine.snapshot()["state"]
        engine.dop_capability()
        self.assertEqual(engine.snapshot()["state"], before)
        self.assertFalse(engine.snapshot()["dsd"]["active"])

    def test_unknown_channel_count_falls_back_to_stereo(self) -> None:
        """面板在停止状态下拿到 ``channels == 0``：必须按立体声问。

        拿 0/1 声道去 ``check_output_settings`` 会被多数设备拒绝，于是把
        「还没开始播」误报成「不支持 DoP」，开关就永远点不亮。
        """
        for value in (0, -1, None, "", "x"):
            engine = self._engine(carrier_ok=True)
            self.calls.clear()
            available, _ = engine.dop_capability(value)  # type: ignore[arg-type]
            self.assertTrue(available, f"channels={value!r} 被误判为不可用")
            self.assertTrue(
                all(ch == 2 for _, ch in self.calls),
                f"channels={value!r} 时应按 2 声道探测，实际 {self.calls}",
            )

    def test_explicit_channel_count_is_honoured(self) -> None:
        """面板按当前源问（例如 6 声道 DSD）时，探测必须用那个声道数。"""
        engine = self._engine(carrier_ok=True)
        self.calls.clear()
        engine.dop_capability(6)
        self.assertTrue(self.calls)
        self.assertTrue(all(ch == 6 for _, ch in self.calls), self.calls)

    def test_device_lookup_failure_is_reported_not_raised(self) -> None:
        """设备表此刻不可用也不许抛异常炸掉设置面板。"""
        engine = self._engine(carrier_ok=True)

        def boom() -> None:
            raise RuntimeError("PortAudio 未就绪")

        engine._resolve_device = boom
        available, reason = engine.dop_capability()
        self.assertFalse(available)
        self.assertIn("设备", reason)


# ============================================================ ⑤ seek 对齐


class TestSeekAlignment(_TmpCase):
    """seek 必须落在容器允许的起点上（否则一个字节被拆进两帧 = 爆音）。"""

    def test_dsf_seek_is_block_aligned(self) -> None:
        block_bytes, blocks = 64, 8
        path = make_samples.make_dsf(
            self.dir / "seek.dsf", block_bytes=block_bytes, blocks=blocks
        )
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            codec="dsf",
            is_dsd=True,
            block_frames=16,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        source.start()
        try:
            # 0.0002 s = 564 bit → 必须向下对齐到整块（512 bit = 64 字节/声道）
            source.seek(0.0002)
            self.assertEqual(source._base_bits % (block_bytes * 8), 0)
            self.assertEqual(source._base_bits, block_bytes * 8)
            block = np.asarray(source.read_block())
            back = dop_unpack(block, lsb_first=True)
            frames = int(block.shape[0])
            for channel in range(2):
                expected = bytes(
                    (byte + channel * 61) & 0xFF
                    for byte in make_samples.dsd_channel_bytes(1, block_bytes)
                )
                got = back[:, channel, :].reshape(-1).tolist()
                self.assertEqual(got, list(expected[: frames * 2]))
        finally:
            source.stop()

    def test_dff_seek_is_pair_aligned(self) -> None:
        """DFF：DoP 每帧吃 2 字节 → 起点必须落在偶数字节帧上。"""
        path = make_samples.make_dff(self.dir / "seek.dff", byte_frames=256)
        fmt = probe_dsd(path)
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            codec="dff",
            is_dsd=True,
            block_frames=16,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        source.start()
        try:
            source.seek(0.0002)                      # 564 bit → 对齐到 560 bit
            self.assertEqual(source._base_bits, 560)
            self.assertEqual(source._base_bits % 16, 0)
            block = np.asarray(source.read_block())
            back = dop_unpack(block, lsb_first=False)
            frames = int(block.shape[0])
            per_channel_byte = source._base_bits // 8   # 70
            for channel in range(2):
                expected = make_samples.dsd_channel_bytes(channel + 1, 256)
                got = back[:, channel, :].reshape(-1).tolist()
                self.assertEqual(
                    got, list(expected[per_channel_byte:per_channel_byte + frames * 2])
                )
            self.assertLess(source._base_bits, fmt.sample_count)
        finally:
            source.stop()

    def test_seek_reported_position_is_aligned_not_requested(self) -> None:
        """位置要报**对齐后**的起点，不能虚报用户点的时刻。"""
        path = make_samples.make_dsf(self.dir / "pos.dsf", block_bytes=64, blocks=8)
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            is_dsd=True,
            block_frames=16,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        source.start()
        try:
            source.seek(0.0002)
            self.assertAlmostEqual(source.position_sec, 512 / 2822400, places=9)
            self.assertLess(source.position_sec, 0.0002)
        finally:
            source.stop()


# ============================================================ 时间基（秒）

class TestTimeBase(_TmpCase):
    """时长/位置单位对齐 ``QueueItem.duration``（**秒**），且时间基是载体率。

    这一组是「进度条会跳」那类 bug 的守门人：DSD 位率是载体率的 16 倍，
    任何一处用错，进度条不是 16 倍飞跑就是慢 16 倍。
    """

    def test_probe_duration_is_seconds(self) -> None:
        block_bytes, blocks = 64, 4
        path = make_samples.make_dsf(
            self.dir / "t.dsf", block_bytes=block_bytes, blocks=blocks
        )
        fmt = probe_dsd(path)
        expected = (block_bytes * blocks * 8) / 2822400.0
        self.assertAlmostEqual(fmt.duration_sec, expected, places=9)
        # 毫秒/秒混用的典型症状：值会差 1000 倍。这里锁「秒」这个量级
        self.assertLess(fmt.duration_sec, 1.0)
        self.assertAlmostEqual(fmt.duration_sec * 1000.0, expected * 1000.0, places=6)

    def test_playback_duration_is_carrier_rate_invariant(self) -> None:
        """同一份位流：按 DSD 位率算与按「帧数 / 载体率」算必须同值。"""
        path = make_samples.make_dsf(self.dir / "inv.dsf", block_bytes=64, blocks=8)
        fmt = probe_dsd(path)
        via_dsd = fmt.sample_count / fmt.dsd_rate
        via_carrier = (fmt.sample_count / 16) / fmt.dop_rate
        self.assertAlmostEqual(via_dsd, via_carrier, places=12)

    def test_source_position_advances_at_carrier_rate(self) -> None:
        path = make_samples.make_dsf(self.dir / "pos2.dsf", block_bytes=64, blocks=8)
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            is_dsd=True,
            block_frames=16,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        self.assertEqual(source.duration_sec, 0.0)      # 未 start 时保守返回 0
        source.start()
        try:
            self.assertAlmostEqual(
                source.duration_sec, (64 * 8 * 8) / 2822400.0, places=9
            )
            block = np.asarray(source.read_block())
            frames = int(block.shape[0])
            self.assertAlmostEqual(source.position_sec, frames / 176400.0, places=9)
            # 若误用 DSD 位率当时间基，位置会小 16 倍
            self.assertNotAlmostEqual(source.position_sec, frames / 2822400.0, places=6)
            # 与引擎的毫秒口径对得上（QueueItem.duration 是秒）
            self.assertEqual(
                int(round(source.duration_sec * 1000)),
                int(round((64 * 8 * 8) / 2822400.0 * 1000)),
            )
        finally:
            source.stop()

    def test_cue_duration_limits_session(self) -> None:
        """CUE 子轨给了 ``duration_ms`` 时以它为准（与 ffmpeg ``-t`` 同语义）。"""
        path = make_samples.make_dsf(self.dir / "cue.dsf", block_bytes=64, blocks=32)
        spec = DecodeSpec(
            path=path,
            sample_rate=176400,
            channels=2,
            sample_fmt="s32",
            is_dsd=True,
            block_frames=16,
            duration_ms=5,
            dop=True,
            dop_rate=176400,
            dsd_rate=2822400,
        )
        source = DsdSource(spec)
        source.start()
        try:
            self.assertAlmostEqual(source.duration_sec, 0.005, places=9)
            produced = 0
            for _ in range(64):
                block = source.read_block()
                if block is None:
                    break
                produced += int(block.shape[0])
            self.assertEqual(produced, int(round(0.005 * 176400)))
            self.assertTrue(source.at_eof)
        finally:
            source.stop()


# ============================================================ 文案与集成


class TestSignalPathWording(unittest.TestCase):
    """decode 级 DoP / 回退文案（受 ``assert_wording`` 约束）。"""

    def test_old_signature_call_still_works(self) -> None:
        """v1.8 的旧调用（不带任何 DoP 参数）必须原样可用（向后兼容）。"""
        path = SignalPath().configure(
            source="x.flac",
            codec="flac",
            decoder_version="ffmpeg",
            sample_fmt="s32",
            channels=2,
            sample_rate=44100,
            rate_want=44100,
            rate_actual=44100,
            int_pipeline=True,
            exclusive=True,
            exclusive_actual=True,
            mode_label="strict",
        )
        self.assertEqual(path.stage("decode").detail, "ffmpeg · flac")
        self.assertEqual(path.stage("decode").status.value, "PASSTHROUGH")
        self.assertEqual(path.stage("gain").detail, "0 dB 旁路（无软件增益）")
        self.assertEqual(path.stage("fade").detail, "无")
        self.assertEqual(path.wording_level(), "L2")

    def _configure(self, **kwargs) -> SignalPath:
        base = dict(
            source="DSF · 24bit/176400 Hz · 2ch · 05:32",
            codec="dsf",
            decoder_version="内置位流",
            sample_fmt="s32",
            channels=2,
            sample_rate=176400,
            rate_want=176400,
            rate_actual=176400,
            is_dsd=True,
            int_pipeline=True,
            backend="asio",
            device="TempoTec USB Audio ASIO Driver",
            exclusive=True,
            exclusive_actual=True,
            mode_label="loose",
        )
        base.update(kwargs)
        return SignalPath().configure(**base)

    def test_dop_decode_text(self) -> None:
        path = self._configure(dop=True, dsd_rate=DSD_RATE_64, dop_rate=176400)
        stage = path.stage("decode")
        self.assertEqual(stage.detail, "DSD64 → DoP64 @176.4 kHz")
        self.assertEqual(stage.status.value, "PASSTHROUGH")

    def test_dop_bypass_is_written_not_silent(self) -> None:
        """DoP 下的音量/EQ/淡变被旁路这件事必须在面板上写出来。"""
        path = self._configure(dop=True, dsd_rate=DSD_RATE_64, dop_rate=176400)
        self.assertEqual(path.stage("gain").status.value, "BYPASSED")
        self.assertIn("DoP", path.stage("gain").detail)
        self.assertIn("DoP", path.stage("eq").detail)
        self.assertIn("DoP", path.stage("fade").detail)
        self.assertIn("DoP", path.stage("resample").detail)

    def test_fallback_text_has_reason(self) -> None:
        path = self._configure(
            dop=False,
            dsd_rate=DSD_RATE_256,
            dop_rate=0,
            sample_fmt="f32",
            int_pipeline=False,
            dop_note="设备不支持 705.6 kHz 载体",
        )
        stage = path.stage("decode")
        self.assertEqual(stage.detail, "DSD256 → PCM 176.4 kHz（设备不支持 705.6 kHz 载体）")
        self.assertEqual(stage.status.value, "FALLBACK")

    def test_plain_dsd_text_keeps_legacy_form(self) -> None:
        """没有真回退（DoP 只是被用户关掉）时保持 v1.8 老文案，不制造漂移。"""
        path = self._configure(dop=False, dsd_rate=DSD_RATE_64, sample_fmt="f32")
        self.assertEqual(path.stage("decode").detail, "内置位流 · dsf · DSD→176400 Hz")
        self.assertEqual(path.stage("decode").status.value, "PASSTHROUGH")

    def test_asio_output_stage(self) -> None:
        path = self._configure(dop=True, dsd_rate=DSD_RATE_64, dop_rate=176400)
        self.assertIn("ASIO · 驱动独占", path.stage("output").detail)
        self.assertEqual(path.stage("output").status.value, "PASSTHROUGH")

    def test_no_forbidden_claims(self) -> None:
        """R2 措辞铁律：DoP 文案不许出现任何位完美类承诺。"""
        for kwargs in (
            dict(dop=True, dsd_rate=DSD_RATE_64, dop_rate=176400),
            dict(dop=True, dsd_rate=DSD_RATE_128, dop_rate=352800),
            dict(
                dop=False,
                dsd_rate=DSD_RATE_256,
                dop_note="设备不支持 705.6 kHz 载体",
                sample_fmt="f32",
                int_pipeline=False,
            ),
        ):
            path = self._configure(**kwargs)
            text = path.to_text() + path.summary() + path.wording_text()
            for banned in FORBIDDEN_CLAIMS:
                self.assertNotIn(banned.lower(), text.lower())
            for stage in path.stages:
                self.assertEqual(assert_wording(stage.detail), stage.detail)

    # ---------------------------------------------- device 级延迟文案（按后端分叉）

    def test_asio_calls_it_driver_buffer_not_latency(self) -> None:
        """ASIO 下 ``stream.latency`` 只是驱动缓冲：不许叫「延迟」。

        叫「延迟」会让人以为整条链路只有 40 ms；叫「驱动缓冲」并标实测/估算，
        才是 PortAudio 这套 API 真能证明的东西。
        """
        path = self._configure(
            dop=True, dsd_rate=DSD_RATE_64, dop_rate=176400,
            latency_ms=40.0, latency_source="measured",
        )
        detail = path.stage("device").detail
        self.assertIn("驱动缓冲 40 ms（实测）", detail)
        self.assertNotIn("延迟", detail, "ASIO 下不该出现「延迟」二字")

    def test_asio_estimated_latency_is_labelled(self) -> None:
        """驱动没上报时用的是估算值 —— 必须写「估算，驱动未上报」。"""
        path = self._configure(
            dop=True, dsd_rate=DSD_RATE_64, dop_rate=176400,
            latency_ms=64.0, latency_source="estimated",
        )
        detail = path.stage("device").detail
        self.assertIn("驱动缓冲 64 ms（估算，驱动未上报）", detail)
        self.assertNotIn("（实测）", detail)

    def test_non_asio_backends_keep_legacy_latency_text(self) -> None:
        """其它后端保持 v1.8 的「延迟 {x} ms」——既有断言依赖这行原文。"""
        for backend in ("wasapi", "mme", "directsound"):
            path = self._configure(
                dop=False, dsd_rate=DSD_RATE_64, sample_fmt="f32",
                backend=backend, latency_ms=40.0, latency_source="measured",
            )
            self.assertIn(
                "延迟 40 ms", path.stage("device").detail,
                f"{backend} 的延迟文案不该漂移",
            )

    def test_no_latency_measured_writes_nothing(self) -> None:
        """没有数据就不写这一项（不写 0 ms，也不写「未知」骗人填满）。"""
        path = self._configure(
            dop=True, dsd_rate=DSD_RATE_64, dop_rate=176400, latency_ms=0.0,
        )
        detail = path.stage("device").detail
        self.assertNotIn("驱动缓冲", detail)
        self.assertNotIn("延迟", detail)

    def test_latency_text_helper_contract(self) -> None:
        """辅助函数的三种出口直接钉住，避免有人只改了一处分支。"""
        self.assertEqual(latency_text("asio", 40.0, "measured"), "驱动缓冲 40 ms（实测）")
        self.assertEqual(
            latency_text("asio", 40.0, "estimated"), "驱动缓冲 40 ms（估算，驱动未上报）"
        )
        self.assertEqual(latency_text("ASIO", 40.0), "驱动缓冲 40 ms（实测）")
        self.assertEqual(latency_text("wasapi", 40.0, "estimated"), "延迟 40 ms")
        self.assertEqual(latency_text("asio", 0.0), "")
        self.assertEqual(latency_text("asio", -1.0), "")
        self.assertEqual(latency_text("asio", float("nan")), "")


class _MemoryOutput:
    """内存输出替身：不发声，按 ``AudioOutput`` 的生产顺序被引擎驱动。"""

    def __init__(self, ring, hook=None) -> None:
        self.ring = ring
        self.hook = hook
        self.rate = 0
        self.channels = int(ring.channels)
        self.started = False
        self.device = "memory"
        self.exclusive = True

    def prepare(self, rate: int, channels: int = 0, **_kwargs) -> int:
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

    def actual_mode(self):
        return OutputMode.EXCLUSIVE

    def snapshot(self) -> dict:
        return {
            "is_open": self.started,
            "rate": self.rate,
            "want_rate": self.rate,
            "fallback": False,
            "channels": self.channels,
            "dtype": str(self.ring.dtype),
            "device": self.device,
            "device_index": -1,
            "backend": "asio",
            "exclusive": self.exclusive,
            "mode": OutputMode.EXCLUSIVE.value,
            "blocksize": 1024,
            "latency_ms": 1.0,
            "underruns": 0,
            "system_mix_rate": 44100,
        }


class TestEngineDopIntegration(_TmpCase):
    """端到端：合成 DSF → 引擎 → 环里出现正确的 DoP 帧，全程没有 ffmpeg。"""

    def setUp(self) -> None:
        super().setUp()
        self.device = DeviceInfo(
            index=7,
            name="TempoTec USB Audio ASIO Driver",
            channels=2,
            default_samplerate=44100.0,
            is_default=True,
            hostapi_index=3,
            hostapi_name="ASIO",
        )

    def _engine(self) -> PlayerEngine:
        settings = Settings()
        settings.playback_device = "TempoTec USB Audio ASIO Driver"
        settings.playback_output_mode = OutputMode.EXCLUSIVE.value
        engine = PlayerEngine(
            settings,
            output_factory=lambda ring, device, hook: _MemoryOutput(ring, hook),
        )
        engine._resolve_device = lambda: self.device
        engine._dop_probe = lambda device, rate, channels, mode: True
        self.addCleanup(engine.shutdown)
        return engine

    @staticmethod
    def _plan(device, want, *, channels=0, mode=OutputMode.SHARED, probe=None,
              skip_levels=(), **kwargs) -> OutputPlan:
        mode_value = OutputMode.EXCLUSIVE
        return OutputPlan(
            rate=int(want),
            mode=mode_value,
            level=1,
            fallback=False,
            requested_rate=int(want),
            requested_mode=mode_value,
            note="",
            supported_rates=(int(want),),
        )

    def _start(self, engine: PlayerEngine, path: Path) -> bool:
        item = QueueItem(
            path=path,
            title=path.stem,
            duration=1.0,
            sample_rate=2822400,
            channels=2,
            bit_depth=1,
            is_dsd=True,
        )
        with mock.patch.object(engine_mod.devices_mod, "plan_output", self._plan):
            return engine._start_item(item, 0)

    def test_dop_playback_uses_builtin_stream_reader(self) -> None:
        path = make_samples.make_dsf(self.dir / "play.dsf", blocks=4)
        engine = self._engine()
        signal_events: list[dict] = []
        engine.on_signal_path = lambda payload: signal_events.append(payload)

        def _no_ffmpeg(*args, **kwargs):
            raise AssertionError("DoP 路径不得启动 ffmpeg 子进程")

        with mock.patch.object(engine_mod, "FfmpegDecoder", _no_ffmpeg):
            started = self._start(engine, path)
        self.assertTrue(started)
        try:
            self.assertIsInstance(engine._decoder, DsdSource)
            self.assertIs(engine.state, PlayerState.PLAYING)
            self.assertEqual(engine._rate, 176400)
            self.assertIsNotNone(engine._ring)
            self.assertEqual(str(engine._ring.dtype), "int32")

            ring = engine._ring
            chunk = np.zeros((512, 2), dtype=np.int32)
            read = ring.read_into(chunk)
            self.assertGreater(read, 0)
            words = chunk[:read]
            self.assertTrue(dop_marker_ok(words))
            self.assertTrue(signal_events, "起播后没有推 EVENT_SIGNAL")

            snapshot = engine.snapshot()
            self.assertTrue(snapshot["dsd"]["active"])
            self.assertEqual(snapshot["dsd"]["dop_label"], "DoP64")
            self.assertEqual(snapshot["dsd"]["dop_rate"], 176400)
            decode = next(
                stage for stage in snapshot["signal_path"] if stage["key"] == "decode"
            )
            self.assertEqual(decode["detail"], "DSD64 → DoP64 @176.4 kHz")
            self.assertIn("ASIO · 驱动独占", snapshot["signal_path"][-2]["detail"])
        finally:
            engine.stop()

    def test_volume_is_locked_during_dop(self) -> None:
        """DoP 期间音量必须返回 0 dB（位流不能被乘法碰）。"""
        path = make_samples.make_dsf(self.dir / "vol.dsf", blocks=4)
        engine = self._engine()
        self.assertTrue(self._start(engine, path))
        try:
            engine.settings.playback_volume_mode = "software"
            self.assertEqual(engine.set_volume(-12.0), 0.0)
            self.assertEqual(float(engine.snapshot()["dsp"]["gain_db"]), 0.0)
            gain = next(
                stage
                for stage in engine.snapshot()["signal_path"]
                if stage["key"] == "gain"
            )
            self.assertEqual(gain["status"], "BYPASSED")
            self.assertIn("DoP", gain["detail"])
        finally:
            engine.stop()

    def test_refresh_signal_recomputes_and_emits(self) -> None:
        """``refresh_signal()`` 必须真的重跑 _configure_signal 并推 EVENT_SIGNAL。"""
        path = make_samples.make_dsf(self.dir / "refresh.dsf", blocks=4)
        engine = self._engine()
        self.assertTrue(self._start(engine, path))
        try:
            events: list[dict] = []
            engine.on_signal_path = lambda payload: events.append(payload)
            # 模拟"设备切换"（不重开流）：output 级文案里的设备名会变
            engine._output.device = "TempoTec USB Audio (new)"
            engine.refresh_signal()
            self.assertEqual(len(events), 1, "refresh_signal 没有推 EVENT_SIGNAL")
            output = next(item for item in events[0]["stages"] if item["key"] == "output")
            self.assertIn("ASIO · 驱动独占", output["detail"])
            self.assertIn("TempoTec USB Audio (new)", output["detail"])
            # 显式刷新一律回话（界面按了刷新就该拿到事件，哪怕文本没变）
            engine.refresh_signal()
            self.assertEqual(len(events), 2)
            # 自动路径仍去重（防闪烁）：音量 0 dB 不变 → 不推
            engine.settings.playback_volume_mode = "bypass"
            engine.set_volume(0.0)
            engine.set_volume(0.0)
            self.assertEqual(len(events), 2)
        finally:
            engine.stop()

    def test_dop_switch_takes_effect_next_start(self) -> None:
        """关掉 DoP 开关**不会**改变正在跑的这条流（面板必须继续说实话）。

        换 DoP/PCM 要重建解码器与载体率，运行期做不到；所以刷新后信号路径
        仍描述「正在跑的这条流」，等下一次起播/seek 才切。这条断言正是
        「开关关了但面板说还在 DoP」这类 bug 的守门人 —— 它必须仍然说 DoP，
        直到流真的换掉。
        """
        path = make_samples.make_dsf(self.dir / "switch.dsf", blocks=4)
        engine = self._engine()
        self.assertTrue(self._start(engine, path))
        try:
            self.assertTrue(engine.snapshot()["dsd"]["active"])
            engine.settings.playback_dsd_dop = False
            engine.refresh_signal()
            self.assertTrue(engine.snapshot()["dsd"]["active"], "正在跑的流不该被开关改写")
            self.assertTrue(engine._spec.dop)
        finally:
            engine.stop()

    def test_refresh_signal_signature_is_stable(self) -> None:
        """轨 A 依赖 ``refresh_signal(self) -> None``：签名不许漂。"""
        import inspect

        signature = inspect.signature(PlayerEngine.refresh_signal)
        self.assertEqual(list(signature.parameters), ["self"])
        # engine.py 有 ``from __future__ import annotations``：注解在运行时是字符串
        self.assertEqual(
            PlayerEngine.refresh_signal.__annotations__.get("return"), "None"
        )

    def test_refresh_signal_without_session_is_safe(self) -> None:
        engine = self._engine()
        engine.refresh_signal()          # 不抛异常
        engine.refresh_signal()
        self.assertFalse(engine.snapshot()["dsd"]["active"])


class TestOutputLatencyGuard(_TmpCase):
    """``_make_output`` 的职责边界：ASIO 不该收到 ``latency`` 提示。

    ``output.py`` 本来就会为 ASIO 丢掉它（PortAudio 的 ASIO 后端会把过大的
    请求值原样回显），但上层传一个注定被丢弃的参数属于职责不清 —— 谁该忽略、
    为什么，只留在真正懂 ASIO 的那一层。
    """

    @staticmethod
    def _device(hostapi: str, index: int = 5) -> DeviceInfo:
        return DeviceInfo(
            index=index,
            name=f"{hostapi} 设备",
            channels=2,
            default_samplerate=48000.0,
            is_default=True,
            hostapi_index=0,
            hostapi_name=hostapi,
        )

    def _capture(self, device: DeviceInfo) -> dict:
        settings = Settings()
        settings.playback_buffer_ms = 120
        engine = PlayerEngine(settings)
        engine._plan = None
        try:
            with mock.patch.object(engine_mod, "AudioOutput") as fake:
                engine._make_output(mock.MagicMock(), device)
                _, kwargs = fake.call_args
                return dict(kwargs)
        finally:
            engine.shutdown()

    def test_asio_gets_no_latency_hint(self) -> None:
        kwargs = self._capture(self._device("ASIO"))
        self.assertNotIn("latency", kwargs, "ASIO 不该收到 latency 提示")

    def test_wasapi_still_gets_buffer_depth(self) -> None:
        """非 ASIO 的「缓冲深度」必须照旧生效（曾经只存不用）。"""
        kwargs = self._capture(self._device("Windows WASAPI"))
        self.assertAlmostEqual(float(kwargs.get("latency", 0.0)), 0.120, places=4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
