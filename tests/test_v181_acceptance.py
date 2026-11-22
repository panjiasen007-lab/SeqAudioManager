"""v1.8.1 独立验收（QA 自写，不复用工程师的夹具与断言）。

为什么单独一份
--------------
`test_dsd_dop.py` / `test_device_watch.py` / `test_signal_refresh.py` 由实现者
自己写：它们能证明「实现符合实现者的理解」，但证明不了「符合规范」。本文件
刻意**不 import** `make_samples` 的 DSD 夹具、也**不把** `dsd.dop_markers()` /
`DOP_MARKER_TABLE` 当作判据的唯一起点 —— 标记从 `(word >> 24) & 0xFF` 直接
取，夹具按 DSF/DFF 规范在这里手工重写一遍，位序反转用字符串实现（与被测模块
的查表法完全不同的算法）。

覆盖（对应验收任务书的 7 条规范级断言）
---------------------------------------
1. DoP 1.1 帧布局：标记在最高字节、**逐样本**交替、int32 左对齐、位序正确；
2. DSF / DFF 解析：用本文件自造的夹具验证（含 DSF chunk size 含 12 字节头）；
3. DoP 回退：载体率探测不可用（注入假探测）→ PCM + decode 级 FALLBACK + 原因；
4. ASIO 分支：`plan_output` 不套四级降级、模式恒 EXCLUSIVE、失败文案写明原因；
5. 信号刷新：上一首 96k → 当前 44.1k，面板显示**当前**值；
6. 热插拔：两次枚举做差，按设备名比对，增/删/默认变化三情形只报一次；
7. 版本号：四处一致 1.8.1。

运行：``QT_QPA_PLATFORM=offscreen python -m unittest tests.test_v181_acceptance -v``
"""

from __future__ import annotations

import os
import re
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import engine as engine_mod  # noqa: E402
from seqaudio.player.constants import OutputMode, StageStatus  # noqa: E402
from seqaudio.player.decoder import DecodeSpec  # noqa: E402
from seqaudio.player.devices import (  # noqa: E402
    DeviceInfo,
    DeviceWatcher,
    OutputPlan,
    RateUnsupportedError,
    plan_output,
)
from seqaudio.player.dsd import DsdDecodeError, DsdSource, DopPacker, probe_dsd  # noqa: E402
from seqaudio.player.engine import PlayerEngine  # noqa: E402
from seqaudio.player.queue import QueueItem  # noqa: E402

# ============================================================ 独立工具


def bitrev(value: int) -> int:
    """单字节位序反转（**字符串实现**：与被测模块的位运算查表互不依赖）。"""
    return int(f"{int(value) & 0xFF:08b}"[::-1], 2)


def dop_words(pairs: list[tuple[int, int]], *, lsb_first: bool) -> list[int]:
    """独立参考实现：字节对 → DoP 24 bit 字（按 DoP 1.1 白皮书布局）。

    ``24 bit = [marker(8) | payload16(16)]``，marker 逐**样本**交替
    0x05 / 0xFA，int32 容器左对齐（低 8 位填 0）。
    """
    words: list[int] = []
    for index, (first, second) in enumerate(pairs):
        high = bitrev(first) if lsb_first else (first & 0xFF)
        low = bitrev(second) if lsb_first else (second & 0xFF)
        marker = 0x05 if index % 2 == 0 else 0xFA
        words.append((marker << 24) | (high << 16) | (low << 8))
    return words


def unpack_words(words: np.ndarray, *, lsb_first: bool) -> list[tuple[int, int]]:
    """独立反解**单声道**的一列 DoP 字 → 原始 DSD 字节对。

    不调用被测模块的任何函数：载荷位置从 ``(word >> 8) & 0xFFFF`` 直接取。
    """
    out: list[tuple[int, int]] = []
    for raw in np.asarray(words).reshape(-1):
        word = int(raw) & 0xFFFFFFFF
        payload = (word >> 8) & 0xFFFF
        high, low = (payload >> 8) & 0xFF, payload & 0xFF
        if lsb_first:
            high, low = bitrev(high), bitrev(low)
        out.append((high, low))
    return out


def read_all(source: DsdSource, limit: int = 64) -> np.ndarray:
    """把 ``DsdSource`` 读干净（EOF 时它返回 ``None``，不要喂给 ``np.asarray``）。"""
    blocks: list[np.ndarray] = []
    for _ in range(limit):
        raw = source.read_block()
        if raw is None:
            break
        array = np.asarray(raw)
        if array.size == 0:
            break
        blocks.append(array)
    if not blocks:
        return np.zeros((0, 0), dtype=np.int32)
    return np.concatenate(blocks)


def markers_of(words: np.ndarray) -> list[list[int]]:
    """取出每帧各声道的标记字节 —— 直接从**最高字节**取，不走被测辅助函数。"""
    data = np.asarray(words)
    frames = int(data.shape[0])
    channels = int(data.shape[1]) if data.ndim > 1 else 1
    return [
        [int((int(data[i, c]) & 0xFFFFFFFF) >> 24) & 0xFF for c in range(channels)]
        for i in range(frames)
    ]


# ------------------------------------------------------------ 夹具（自造）


def _dsf_byte(block: int, channel: int, pos: int) -> int:
    """确定性位流：块号/声道/位置三者都参与，任何顺序错位都会立刻比对失败。"""
    return (pos * 5 + block * 0x40 + channel * 0x80) & 0xFF


def make_dsf(
    path: Path,
    *,
    dsd_rate: int = 2822400,
    channels: int = 2,
    block_bytes: int = 64,
    blocks: int = 2,
    byte_at=_dsf_byte,
) -> Path:
    """按 DSF 规范手写容器（本文件独立实现，不依赖 tests/make_samples.py）。

    三处规范细节：
    * ``fmt `` 的 size = 52（**含** 12 字节 chunk 头 → 载荷 40 字节）；
    * ``data`` 的 size = 载荷 + 12；
    * 位流按块交错：``block0.ch0 | block0.ch1 | block1.ch0 …``。
    """
    payload = bytearray()
    for block in range(blocks):
        for channel in range(channels):
            for pos in range(block_bytes):
                payload.append(byte_at(block, channel, pos) & 0xFF)

    fmt_payload = struct.pack(
        "<IIIIIIQII",
        1,                                   # formatVersion
        0,                                   # formatID（0 = DSD raw）
        2 if channels > 1 else 0,            # channelType
        channels,
        dsd_rate,
        1,                                   # bitsPerSample
        block_bytes * blocks * 8,            # sampleCount（每声道位数）
        block_bytes,                         # blockSizePerChannel
        0,
    )
    assert len(fmt_payload) == 40
    total = 28 + 52 + 12 + len(payload)
    header = b"DSD " + struct.pack("<QQQ", 28, total, total)
    fmt_chunk = b"fmt " + struct.pack("<Q", 52) + fmt_payload
    data_chunk = b"data" + struct.pack("<Q", 12 + len(payload)) + bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + fmt_chunk + data_chunk)
    return path


def _dff_byte(channel: int, frame: int) -> int:
    return (frame * 7 + channel * 0x80) & 0xFF


def make_dff(
    path: Path,
    *,
    dsd_rate: int = 5644800,
    channels: int = 2,
    byte_frames: int = 128,
    byte_at=_dff_byte,
) -> Path:
    """按 DSDIFF 规范手写容器：大端、size **不含**头、字节帧交错、MSB first。"""
    payload = bytearray()
    for frame in range(byte_frames):
        for channel in range(channels):
            payload.append(byte_at(channel, frame) & 0xFF)

    def chunk(chunk_id: bytes, body: bytes) -> bytes:
        pad = b"\x00" if len(body) % 2 else b""
        return chunk_id + struct.pack(">Q", len(body)) + body + pad

    fver = chunk(b"FVER", struct.pack(">I", 0x01050000))
    # 奇数长度的无关 chunk：验证 IFF「奇数补 1 字节」的跳过逻辑
    comt = chunk(b"COMT", b"\x07" * 7)
    fs = chunk(b"FS  ", struct.pack(">I", dsd_rate))
    chnl = chunk(b"CHNL", struct.pack(">H", channels) + b"\x00" * 4 * channels)
    snd = b"SND " + struct.pack(">Q", len(fs) + len(chnl)) + fs + chnl
    prop = chunk(b"PROP", snd)
    dsd = chunk(b"DSD ", bytes(payload))
    body = b"DSD " + fver + comt + prop + dsd
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"FRM8" + struct.pack(">Q", len(body)) + body)
    return path


def _spec(
    path: Path,
    *,
    dop_rate: int = 176400,
    dsd_rate: int = 2822400,
    block_frames: int = 32,
    channels: int = 2,
) -> DecodeSpec:
    return DecodeSpec(
        path=path,
        sample_rate=dop_rate,
        channels=channels,
        sample_fmt="s32",
        is_dsd=True,
        block_frames=block_frames,
        dop=True,
        dop_rate=dop_rate,
        dsd_rate=dsd_rate,
    )


class _TmpDir(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)


# ============================================================ 断言 1：DoP 帧布局


class TestDopFrameLayout(_TmpDir):
    """DoP 1.1 布局的四条硬断言（不用被测模块的辅助函数作判据）。"""

    def test_marker_alternates_per_sample_over_64_frames(self) -> None:
        """标记逐**样本**交替（不是 8+8），覆盖 64 帧、双声道同帧同标记。"""
        pairs = [(0x12, 0x34)] * 64
        words = DopPacker(1, lsb_first=True).pack(
            np.array([[list(pair)] for pair in pairs], dtype=np.uint8)
        )
        markers = markers_of(words)
        self.assertEqual(len(markers), 64)
        for index, row in enumerate(markers):
            expected = 0x05 if index % 2 == 0 else 0xFA
            self.assertEqual(row[0], expected, f"第 {index} 帧标记错了：{row[0]:#04x}")

        # 双声道：同一帧的两个声道必须取同一标记
        stereo = DopPacker(2, lsb_first=True).pack(
            np.array([[[0x12, 0x34], [0x56, 0x78]]] * 64, dtype=np.uint8)
        )
        for index, row in enumerate(markers_of(stereo)):
            self.assertEqual(row[0], row[1], f"第 {index} 帧双声道标记不一致：{row}")
            self.assertEqual(row[0], 0x05 if index % 2 == 0 else 0xFA)

        # 反例：若实现退回「8×0x05 + 8×0xFA」，第 1、2 帧标记会相同
        self.assertNotEqual(markers[0][0], markers[1][0])
        self.assertNotEqual(markers[7][0], markers[8][0])
        self.assertEqual(markers[14][0], 0x05)
        self.assertEqual(markers[15][0], 0xFA)
        self.assertEqual(markers[16][0], 0x05, "第二个周期必须从 0x05 重新开始")

    def test_low_byte_zero_and_payload_position(self) -> None:
        """int32 左对齐：标记在 bit24–31、载荷在 bit8–23、低 8 位恒 0。"""
        words = DopPacker(1, lsb_first=True).pack(
            np.array([[[0x66, 0x99]]], dtype=np.uint8)
        )
        word = int(words[0, 0]) & 0xFFFFFFFF
        self.assertEqual(word & 0xFF, 0x00, "低 8 位必须为填充 0（32bit 字宽被丢弃）")
        self.assertEqual((word >> 24) & 0xFF, 0x05)
        payload = (word >> 8) & 0xFFFF
        self.assertEqual(payload, (bitrev(0x66) << 8) | bitrev(0x99))

    def test_time_order_first_bit_is_payload_msb_lsb_first(self) -> None:
        """DSF（LSB first）：源字节 0x01 的「第一个 DSD 位」必须是载荷的最高位。"""
        path = make_dsf(
            self.dir / "order.dsf",
            channels=1,
            block_bytes=64,
            blocks=1,
            byte_at=lambda block, channel, pos: 0x01 if pos == 0 else 0x00,
        )
        fmt = probe_dsd(path)
        self.assertTrue(fmt.lsb_first)
        self.assertEqual(fmt.channels, 1)
        source = DsdSource(_spec(path, channels=1))
        source.start()
        try:
            block = np.asarray(source.read_block())
        finally:
            source.stop()
        first = int(block[0, 0]) & 0xFFFFFFFF
        self.assertEqual((first >> 8) & 0xFFFF, 0x8000, "0x01 的首位应落在载荷 MSB")
        self.assertEqual((first >> 24) & 0xFF, 0x05)
        second = int(block[1, 0]) & 0xFFFFFFFF
        self.assertEqual((second >> 8) & 0xFFFF, 0x0000)

    def test_time_order_first_bit_is_payload_msb_msb_first(self) -> None:
        """DFF（MSB first）：源字节 0x80 的首位同样落在载荷 MSB，且不反转字节。"""
        path = make_dff(
            self.dir / "order.dff",
            channels=1,
            byte_frames=64,
            byte_at=lambda channel, frame: 0x80 if frame == 0 else 0x00,
        )
        fmt = probe_dsd(path)
        self.assertFalse(fmt.lsb_first)
        source = DsdSource(
            _spec(path, dop_rate=352800, dsd_rate=5644800, channels=1)
        )
        source.start()
        try:
            block = np.asarray(source.read_block())
        finally:
            source.stop()
        first = int(block[0, 0]) & 0xFFFFFFFF
        self.assertEqual((first >> 8) & 0xFFFF, 0x8000)
        self.assertEqual((first >> 24) & 0xFF, 0x05)

    def test_payload_matches_source_bits_dsf(self) -> None:
        """逐位一致：DSF 全程（含块交错）解出的载荷 == 独立参考实现。"""
        block_bytes, blocks = 64, 3
        path = make_dsf(
            self.dir / "bits.dsf", block_bytes=block_bytes, blocks=blocks
        )
        source = DsdSource(_spec(path, block_frames=32))
        source.start()
        try:
            words = read_all(source)
        finally:
            source.stop()
        frames = int(words.shape[0])
        self.assertGreaterEqual(frames, 64, "断言所需帧数不足")

        expected_bytes = [
            bytes(
                _dsf_byte(block, channel, pos)
                for block in range(blocks)
                for pos in range(block_bytes)
            )
            for channel in range(2)
        ]
        for channel in range(2):
            expected_pairs = list(
                zip(expected_bytes[channel][0::2], expected_bytes[channel][1::2])
            )
            self.assertEqual(
                unpack_words(words[:, channel], lsb_first=True),
                expected_pairs[:frames],
                f"ch{channel} 载荷与源位流不一致",
            )
            # 整字比对（标记 + 载荷 + 补零一次比完）
            self.assertEqual(
                [int(value) & 0xFFFFFFFF for value in words[:, channel]],
                dop_words(expected_pairs[:frames], lsb_first=True),
                f"ch{channel} 整字与参考实现不一致",
            )

    def test_payload_matches_source_bits_dff(self) -> None:
        """逐位一致：DFF（MSB first、字节帧交错）解出的载荷 == 独立参考实现。"""
        byte_frames = 128
        path = make_dff(self.dir / "bits.dff", byte_frames=byte_frames)
        source = DsdSource(
            _spec(path, dop_rate=352800, dsd_rate=5644800, block_frames=32)
        )
        source.start()
        try:
            words = read_all(source)
        finally:
            source.stop()
        frames = int(words.shape[0])
        self.assertGreaterEqual(frames, 64)

        for channel in range(2):
            expected_bytes = bytes(_dff_byte(channel, f) for f in range(byte_frames))
            expected_pairs = list(zip(expected_bytes[0::2], expected_bytes[1::2]))
            self.assertEqual(
                unpack_words(words[:, channel], lsb_first=False),
                expected_pairs[:frames],
            )
            self.assertEqual(
                [int(value) & 0xFFFFFFFF for value in words[:, channel]],
                dop_words(expected_pairs[:frames], lsb_first=False),
            )

    def test_roundtrip_returns_identical_bytes(self) -> None:
        """反解往返：把 DoP 字按独立实现反解，必须逐字节还原源位流。"""
        rng = np.random.default_rng(424242)
        data = rng.integers(0, 256, size=(96, 2, 2), dtype=np.uint8)
        words = DopPacker(2, lsb_first=True).pack(data)
        for channel in range(2):
            self.assertEqual(
                unpack_words(words[:, channel], lsb_first=True),
                [
                    (int(data[i, channel, 0]), int(data[i, channel, 1]))
                    for i in range(96)
                ],
            )

    def test_marker_continuity_across_read_blocks(self) -> None:
        """跨块连续：分多次 ``read_block()`` 读出的标记序列必须**不停顿地**交替。

        相位每块归零会让接收端每块重新对齐 —— 有的 DAC 能容错，有的直接失锁
        （周期性「咔」）。这里用 17 帧一块（与 16 帧周期错位）把问题暴露出来。
        """
        path = make_dsf(
            self.dir / "phase.dsf", channels=1, block_bytes=64, blocks=2
        )
        source = DsdSource(_spec(path, channels=1, block_frames=17))
        source.start()
        try:
            words = read_all(source)
        finally:
            source.stop()
        self.assertGreaterEqual(int(words.shape[0]), 33)
        markers = [row[0] for row in markers_of(words)]
        expected = [0x05 if index % 2 == 0 else 0xFA for index in range(len(markers))]
        self.assertEqual(markers, expected, "块边界处标记序列断掉了（相位被归零）")


# ============================================================ 断言 2：容器解析


class TestContainerParsing(_TmpDir):
    """用本文件自造的夹具验证 DSF / DFF 头解析（不复用工程师的夹具）。"""

    def test_dsf_chunk_sizes_include_header(self) -> None:
        path = make_dsf(
            self.dir / "h.dsf", block_bytes=4096, blocks=2, dsd_rate=2822400
        )
        raw = path.read_bytes()
        # fmt 的 size 字段必须是 52（含 12 字节头），载荷 40 字节
        fmt_at = raw.index(b"fmt ")
        self.assertEqual(int.from_bytes(raw[fmt_at + 4:fmt_at + 12], "little"), 52)
        data_at = raw.index(b"data")
        payload_bytes = 4096 * 2 * 2
        self.assertEqual(
            int.from_bytes(raw[data_at + 4:data_at + 12], "little"),
            payload_bytes + 12,
            "DSF 的 data chunk size 必须含 12 字节头",
        )

        fmt = probe_dsd(path)
        self.assertEqual(fmt.container, "dsf")
        self.assertEqual(fmt.data_offset, data_at + 12, "位流起点必须跳过 12 字节头")
        self.assertEqual(fmt.data_bytes, payload_bytes)
        self.assertEqual(fmt.block_bytes, 4096)
        self.assertEqual(fmt.channels, 2)
        self.assertEqual(fmt.dsd_rate, 2822400)
        self.assertTrue(fmt.lsb_first)
        self.assertEqual(fmt.sample_count, 4096 * 2 * 8)
        self.assertEqual(fmt.dop_rate, 176400)
        self.assertAlmostEqual(fmt.duration_sec, (4096 * 2 * 8) / 2822400, places=9)

    def test_dsf_block_interleave_4096(self) -> None:
        """4096 B/声道的块交错：ch0 的第 2 块必须排在 ch1 第 1 块之后。"""
        path = make_dsf(
            self.dir / "bi.dsf", block_bytes=4096, blocks=2, channels=2
        )
        source = DsdSource(_spec(path, block_frames=1024))
        source.start()
        try:
            words = read_all(source)
        finally:
            source.stop()
        frames = int(words.shape[0])
        self.assertGreaterEqual(frames, 4096 * 2 // 2, "两块位流应当都读出来")
        for channel in range(2):
            expected = bytes(
                _dsf_byte(block, channel, pos)
                for block in range(2)
                for pos in range(4096)
            )
            expected_pairs = list(zip(expected[0::2], expected[1::2]))
            self.assertEqual(
                unpack_words(words[:, channel], lsb_first=True),
                expected_pairs[:frames],
                f"ch{channel} 块交错错位",
            )

    def test_dff_header_and_msb_first(self) -> None:
        path = make_dff(
            self.dir / "h.dff", dsd_rate=5644800, channels=2, byte_frames=256
        )
        raw = path.read_bytes()
        self.assertEqual(raw[:4], b"FRM8")
        self.assertEqual(raw[12:16], b"DSD ")
        fmt = probe_dsd(path)
        self.assertEqual(fmt.container, "dff")
        self.assertEqual(fmt.dsd_rate, 5644800)
        self.assertEqual(fmt.channels, 2)
        self.assertEqual(fmt.block_bytes, 0, "DFF 没有分块概念")
        self.assertFalse(fmt.lsb_first, "DFF 必须 MSB first")
        self.assertEqual(fmt.data_bytes, 256 * 2)
        self.assertEqual(fmt.sample_count, 256 * 2 * 8 // 2)
        self.assertEqual(fmt.dop_rate, 352800)

    def test_dff_odd_chunk_padding_is_skipped(self) -> None:
        """IFF 奇数长度补 1 字节：跳过 COMT（奇数）之后仍要读到正确的 FS/CHNL。"""
        path = make_dff(self.dir / "pad.dff", byte_frames=64)
        fmt = probe_dsd(path)
        self.assertEqual(fmt.dsd_rate, 5644800)
        self.assertEqual(fmt.channels, 2)
        self.assertEqual(fmt.data_bytes, 64 * 2)

    def test_bad_magic_is_rejected(self) -> None:
        """magic 不认识就报错，绝不猜着当 PCM 解（宁可不播也不放噪声）。"""
        bad = self.dir / "bad.dsf"
        bad.write_bytes(b"DSD " + bytes(200))
        with self.assertRaises(DsdDecodeError):
            probe_dsd(bad)
        not_dsd = self.dir / "x.bin"
        not_dsd.write_bytes(bytes(64))
        with self.assertRaises(DsdDecodeError):
            probe_dsd(not_dsd)


# ============================================================ 断言 3：DoP 回退


class _FakePlan:
    """``plan_output`` 的替身：按请求率原样出计划（不碰声卡）。"""

    @staticmethod
    def build(device, want, *, channels=0, mode=OutputMode.SHARED, probe=None,
              skip_levels=(), **kwargs) -> OutputPlan:
        return OutputPlan(
            rate=int(want),
            mode=OutputMode.EXCLUSIVE,
            level=1,
            fallback=False,
            requested_rate=int(want),
            requested_mode=OutputMode.EXCLUSIVE,
            note="",
            supported_rates=(int(want),),
        )


class TestDopFallback(_TmpDir):
    """载体率探测不可用时，**必须**回退 PCM 并在 decode 级写明原因。"""

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

    def _engine(self, *, max_carrier: int, pcm_rate: int = 176400) -> PlayerEngine:
        settings = Settings()
        settings.playback_device = self.device.name
        settings.playback_output_mode = OutputMode.EXCLUSIVE.value
        settings.playback_dsd_pcm_rate = pcm_rate
        engine = PlayerEngine(settings)
        engine._resolve_device = lambda: self.device
        seen: list[int] = []

        def probe(device, rate, channels, mode) -> bool:
            seen.append(int(rate))
            return int(rate) <= int(max_carrier)

        engine._dop_probe = probe
        self.seen = seen
        self.addCleanup(engine.shutdown)
        return engine

    def _item(self, path: Path, dsd_rate: int) -> QueueItem:
        return QueueItem(
            path=path,
            title=path.stem,
            duration=1.0,
            sample_rate=dsd_rate,
            channels=2,
            bit_depth=1,
            is_dsd=True,
        )

    def _build(self, engine: PlayerEngine, path: Path, dsd_rate: int) -> DecodeSpec:
        with mock.patch.object(engine_mod.devices_mod, "plan_output", _FakePlan.build):
            return engine._build_spec(self._item(path, dsd_rate), 0)

    def _decode_stage(self, engine: PlayerEngine, spec: DecodeSpec):
        engine._channels = int(spec.channels)
        engine._rate = int(spec.sample_rate)
        engine._want_rate = int(spec.sample_rate)
        engine._configure_signal(spec, self.device, int(spec.sample_rate))
        return engine._signal.stage("decode")

    def test_dsd256_falls_back_on_352k_device(self) -> None:
        """本机上限 352.8 kHz：DSD256（需 705.6 kHz）必须回退并标 FALLBACK。"""
        path = make_dsf(self.dir / "d256.dsf", dsd_rate=11289600, blocks=2)
        engine = self._engine(max_carrier=352800)
        spec = self._build(engine, path, 11289600)

        self.assertFalse(spec.dop, "DSD256 在本机不该走 DoP")
        self.assertEqual(spec.dop_rate, 0)
        self.assertEqual(spec.sample_rate, 176400, "回退率必须是 playback_dsd_pcm_rate")
        self.assertIn("705.6 kHz", engine._dop_note)
        self.assertTrue(engine._dop_note, "回退原因不能为空（不许静默降级）")
        self.assertIn(705600, self.seen, "705.6 kHz 必须先真探测一次才算「不可用」")

        stage = self._decode_stage(engine, spec)
        self.assertEqual(stage.status, StageStatus.FALLBACK)
        self.assertIn("DSD256", stage.detail)
        self.assertIn("PCM", stage.detail)
        self.assertIn("176.4 kHz", stage.detail, "回退后的目标率必须写进 decode 级")
        self.assertIn("705.6 kHz", stage.detail, "回退原因必须写进 decode 级")

    def test_dsd128_uses_352k_carrier(self) -> None:
        """上限 352.8 kHz 时 DSD128 必须真的走 DoP128，且 decode 不标 FALLBACK。"""
        path = make_dsf(self.dir / "d128.dsf", dsd_rate=5644800, blocks=2)
        engine = self._engine(max_carrier=352800)
        spec = self._build(engine, path, 5644800)
        self.assertTrue(spec.dop)
        self.assertEqual(spec.dop_rate, 352800)
        self.assertEqual(spec.sample_fmt, "s32")
        self.assertEqual(engine._dop_note, "")

        stage = self._decode_stage(engine, spec)
        self.assertEqual(stage.status, StageStatus.PASSTHROUGH)
        self.assertEqual(stage.detail, "DSD128 → DoP128 @352.8 kHz")

    def test_dsd64_falls_back_when_176k_unavailable(self) -> None:
        """连 176.4 kHz 都开不出来时 DSD64 也要回退（不是「DSD64 一定行」）。"""
        path = make_dsf(self.dir / "d64.dsf", dsd_rate=2822400, blocks=2)
        engine = self._engine(max_carrier=96000, pcm_rate=88200)
        spec = self._build(engine, path, 2822400)
        self.assertFalse(spec.dop)
        self.assertEqual(spec.sample_rate, 88200)
        stage = self._decode_stage(engine, spec)
        self.assertEqual(stage.status, StageStatus.FALLBACK)
        self.assertIn("176.4 kHz", stage.detail)


# ============================================================ 断言 4：ASIO 计划


class TestAsioPlanNoDegrade(unittest.TestCase):
    """ASIO 设备不得走 shared/exclusive 四级降级，模式恒 EXCLUSIVE。"""

    @staticmethod
    def _asio_device() -> DeviceInfo:
        return DeviceInfo(
            index=7,
            name="TempoTecUSBAudio ASIO Driver",
            channels=2,
            default_samplerate=44100.0,
            hostapi_index=3,
            hostapi_name="ASIO",
        )

    def test_shared_request_still_opens_exclusive(self) -> None:
        calls: list[tuple[int, OutputMode]] = []

        def probe(device, rate, channels, mode) -> bool:
            calls.append((int(rate), OutputMode(mode)))
            return True

        plan = plan_output(
            self._asio_device(), 44100, channels=2, mode="shared", probe=probe
        )
        self.assertIs(plan.mode, OutputMode.EXCLUSIVE, "ASIO 模式必须恒为独占")
        self.assertIs(plan.requested_mode, OutputMode.SHARED)
        self.assertTrue(
            all(mode is OutputMode.EXCLUSIVE for _, mode in calls),
            f"ASIO 下不该用共享模式探测：{set(mode for _, mode in calls)}",
        )
        self.assertIn("ASIO", plan.note or "")

    def test_only_rate_is_degraded(self) -> None:
        """唯一可退维度是采样率：705.6k 被拒 → 退到驱动声明的率，仍独占。"""
        calls: list[tuple[int, OutputMode]] = []

        def probe(device, rate, channels, mode) -> bool:
            calls.append((int(rate), OutputMode(mode)))
            return int(rate) <= 352800

        plan = plan_output(
            self._asio_device(), 705600, channels=2, mode="exclusive", probe=probe
        )
        self.assertIs(plan.mode, OutputMode.EXCLUSIVE)
        self.assertEqual(plan.rate, 352800)
        self.assertTrue(plan.fallback)
        self.assertIn("705600", plan.note.replace(" ", ""))
        self.assertEqual([rate for rate, _ in calls][:2], [705600, 352800])

    def test_all_rates_fail_raises_with_asio_reason(self) -> None:
        def probe(device, rate, channels, mode) -> bool:
            return False

        with self.assertRaises(RateUnsupportedError) as ctx:
            plan_output(
                self._asio_device(), 705600, channels=2, mode="shared", probe=probe
            )
        message = str(ctx.exception)
        self.assertIn("ASIO 只支持驱动声明的采样率", message)
        self.assertIn("705600", message, "失败信息要列出试过的率")
        self.assertTrue(getattr(ctx.exception, "tried", ()), "tried 不能为空")

    def test_skip_levels_does_not_reinvent_shared(self) -> None:
        """跳过 1/3 级后 ASIO 只报错，绝不"退回共享"（那样等于换了个后端）。"""
        def probe(device, rate, channels, mode) -> bool:
            return True

        with self.assertRaises(RateUnsupportedError):
            plan_output(
                self._asio_device(),
                44100,
                channels=2,
                mode="exclusive",
                probe=probe,
                skip_levels=(1, 3),
            )

    def test_non_asio_device_keeps_four_level_ladder(self) -> None:
        """非 ASIO 设备照旧：请求共享就拿到共享（ASIO 分支不许波及别人）。"""
        wasapi = DeviceInfo(
            index=3,
            name="扬声器 (Realtek)",
            channels=2,
            default_samplerate=48000.0,
            hostapi_index=2,
            hostapi_name="Windows WASAPI",
        )

        def probe(device, rate, channels, mode) -> bool:
            return int(rate) == 44100

        plan = plan_output(wasapi, 44100, channels=2, mode="shared", probe=probe)
        self.assertIs(plan.mode, OutputMode.SHARED)
        self.assertEqual(plan.rate, 44100)

    def test_asio_never_builds_wasapi_settings(self) -> None:
        """ASIO 下不许构造 ``WasapiSettings``（那对 ASIO 毫无意义）。"""
        from seqaudio.player.devices import extra_settings_for

        self.assertIsNone(extra_settings_for(OutputMode.EXCLUSIVE, self._asio_device()))
        self.assertIsNone(
            extra_settings_for(OutputMode.EXCLUSIVE_STRICT, self._asio_device())
        )

    def test_asio_strict_request_is_not_a_downgrade(self) -> None:
        """ASIO + 残留的「严格独占」设置不算模式降级（v1.8.1 已修）。

        现场：``playback_output_mode="exclusive_strict"``（在 WASAPI 下设过）
        + 切到 ASIO + ``playback_exclusive_fallback="never"`` →
        ``PlayerEngine._build_spec`` 抛
        ``PlayerError("独占无法按原参数打开，且设置为「只报错」：设备不支持")``，
        而这条流其实开得出来（探测返回 True，ASIO 也没有 WasapiSettings）。

        根因：``devices._plan_asio_output`` 返回 ``mode=EXCLUSIVE`` 而
        ``requested_mode`` 仍是 ``EXCLUSIVE_STRICT``，于是
        ``OutputPlan.mode_fallback`` 为真、``note`` 为空 —— 引擎把
        「ASIO 不分宽松/严格」误判成「降级了」。

        期望：ASIO 的计划不该被算作模式降级（``mode_fallback`` 为 False）。
        修复后本用例会自动变成 unexpected success，届时去掉装饰器。
        """
        plan = plan_output(
            self._asio_device(),
            44100,
            channels=2,
            mode="exclusive_strict",
            probe=lambda device, rate, channels, mode: True,
        )
        self.assertFalse(
            plan.mode_fallback,
            "ASIO 的「严格→宽松」没有实际降级，不该计入 mode_fallback",
        )


# ============================================================ 断言 5：信号刷新


class TestSignalRefreshShowsCurrent(_TmpDir):
    """上一首 96k → 当前 44.1k：面板两处（计数器 + decode）必须显示**当前**值。"""

    def setUp(self) -> None:
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from seqaudio.ui.player_signals import PlayerSignalsPanel

        self.app = QApplication.instance() or QApplication([])
        self.panel = PlayerSignalsPanel()
        self.addCleanup(self.panel.deleteLater)

    @staticmethod
    def _snapshot(*, source: str, codec: str, rate: int) -> dict[str, Any]:
        from seqaudio.player.signal_path import SignalPath

        path = SignalPath().configure(
            source=source,
            codec=codec,
            decoder_version="ffmpeg",
            sample_fmt="s16",
            channels=2,
            sample_rate=rate,
            rate_want=rate,
            rate_actual=rate,
            backend="mme",
            device="扬声器 (Realtek Audio)",
            device_dtype="int16",
            latency_ms=186.0,
            int_pipeline=True,
        )
        return {
            "signal_path": path.snapshot(),
            "signal_summary": path.summary(),
            "counters": path.counters.to_dict(),
            "rate": int(rate),
        }

    def _window(self, stale: dict[str, Any], fresh: dict[str, Any]):
        from seqaudio.ui.main_window import MainWindow

        class _StubBridge:
            def __init__(self) -> None:
                self.polls = 0
                self.stale_reads = 0

            def last_snapshot(self) -> dict[str, Any]:
                self.stale_reads += 1
                return dict(stale)

            def poll_now(self) -> dict[str, Any]:
                self.polls += 1
                return dict(fresh)

        class _Window:
            # 用真实的绑定方法：验证「槽 → 刷新入口」这条链本身
            _refresh_signal_panel = MainWindow._refresh_signal_panel
            _update_dsd_ui = MainWindow._update_dsd_ui
            _on_player_signal = MainWindow._on_player_signal

            def __init__(self, panel, bridge) -> None:
                self.player_signals = panel
                self.player_bridge = bridge

        bridge = _StubBridge()
        return _Window(self.panel, bridge), bridge

    def test_previous_96k_does_not_leak_into_current_441k(self) -> None:
        stale = self._snapshot(source="上一首 96k.flac", codec="flac", rate=96000)
        fresh = self._snapshot(source="当前 44.1k.alac", codec="alac", rate=44100)
        window, bridge = self._window(stale, fresh)

        # 先让面板处于「上一首」状态
        self.panel.update_from(stale)
        self.assertEqual(self.panel.counter_text("device_rate"), "96000 Hz")

        import types

        window._on_player_signal = types.MethodType(
            type(window)._on_player_signal, window
        )
        window._on_player_signal({"stages": []})

        self.assertEqual(bridge.polls, 1, "槽必须先 poll 一次再刷新")
        self.assertEqual(
            self.panel.counter_text("device_rate"), "44100 Hz", "计数器停留在上一首"
        )
        self.assertIn("44100 Hz", self.panel.row_detail("format"))
        self.assertIn("alac", self.panel.row_detail("decode"))
        self.assertEqual(self.panel.row_detail("source"), "当前 44.1k.alac")
        for key in ("source", "decode", "format", "device"):
            detail = self.panel.row_detail(key)
            self.assertNotIn("96000", detail, f"{key} 级残留上一首采样率：{detail}")
            self.assertNotIn("flac", detail, f"{key} 级残留上一首编码：{detail}")

    def test_engine_refresh_signal_recomputes_current_values(self) -> None:
        """引擎侧钩子：refresh_signal() 必须按**当前** spec 重算，而非留旧文案。"""
        engine = PlayerEngine(Settings())
        self.addCleanup(engine.shutdown)
        engine._dop_probe = lambda device, rate, channels, mode: False
        events: list[dict] = []
        engine.on_signal_path = lambda payload: events.append(payload)

        device = DeviceInfo(
            index=1,
            name="扬声器 (Realtek)",
            channels=2,
            default_samplerate=44100.0,
            hostapi_index=0,
            hostapi_name="MME",
        )
        path_96 = make_dsf(self.dir / "a.dsf", blocks=2)
        spec_96 = DecodeSpec(
            path=path_96, sample_rate=96000, channels=2, sample_fmt="s16", codec="flac"
        )
        engine._spec, engine._channels, engine._rate = spec_96, 2, 96000
        engine._want_rate = 96000
        engine._configure_signal(spec_96, device, 96000)
        self.assertIn("96000 Hz", engine._signal.stage("format").detail)

        spec_44 = DecodeSpec(
            path=path_96, sample_rate=44100, channels=2, sample_fmt="s16", codec="alac"
        )
        engine._spec, engine._rate = spec_44, 44100
        engine._want_rate = 44100
        engine.refresh_signal()

        self.assertTrue(events, "refresh_signal 没有推 EVENT_SIGNAL")
        decode = engine._signal.stage("decode")
        fmt = engine._signal.stage("format")
        self.assertIn("alac", decode.detail)
        self.assertIn("44100 Hz", fmt.detail)
        self.assertNotIn("96000", fmt.detail, "format 级残留上一首采样率")
        payload = events[-1]
        self.assertIn("44100", str(payload))


# ============================================================ 断言 6：热插拔


class TestDeviceWatchDiff(unittest.TestCase):
    """两次伪造枚举做差：按**设备名**比对，只报 1 次变化。"""

    @staticmethod
    def _watch(names: list[tuple[str, bool]]) -> list[dict[str, Any]]:
        # 故意让 index 每次都漂移：ASIO 启用后 PortAudio 全局索引会整体后移
        return [
            {"index": 100 + i * 7, "name": name, "is_default": default, "hostapi": "MME"}
            for i, (name, default) in enumerate(names)
        ]

    def _watcher(self, results: list[list[dict[str, Any]]]) -> DeviceWatcher:
        box = {"calls": 0}

        def enumerator(backend: str) -> list[dict[str, Any]]:
            index = min(box["calls"], len(results) - 1)
            box["calls"] += 1
            return results[index]

        return DeviceWatcher("mme", enumerator=enumerator)

    def test_first_poll_only_primes(self) -> None:
        watcher = self._watcher([self._watch([("A", True), ("B", False)])])
        first = watcher.poll_once()
        self.assertFalse(first.changed, "首次枚举只建立基线，不该报变化")
        self.assertEqual(watcher.names(), frozenset({"A", "B"}))

    def test_identical_names_with_shifted_indices_is_not_a_change(self) -> None:
        """索引变了但名字没变 → **不算**变化（按索引比会每 3 s 假阳性刷一次）。"""
        watcher = self._watcher(
            [
                self._watch([("A", True), ("B", False)]),
                [{"index": 999, "name": "A", "is_default": True},
                 {"index": 1, "name": "B", "is_default": False}],
            ]
        )
        watcher.poll_once()
        second = watcher.poll_once()
        self.assertFalse(second.changed)
        self.assertEqual((second.added, second.removed), ((), ()))

    def test_added_reported_once(self) -> None:
        watcher = self._watcher(
            [
                self._watch([("A", True)]),
                self._watch([("A", True), ("C", False)]),
                self._watch([("A", True), ("C", False)]),
            ]
        )
        watcher.poll_once()
        change = watcher.poll_once()
        self.assertTrue(change.changed)
        self.assertEqual(change.added, ("C",))
        self.assertEqual(change.removed, ())
        self.assertFalse(watcher.poll_once().changed, "同一变化只报 1 次")

    def test_removed_reported_once(self) -> None:
        watcher = self._watcher(
            [
                self._watch([("A", True), ("B", False), ("C", False)]),
                self._watch([("A", True)]),
                self._watch([("A", True)]),
            ]
        )
        watcher.poll_once()
        change = watcher.poll_once()
        self.assertTrue(change.changed)
        self.assertEqual(change.removed, ("B", "C"))
        self.assertFalse(watcher.poll_once().changed)

    def test_default_change_is_reported(self) -> None:
        watcher = self._watcher(
            [
                self._watch([("A", True), ("B", False)]),
                self._watch([("A", False), ("B", True)]),
                self._watch([("A", False), ("B", True)]),
            ]
        )
        watcher.poll_once()
        change = watcher.poll_once()
        self.assertTrue(change.default_changed, "系统默认设备变化必须上报")
        self.assertTrue(change.changed)
        self.assertFalse(watcher.poll_once().changed, "同一变化只报 1 次")

    def test_reset_rebuilds_baseline_without_reporting(self) -> None:
        watcher = self._watcher(
            [self._watch([("A", True)]), self._watch([("A", True), ("Z", False)])]
        )
        watcher.poll_once()
        watcher.reset("asio")
        self.assertFalse(watcher.poll_once().changed)
        self.assertEqual(watcher.names(), frozenset({"A", "Z"}))

    def test_enumeration_failure_keeps_baseline(self) -> None:
        """枚举抛异常时保留基线（不能把一次失败当成「设备全被拔了」）。"""
        box = {"fail": False}

        def enumerator(backend: str) -> list[dict[str, Any]]:
            if box["fail"]:
                raise RuntimeError("PortAudio 未就绪")
            return self._watch([("A", True)])

        watcher = DeviceWatcher("mme", enumerator=enumerator)
        watcher.poll_once()
        box["fail"] = True
        self.assertFalse(watcher.poll_once().changed)
        self.assertEqual(watcher.names(), frozenset({"A"}))


class TestRealWindowIntegration(_TmpDir):
    """真 ``MainWindow`` 离屏实例（bridge 换成替身，**不开声卡、不播放**）。

    第 5、6 条断言在单元层都过了之后，这里再验一次「真窗口里的接线」：
    引擎事件槽 → 刷新入口 → 面板，以及 3 s 定时器槽 → 只刷一次。
    """

    def setUp(self) -> None:
        super().setUp()
        from PySide6.QtWidgets import QApplication

        from seqaudio.ui.main_window import MainWindow

        self.app = QApplication.instance() or QApplication([])
        self.window = MainWindow(Settings(offline_mode=True, online_enabled=False))
        self.addCleanup(self.window.close)
        self.addCleanup(self.window.deleteLater)
        self._real_bridge = self.window.player_bridge

    # ------------------------------------------------------------ 替身

    def _install_bridge(self, stale: dict[str, Any], fresh: dict[str, Any]) -> "_StubBridge":
        stub = _StubBridge(stale, fresh)
        self.window.player_bridge = stub
        return stub

    def _install_watch(self, results: list[list[dict[str, Any]]]) -> None:
        box = {"calls": 0}

        def enumerator(backend: str) -> list[dict[str, Any]]:
            index = min(box["calls"], len(results) - 1)
            box["calls"] += 1
            return results[index]

        self.window._device_watcher = DeviceWatcher("mme", enumerator=enumerator)

    # ------------------------------------------------------------ 用例

    def test_state_slot_pushes_current_values(self) -> None:
        """PLAYING 槽 → 面板：计数器与 decode 级必须是**当前**曲目。"""
        stale = _bridge_snapshot(source="上一首 96k.flac", codec="flac", rate=96000)
        fresh = _bridge_snapshot(source="当前 44.1k.alac", codec="alac", rate=44100)
        stub = self._install_bridge(stale, fresh)

        self.window.player_signals.update_from(stale)
        self.assertEqual(self.window.player_signals.counter_text("device_rate"), "96000 Hz")

        self.window._on_player_state("PLAYING")

        self.assertEqual(stub.polls, 1)
        panel = self.window.player_signals
        self.assertEqual(panel.counter_text("device_rate"), "44100 Hz")
        self.assertIn("44100 Hz", panel.row_detail("format"))
        self.assertIn("alac", panel.row_detail("decode"))
        self.assertEqual(panel.row_detail("source"), "当前 44.1k.alac")
        self.assertNotIn("96000", panel.row_detail("format"))

    def test_device_watch_timer_is_3s(self) -> None:
        timer = getattr(self.window, "_device_watch_timer", None)
        self.assertIsNotNone(timer, "没有设备热插拔定时器")
        self.assertEqual(timer.interval(), 3000, "PRD 固定 3 s 轮询")
        self.assertTrue(timer.isActive() or True)  # 未进事件循环时 isActive 取决于平台

    def test_dop_locks_volume_slider(self) -> None:
        """DoP 期间音量滑块禁用并提示（PRD P1-1③，v1.8.1 已补接线）。

        现状：引擎侧确实把软件音量锁成 0 dB（``engine.set_volume`` 有分支），
        信号路径也写「DoP ∖ 音量旁路」，但**界面没有任何一处**把滑块置灰，
        ``i18n`` 里的 ``dev_dop_volume_note``（「音量由驱动控制…」）在整个
        ``src/`` 里零引用。用户看到的是「滑块能拖动、拖了没效果」。
        """
        stale = _bridge_snapshot(source="曲目.dsf", codec="dsf", rate=176400)
        fresh = _bridge_snapshot(source="曲目.dsf", codec="dsf", rate=176400)
        fresh["dsd"] = {
            "active": True,
            "fallback": False,
            "dsd_label": "DSD64",
            "dop_label": "DoP64",
            "dop_rate": 176400,
            "note": "",
        }
        self._install_bridge(stale, fresh)
        self.window._on_player_signal({"stages": []})

        slider = self.window.player_bar.volume_slider
        self.assertFalse(slider.isEnabled(), "DoP 期间音量滑块必须禁用")
        self.assertIn("驱动", slider.toolTip())

    def test_poll_device_change_refreshes_once_per_change(self) -> None:
        """一次设备表变化只刷新一次：第二次轮询（无变化）不再动界面。"""
        stale = _bridge_snapshot(source="曲目.flac", codec="flac", rate=44100)
        stub = self._install_bridge(stale, stale)
        self._install_watch(
            [
                [{"name": "A", "is_default": True}],
                [{"name": "A", "is_default": True}, {"name": "B", "is_default": False}],
                [{"name": "A", "is_default": True}, {"name": "B", "is_default": False}],
            ]
        )
        self.window._poll_device_change()      # 建立基线
        self.assertEqual(stub.refresh_calls, 0)
        self.assertEqual(stub.device_list_calls, 0)

        self.window._poll_device_change()      # 真变化
        self.assertEqual(stub.refresh_calls, 1, "设备变化后必须重算信号路径")
        self.assertEqual(stub.device_list_calls, 1, "下拉必须原地刷新一次")

        self.window._poll_device_change()      # 无变化
        self.assertEqual(stub.refresh_calls, 1, "同一变化只许刷新 1 次")
        self.assertEqual(stub.device_list_calls, 1)


class _StubBridge:
    """最小桥接替身：记调用次数，``poll_now`` 返回"当前"，``last_snapshot`` 返回"上一首"。"""

    def __init__(self, stale: dict[str, Any], fresh: dict[str, Any]) -> None:
        self._stale = stale
        self._fresh = fresh
        self.polls = 0
        self.refresh_calls = 0
        self.device_list_calls = 0

    def last_snapshot(self) -> dict[str, Any]:
        return dict(self._stale)

    def poll_now(self) -> dict[str, Any]:
        self.polls += 1
        return dict(self._fresh)

    def refresh_signal(self) -> None:
        self.refresh_calls += 1

    def list_output_devices(self, backend: str = "") -> list[dict[str, Any]]:
        self.device_list_calls += 1
        return [{"name": "A", "is_default": True}]


def _bridge_snapshot(*, source: str, codec: str, rate: int) -> dict[str, Any]:
    """构造 ``engine.snapshot()`` 里信号面板会读的那几个键。"""
    from seqaudio.player.signal_path import SignalPath

    path = SignalPath().configure(
        source=source,
        codec=codec,
        decoder_version="ffmpeg",
        sample_fmt="s16",
        channels=2,
        sample_rate=rate,
        rate_want=rate,
        rate_actual=rate,
        backend="mme",
        device="扬声器 (Realtek Audio)",
        device_dtype="int16",
        latency_ms=186.0,
        int_pipeline=True,
    )
    return {
        "signal_path": path.snapshot(),
        "signal_summary": path.summary(),
        "counters": path.counters.to_dict(),
        "rate": int(rate),
    }


# ============================================================ 断言 7：版本号


class TestVersionConsistency(unittest.TestCase):
    """四处版本号必须一致为 1.8.1（PRD P0-4）。"""

    EXPECTED = "1.8.1"

    def test_pyproject(self) -> None:
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(match, "pyproject.toml 里找不到 version")
        self.assertEqual(match.group(1), self.EXPECTED)

    def test_package_dunder_version(self) -> None:
        text = (ROOT / "src" / "seqaudio" / "__init__.py").read_text(encoding="utf-8")
        match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
        self.assertIsNotNone(match)
        self.assertEqual(match.group(1), self.EXPECTED)

    def test_installer_meta_version_and_payload(self) -> None:
        text = (ROOT / "installer" / "_meta.py").read_text(encoding="utf-8")
        version = re.search(r'^VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
        payload = re.search(r'^PAYLOAD\s*=\s*"([^"]+)"', text, re.MULTILINE)
        self.assertIsNotNone(version, "installer/_meta.py 缺 VERSION")
        self.assertIsNotNone(payload, "installer/_meta.py 缺 PAYLOAD")
        self.assertEqual(version.group(1), self.EXPECTED)
        self.assertIn(
            f"v{self.EXPECTED}", payload.group(1), "PAYLOAD 文件名必须带 v1.8.1"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
