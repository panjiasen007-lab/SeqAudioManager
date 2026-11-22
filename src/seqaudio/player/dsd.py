"""DSD 位流直读与 DoP 打包（v1.8.1 · P1-1）。

为什么不用 ffmpeg
-----------------
ffmpeg 能把 DSD 解成 PCM，但那是「解」，不是「直出」：一旦过 ffmpeg，DSD 的
1 bit 流就被烧成了 176.4 kHz 的 PCM 样本，后面再没有任何办法还原。DoP
（DSD over PCM）走的是另一条路 —— 把 DSD 位流**原样**装进 24 bit PCM 帧里
（每帧 1 字节标记 + 2 字节数据），让支持 DoP 的 DAC 自己解回 DSD。这条路必须
绕开一切软件 DSP，也必须绕开 ffmpeg：所以本模块自己读 .dsf/.dff 的 chunk 头与
位流，产出的就是可以直接交给声卡的 int32 帧。

两个容器，两套字节序
--------------------
* **DSF**：小端头，``chunk size`` **含 12 字节头**（最容易错的一处）；位流按
  「块」交错 —— ``block0.ch0(block_bytes)  block0.ch1  block1.ch0 …``；每个
  字节 **LSB first**（低位先出）。
* **DFF**（DSDIFF）：大端头，``chunk size`` **不含**头（IFF 惯例，奇数长度补
  1 字节）；位流按字节帧交错（``ch0, ch1, ch0, ch1 …``），每个字节
  **MSB first**。

把这两件事搞混的后果不是「音质差一点」，而是整段噪声或者一声不出。所以
``probe_dsd()`` 只认文件头的 magic（扩展名只作兜底），解析失败一律抛
``DsdDecodeError``，由引擎明确回退 PCM —— 绝不猜。

DoP 帧的确切布局（DoP 1.1 规范）
--------------------------------
::

    24 bit 帧 = [ marker(8) | payload16(16) ]        # 标记在最高字节
    payload16 = (rev8(a) << 8) | rev8(b)             # a、b 为同声道连续两字节（LSB first）
    marker    = 0x05 / 0xFA 逐样本交替                 # 每个样本都交替，不是 8+8
    int32     = (marker << 24) | (payload16 << 8)    # 左对齐，低 8 位补 0

规范依据三处一致（dsd-guide.com 白皮书 / ESS ES9020 datasheet / XMOS lib_xua）：
标记占**最高** 8 bit、**每个样本**交替 0x05 与 0xFA、同一帧的各声道取同一标记、
剩下的低 16 bit 才是 DSD 数据（最老的位在 t0）。32 bit 字宽时接收端会丢弃
**低 8 bit**（ESS datasheet 原话 "the 8 LSBs are dropped"），所以 24 bit 字必须
**左对齐**放进 int32 —— 这也是本模块唯一的容器布局。

位序为什么还要反转
------------------
DSF 的位流是**低位先出**，而 DoP 载荷是**高位先出**的 PCM 样本：不 ``rev8``
就等于把每个字节里的 8 个 DSD 位倒着送出去。DFF 本来就是 MSB first，不需要反转
（``lsb_first=False``）。两条路径都有逐位往返用例看住。

载体率
------
``dop_rate = dsd_rate // 16``（每帧承载 16 bit 数据）：DSD64 → 176400、
DSD128 → 352800、DSD256 → 705600。

关于「硬件是否锁定」
--------------------
本机 TempoTec ASIO 声明支持 176400/352800，但默认输出可能正被其它进程占用
（``DeviceBusyError -9985``），所以**真实 DAC 的锁定无法在单测里验证**。这里的
做法是：布局严格按规范写、用往返断言把「打包/解包自洽」钉死；载体率探测失败
就按设计回退 PCM 并留 note，绝不放宽断言去凑绿。

线程纪律
--------
与 ``decoder.FfmpegDecoder`` 完全同构（``start/stop/is_alive/read_block/
at_eof/position_sec/returncode``，另有 ``pause_feeding/resume_feeding/
stopped/dropped_frames``），因此 ``engine._make_decoder`` 只需一个分支，
``_teardown_playback`` / ``_wait_prefill`` 零改动。推模式下由**一条**线程读
文件、写环形缓冲；PortAudio 回调绝不碰文件句柄。位流读取用一把 ``_io_lock``
保护，``seek()`` 与读线程因此可以共存。

**不做** Native DSD：PortAudio 没有 DSD 端点，需要厂商私有 ASIO 扩展。碰到
只支持 Native DSD 的路径就按「回退 PCM」处理，绝不伪造。
"""

from __future__ import annotations

import logging
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, Callable, Optional

import numpy as np

from .constants import (
    DECODE_STALL_TIMEOUT_SEC,
    DECODER_JOIN_TIMEOUT_SEC,
    DEFAULT_CHANNELS,
    DOP_FRAMES_PER_CYCLE,
    DOP_MARKER,
    DOP_RATE_OF,
    DSD_RATES,
    FEED_STALL_SLEEP_SEC,
)

if TYPE_CHECKING:  # 只在类型检查时引入，避免 dsd -> decoder -> dsd 的循环导入
    from .decoder import DecodeSpec
    from .ringbuffer import PcmRingBuffer

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- 常量
#
# DSD 位率表、DoP 载体率表、标记与周期都住在 ``player/constants.py``
# （"只允许 import，不允许另写"），这里只做两件事：给它们起便于阅读的别名，
# 并补上本模块自己的打包细节。

DSD_RATE_64 = DSD_RATES[0]
DSD_RATE_128 = DSD_RATES[1]
DSD_RATE_256 = DSD_RATES[2]
#: DSD512 只用于**标签**推导（本机载体率 1411.2 kHz 必然不可用，走回退），
#: 刻意不加进 ``constants.DSD_RATES``：那三档才是本版认真处理的档位。
DSD_RATE_512 = DSD_RATE_256 * 2
#: DoP 每一帧的标记字节（第 1 个、第 2 个…）
DOP_MARKER_FIRST = DOP_MARKER[0]
DOP_MARKER_SECOND = DOP_MARKER[1]
#: 24 bit 帧内标记字节的位置（**最高**字节）与 16 bit 载荷的位置
DOP_MARKER_SHIFT = 24
DOP_PAYLOAD_SHIFT = 8
#: 逐样本交替的标记表（DoP 1.1：每个样本都交替，**不是** 8×0x05 + 8×0xFA）。
#:
#: 曾有一版「架构审计」声称是 8+8 分组，与 dsd-guide.com 白皮书、ESS ES9020
#: datasheet、XMOS lib_xua 三处原文都不符，已按规范改为逐样本交替。
DOP_MARKER_TABLE = tuple(
    DOP_MARKER_FIRST if index % 2 == 0 else DOP_MARKER_SECOND
    for index in range(DOP_FRAMES_PER_CYCLE)
)

#: DSF 头的固定尺寸（"DSD " chunk）
_DSF_HEADER_SIZE = 28
#: DSF "fmt " chunk 的固定尺寸（含 12 字节头）
_DSF_FMT_SIZE = 52
#: 任何 RIFF/DSDIFF chunk 头的字节数（id 4 + size 8）
_CHUNK_HEADER_SIZE = 12


class DsdDecodeError(RuntimeError):
    """DSD 位流不可用（文件头非法、块大小缺失、载体率不匹配、打不开文件）。

    与 ``decoder.DecodeError`` / ``devices.DeviceError`` 并列而不继承它们：
    ``dsd.py`` 刻意不 import ``devices``（避免循环），引擎用
    ``except (DecodeError, DsdDecodeError)`` 统一兜底。
    """


# ---------------------------------------------------------------- 标签


def format_rate_khz(rate: int) -> str:
    """采样率 → 给人看的短串（``176400`` → ``"176.4 kHz"``）。

    信号路径的 DoP 文案要用「@176.4 kHz」这种写法；整数 kHz 不补 ``.0``
    （``48000`` → ``"48 kHz"``），与界面既有文案保持一致。
    """
    value = int(rate or 0)
    if value <= 0:
        return "—"
    if value % 1000 == 0:
        return f"{value // 1000} kHz"
    return f"{value / 1000:.1f} kHz"


def dsd_label_for(dsd_rate: int) -> str:
    """DSD 位率 → ``"DSD64"`` / ``"DSD128"`` / … ；认不出就带数值。"""
    value = int(dsd_rate or 0)
    multiple = value // 44100 if value > 0 else 0
    if value > 0 and value % 44100 == 0 and multiple >= 1:
        return f"DSD{multiple}"
    return f"DSD {value} Hz" if value > 0 else "DSD"


def dop_label_for(dsd_rate: int) -> str:
    """DSD 位率 → 对应的 DoP 名字（``"DoP64"`` / ``"DoP128"`` / ``"DoP256"``）。"""
    value = int(dsd_rate or 0)
    multiple = value // 44100 if value > 0 else 0
    if value > 0 and value % 44100 == 0 and multiple >= 1:
        return f"DoP{multiple}"
    return "DoP"


def dop_rate_for(dsd_rate: int) -> int:
    """DSD 位率 → DoP 载体率（``dsd_rate // 16``）。"""
    value = int(dsd_rate or 0)
    return value // 16 if value > 0 else 0


# ---------------------------------------------------------------- 位序表


def _build_rev8() -> np.ndarray:
    """构造 8 位位序反转查表（用位运算，**不用**字符串格式化）。

    256 项一次性算好：位流读取是按兆字节计的，``f"{x:08b}"[::-1]`` 在热路径上
    比查表慢两个数量级。
    """
    table = np.zeros(256, dtype=np.uint8)
    for value in range(256):
        v = value
        v = ((v & 0xF0) >> 4) | ((v & 0x0F) << 4)
        v = ((v & 0xCC) >> 2) | ((v & 0x33) << 2)
        v = ((v & 0xAA) >> 1) | ((v & 0x55) << 1)
        table[value] = np.uint8(v)
    return table


#: numpy 侧查表（矢量化打包用）
REV8_TABLE = _build_rev8()
#: Python 侧标量查表（调试/单点用）
REV8 = tuple(int(item) for item in REV8_TABLE)
#: 标记查表（按帧序号取模）
_MARKER_LOOKUP = np.array(DOP_MARKER_TABLE, dtype=np.int32)


def rev8(value: int) -> int:
    """单字节位序反转（``0b00000110`` → ``0b01100000``）。"""
    return REV8[int(value) & 0xFF]


# ---------------------------------------------------------------- 格式描述


@dataclass(frozen=True)
class DsdFormat:
    """一个 DSD 文件的位流描述（``probe_dsd()`` 的产物，只读）。

    ``block_bytes`` 是 DSF 的「每声道一块多少字节」（通常 4096）；DFF 没有
    分块概念，恒为 ``0``，此时位流是逐字节帧交错的。

    ``sample_count`` 以**每声道位数**计（不是字节、也不是帧）：DSF 头里本来
    就是这个口径，DFF 由 ``data_bytes * 8 / channels`` 反推。
    """

    path: Path
    container: str            # "dsf" / "dff"
    dsd_rate: int
    channels: int
    block_bytes: int
    lsb_first: bool
    sample_count: int
    data_offset: int          # 位流在文件里的起始偏移
    data_bytes: int           # 位流总字节数（全声道合计）

    # ------------------------------------------------------------ 派生

    @property
    def duration_sec(self) -> float:
        """按 DSD 位率算时长（秒）；位率非法时返回 0。"""
        if self.dsd_rate <= 0 or self.sample_count <= 0:
            return 0.0
        return self.sample_count / float(self.dsd_rate)

    @property
    def dop_rate(self) -> int:
        """DoP 载体率 = ``dsd_rate // 16``（DSD64→176400、DSD128→352800…）。"""
        return dop_rate_for(self.dsd_rate)

    @property
    def dop_label(self) -> str:
        return dop_label_for(self.dsd_rate)

    @property
    def dsd_label(self) -> str:
        return dsd_label_for(self.dsd_rate)

    @property
    def dsd_multiple(self) -> int:
        """``2822400`` → ``64``（日志里写「DSD64 → DoP @176400 Hz」用）。"""
        return self.dsd_rate // 44100 if self.dsd_rate > 0 else 0

    @property
    def bytes_per_channel(self) -> int:
        if self.channels <= 0:
            return 0
        return self.data_bytes // self.channels

    @property
    def block_frames(self) -> int:
        """一个 DSF 块能出多少 DoP 帧（每帧吃 2 字节）；DFF 返回 0。"""
        return self.block_bytes // 2 if self.block_bytes > 1 else 0

    # ------------------------------------------------------------ 对齐

    def align_bits(self, bits: int) -> int:
        """把「每声道 DSD 位数」向下对齐到容器允许的起点。

        为什么要对齐：DSF 的位流按块交错，只有块起点能安全地当作「一首歌的某个
        位置」；DFF 虽然逐字节交错，但 DoP 每帧要吃 2 字节，起点必须落在偶数
        字节帧上。不对齐就会出现「某个字节被拆到两帧里」的错位，听起来是爆音。
        """
        value = max(0, int(bits))
        if self.block_bytes > 1:
            block_bits = self.block_bytes * 8
            return (value // block_bits) * block_bits
        return (value // 16) * 16

    def byte_offset(self, aligned_bits: int) -> int:
        """对齐后的「每声道位数」→ 相对位流起点的字节偏移（全声道合计）。"""
        bits = max(0, int(aligned_bits))
        per_channel_bytes = bits // 8
        if self.block_bytes > 1:
            blocks = per_channel_bytes // self.block_bytes
            return blocks * self.block_bytes * max(1, self.channels)
        frames = per_channel_bytes // 2
        return frames * 2 * max(1, self.channels)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "container": self.container,
            "dsd_rate": int(self.dsd_rate),
            "channels": int(self.channels),
            "block_bytes": int(self.block_bytes),
            "lsb_first": bool(self.lsb_first),
            "sample_count": int(self.sample_count),
            "duration_sec": round(self.duration_sec, 3),
            "dop_rate": int(self.dop_rate),
            "dop_label": self.dop_label,
            "dsd_label": self.dsd_label,
        }


# ---------------------------------------------------------------- DoP 打包


class DopPacker:
    """把 DSD 位流字节对打成 DoP 帧（int32 容器）。

    Args:
        channels: 声道数（每帧每声道出一个字）。
        lsb_first: 源字节是否低位先出。DSF 为 ``True``（要 ``rev8``），
            DFF 为 ``False``（已经是 MSB first，直接拼）。

    相位 ``_phase`` 必须跨块连续：标记的 0x05/0xFA 交替是按**帧**走的，
    每块都从头开始会让接收端每一块都重新对齐 —— 有的 DAC 能容错，有的直接
    失锁（表现为周期性「咔」）。所以 ``pack()`` 连续调用与一次性拼好等价，
    这也是单元测试里断言的一条。
    """

    def __init__(self, channels: int = DEFAULT_CHANNELS, lsb_first: bool = True) -> None:
        self._channels = max(1, int(channels or DEFAULT_CHANNELS))
        self._lsb_first = bool(lsb_first)
        self._phase = 0

    # ------------------------------------------------------------ 属性

    @property
    def channels(self) -> int:
        return self._channels

    @property
    def lsb_first(self) -> bool:
        return self._lsb_first

    @property
    def phase(self) -> int:
        """下一个输出帧在 16 帧周期里的位置（0–15）。"""
        return self._phase

    def reset(self) -> None:
        """把相位归零（seek / 重新起播时用）。"""
        self._phase = 0

    # ------------------------------------------------------------ 打包

    def pack(self, pairs: np.ndarray) -> np.ndarray:
        """``(frames, channels, 2) uint8`` → ``(frames, channels) int32``。

        第二个轴上的两个字节属于**同一个声道**的连续两个 DSD 字节
        （``pairs[:, ch, 0]`` 先出）。返回值是可直接写进 int32 环形缓冲的
        DoP 帧：``bit24–31`` 标记、``bit8–23`` 数据、低 8 位补 0（左对齐）。

        注意：标记 0xFA 会置 bit31，因此按**有符号** int32 读是负数
        （``0xFA000000`` → ``-100663296``）。这是位模式，不是数值 ——
        ``paInt32`` 流里只有位模式有意义，接收端丢弃低 8 位后取 24 bit 字。
        需要数值比较时用 ``words.view(np.uint32)`` 或 ``word & 0xFFFFFFFF``，
        **不要**「修」成正数（那会改掉标记位）。

        Raises:
            ValueError: 形状不对（形状错了继续算只会产出一堆噪声）。
        """
        data = np.asarray(pairs)
        if data.ndim != 3 or data.shape[2] != 2:
            raise ValueError(f"DoP 打包需要 (frames, channels, 2) 的字节对，收到 {data.shape}")
        if data.shape[1] != self._channels:
            raise ValueError(
                f"DoP 打包声道数不匹配：packer {self._channels}，输入 {data.shape[1]}"
            )
        frames = int(data.shape[0])
        if frames <= 0:
            return np.zeros((0, self._channels), dtype=np.int32)

        first = data[:, :, 0]
        second = data[:, :, 1]
        if self._lsb_first:
            # DSF：位流低位先出，PCM 帧高位先出 → 每字节内部要反转一次
            first = REV8_TABLE[first]
            second = REV8_TABLE[second]
        payload = (first.astype(np.int32) << 8) | second.astype(np.int32)

        index = (np.arange(frames, dtype=np.int64) + int(self._phase)) % DOP_FRAMES_PER_CYCLE
        marker = _MARKER_LOOKUP[index]
        # 标记在最高字节、数据在其下 16 bit、低 8 位补 0（32bit 字宽时接收端丢弃 LSB 8 位）
        out = (marker[:, None] << DOP_MARKER_SHIFT) | (payload << DOP_PAYLOAD_SHIFT)
        self._phase = int((self._phase + frames) % DOP_FRAMES_PER_CYCLE)
        return out.astype(np.int32, copy=False)

    def pack_bytes(self, dsd: np.ndarray) -> np.ndarray:
        """``(bytes, channels) uint8`` → ``(bytes // 2, channels) int32``。

        方便「按字节流思考」的调用方；内部先隔行重组为字节对再走 ``pack()``，
        保证两条入口的标记相位判定完全一致。
        """
        data = np.asarray(dsd)
        if data.ndim != 2:
            raise ValueError(f"pack_bytes 需要 (bytes, channels)，收到 {data.shape}")
        if data.shape[1] != self._channels:
            raise ValueError(
                f"DoP 打包声道数不匹配：packer {self._channels}，输入 {data.shape[1]}"
            )
        usable = (int(data.shape[0]) // 2) * 2
        paired = data[:usable].reshape(-1, 2, self._channels).transpose(0, 2, 1)
        return self.pack(paired)


def dop_unpack(words: np.ndarray, lsb_first: bool = True) -> np.ndarray:
    """DoP 字 → 原始 DSD 字节对（``(frames, channels, 2) uint8``）。

    只在**自检与单元测试**里用：生产路径永远是从位流到声卡单方向。有了它才能
    断言「打包再解包逐位一致」，而不是靠肉眼看十六进制。
    """
    data = np.asarray(words, dtype=np.int32)
    if data.ndim != 2:
        raise ValueError(f"dop_unpack 需要 (frames, channels)，收到 {data.shape}")
    payload = (data >> DOP_PAYLOAD_SHIFT) & 0xFFFF
    first = ((payload >> 8) & 0xFF).astype(np.uint8)
    second = (payload & 0xFF).astype(np.uint8)
    if lsb_first:
        first = REV8_TABLE[first]
        second = REV8_TABLE[second]
    return np.stack([first, second], axis=-1)


def dop_markers(words: np.ndarray) -> np.ndarray:
    """取出每个 DoP 帧的标记字节（``(frames, channels) uint8``，取自最高字节）。"""
    data = np.asarray(words, dtype=np.int32)
    return ((data >> DOP_MARKER_SHIFT) & 0xFF).astype(np.uint8)


def dop_int24_bytes(words: np.ndarray) -> np.ndarray:
    """DoP 字 → 24 bit 打包的 3 字节帧（``(frames, channels, 3) uint8``）。

    给「输出链路是 int24（3 字节/样本）」的情形用：字节顺序就是
    ``marker, payload_hi, payload_lo``，与 DoP 1.1 的 24 bit 帧逐字节对应。
    本项目的输出走 int32（左对齐），这条路留在库里是为了让格式对照可执行、
    可断言 —— 而不是靠文档里一句口头约定。
    """
    data = np.asarray(words, dtype=np.int32)
    if data.ndim != 2:
        raise ValueError(f"dop_int24_bytes 需要 (frames, channels)，收到 {data.shape}")
    marker = ((data >> DOP_MARKER_SHIFT) & 0xFF).astype(np.uint8)
    payload = (data >> DOP_PAYLOAD_SHIFT) & 0xFFFF
    high = ((payload >> 8) & 0xFF).astype(np.uint8)
    low = (payload & 0xFF).astype(np.uint8)
    return np.stack([marker, high, low], axis=-1)


def dop_marker_ok(words: np.ndarray) -> bool:
    """标记序列是否严格符合 ``DOP_MARKER_TABLE``（自检/测试用）。"""
    markers = dop_markers(words)
    if markers.size == 0:
        return True
    expected = np.array(
        [_MARKER_LOOKUP[index % DOP_FRAMES_PER_CYCLE] for index in range(markers.shape[0])],
        dtype=np.uint8,
    )
    return bool(np.all(markers == expected[:, None]))


# ---------------------------------------------------------------- 文件头解析


def probe_dsd(path: Path | str) -> DsdFormat:
    """读 DSD 文件头，返回位流描述。**不起子进程、不读位流**。

    按 magic 判定容器（``DSD ``=DSF、``FRM8``=DFF），扩展名只在 magic 缺失时
    兜底：改错扩展名的 DSF 不会被当成 PCM 硬解。

    Raises:
        DsdDecodeError: 文件不存在、magic 不认识、必需 chunk 缺失或取值非法。
    """
    target = Path(path)
    if not target.is_file():
        raise DsdDecodeError(f"文件不存在：{target}")
    try:
        with target.open("rb") as fp:
            magic = fp.read(4)
    except OSError as exc:
        raise DsdDecodeError(f"打不开文件：{target.name}（{exc}）") from exc

    if magic == b"DSD ":
        return _probe_dsf(target)
    if magic == b"FRM8":
        return _probe_dff(target)
    suffix = target.suffix.lstrip(".").lower()
    if suffix == "dsf":
        return _probe_dsf(target)
    if suffix in ("dff", "dsdiff"):
        return _probe_dff(target)
    raise DsdDecodeError(f"不是 DSD 位流文件（magic={magic!r}）：{target.name}")


def _probe_dsf(path: Path) -> DsdFormat:
    """解析 DSF（``DSD `` chunk + ``fmt `` + ``data``）。

    三个坑，缺一不可：小端、``size`` **含 12 字节头**、``blocksizePerChannel``
    是「每声道每块字节数」而不是总字节数。
    """
    try:
        fp = path.open("rb")
    except OSError as exc:
        raise DsdDecodeError(f"打不开 DSF：{path.name}（{exc}）") from exc
    try:
        head = fp.read(_DSF_HEADER_SIZE)
        if len(head) < _DSF_HEADER_SIZE or head[:4] != b"DSD ":
            raise DsdDecodeError(f"DSF 头不完整（{len(head)} 字节）：{path.name}")
        declared_total = int.from_bytes(head[12:20], "little")

        fmt_head = fp.read(_DSF_FMT_SIZE)
        if len(fmt_head) < _DSF_FMT_SIZE or fmt_head[:4] != b"fmt ":
            raise DsdDecodeError(f"DSF 缺少 fmt chunk：{path.name}")
        fmt_size = int.from_bytes(fmt_head[4:12], "little")
        if fmt_size < _DSF_FMT_SIZE:
            raise DsdDecodeError(f"fmt chunk 长度非法（{fmt_size}）：{path.name}")

        # 载荷 40 字节：6 个 u32 + u64 sampleCount + u32 blockSize + u32 reserved
        (
            format_version,
            format_id,
            channel_type,
            channels,
            dsd_rate,
            bits_per_sample,
            sample_count,
            block_bytes,
            _reserved,
        ) = struct.unpack("<IIIIIIQII", fmt_head[_CHUNK_HEADER_SIZE:_DSF_FMT_SIZE])

        data_head = fp.read(_CHUNK_HEADER_SIZE)
        if len(data_head) < _CHUNK_HEADER_SIZE or data_head[:4] != b"data":
            raise DsdDecodeError(f"DSF 缺少 data chunk：{path.name}")
        data_size = int.from_bytes(data_head[4:12], "little")
        data_offset = fp.tell()
    except OSError as exc:
        raise DsdDecodeError(f"读取 DSF 头失败：{path.name}（{exc}）") from exc
    finally:
        fp.close()

    if channels <= 0 or channels > 8:
        raise DsdDecodeError(f"DSF 声道数非法（{channels}）：{path.name}")
    if dsd_rate <= 0:
        raise DsdDecodeError(f"DSF 采样率非法（{dsd_rate}）：{path.name}")
    if block_bytes <= 0:
        raise DsdDecodeError(f"DSF 缺少 blockSizePerChannel，无法按块解交错：{path.name}")
    if dsd_rate % 44100 != 0:
        # DSD 位率必须是 44.1 kHz 的整数倍；不在集合里也能放（例如 DSD1024），
        # 但要在日志里留痕，出问题时好定位
        log.info("DSF 位率 %d Hz 不是 44.1 kHz 的整数倍（未知 DSD 档位）", dsd_rate)
    if bits_per_sample not in (1, 8):
        log.warning(
            "DSF bitsPerSample=%d（DSD 通常为 1），按 1 bit 位流处理：%s",
            bits_per_sample, path.name,
        )
    if format_id not in (0,):
        log.debug("DSF formatID=%d（0=DSD raw），formatVersion=%d", format_id, format_version)

    data_bytes = int(data_size) - _CHUNK_HEADER_SIZE
    file_size = path.stat().st_size if path.is_file() else 0
    remain = max(0, file_size - int(data_offset))
    if data_bytes <= 0 or data_bytes > remain:
        # 头里的 total_file_size 与实际不一致（截断文件/工具写坏）→ 以实际为准
        if data_bytes != remain:
            log.warning(
                "DSF data chunk 声明 %s 字节，实际可用 %s 字节，按实际处理：%s",
                data_bytes, remain, path.name,
            )
        data_bytes = remain
    if data_bytes <= 0:
        raise DsdDecodeError(f"DSF 没有音频数据：{path.name}")
    if declared_total and declared_total != file_size:
        log.debug("DSF 声明总长 %d，实际 %d：%s", declared_total, file_size, path.name)
    if channel_type not in (0, 1, 2):
        log.debug("DSF channelType=%d：%s", channel_type, path.name)

    if sample_count <= 0:
        sample_count = data_bytes * 8 // max(1, channels)

    return DsdFormat(
        path=path,
        container="dsf",
        dsd_rate=int(dsd_rate),
        channels=int(channels),
        block_bytes=int(block_bytes),
        lsb_first=True,
        sample_count=int(sample_count),
        data_offset=int(data_offset),
        data_bytes=int(data_bytes),
    )


def _probe_dff(path: Path) -> DsdFormat:
    """解析 DFF/DSDIFF（``FRM8`` → ``PROP``/``SND `` → ``DSD ``）。

    与 DSF 的三处差别：**大端**、chunk ``size`` **不含**头、奇数长度要补 1 字节
    （IFF 惯例）。位流是逐字节帧交错且 MSB first，所以 ``lsb_first=False``。
    """
    dsd_rate = 0
    channels = 0
    data_offset = 0
    data_bytes = 0
    try:
        with path.open("rb") as fp:
            head = fp.read(16)
            if len(head) < 16 or head[:4] != b"FRM8":
                raise DsdDecodeError(f"DFF 头不完整（{len(head)} 字节）：{path.name}")
            if head[12:16] != b"DSD ":
                raise DsdDecodeError(
                    f"FRM8 的 form type 不是 'DSD '（{head[12:16]!r}）：{path.name}"
                )
            while True:
                chunk_head = fp.read(_CHUNK_HEADER_SIZE)
                if len(chunk_head) < _CHUNK_HEADER_SIZE:
                    break
                chunk_id = chunk_head[:4]
                chunk_size = int.from_bytes(chunk_head[4:12], "big")
                if chunk_id == b"DSD ":
                    data_offset = fp.tell()
                    data_bytes = chunk_size
                    break
                if chunk_id == b"PROP":
                    payload = fp.read(chunk_size)
                    rate, ch = _parse_dff_prop(payload)
                    dsd_rate = dsd_rate or rate
                    channels = channels or ch
                    if chunk_size % 2:
                        fp.seek(1, 1)          # IFF：奇数长度补 1 字节
                    continue
                # 其余 chunk（FVER/DIIN/COMT…）与本模块无关，跳过
                fp.seek(chunk_size + (chunk_size % 2), 1)
    except OSError as exc:
        raise DsdDecodeError(f"读取 DFF 头失败：{path.name}（{exc}）") from exc

    if data_bytes <= 0 or data_offset <= 0:
        raise DsdDecodeError(f"DFF 缺少 DSD 音频 chunk：{path.name}")
    if dsd_rate <= 0:
        raise DsdDecodeError(f"DFF 缺少 FS（采样率）：{path.name}")
    if channels <= 0:
        raise DsdDecodeError(f"DFF 缺少 CHNL（声道数）：{path.name}")

    file_size = path.stat().st_size if path.is_file() else 0
    remain = max(0, file_size - int(data_offset))
    if data_bytes > remain:
        log.warning(
            "DFF 声明音频 %s 字节，实际可用 %s 字节，按实际处理：%s",
            data_bytes, remain, path.name,
        )
        data_bytes = remain
    sample_count = data_bytes * 8 // channels
    return DsdFormat(
        path=path,
        container="dff",
        dsd_rate=int(dsd_rate),
        channels=int(channels),
        block_bytes=0,               # DFF 不分块
        lsb_first=False,             # MSB first
        sample_count=int(sample_count),
        data_offset=int(data_offset),
        data_bytes=int(data_bytes),
    )


def _parse_dff_prop(payload: bytes) -> tuple[int, int]:
    """``PROP`` 载荷 → ``(采样率, 声道数)``；认不出返回 ``(0, 0)``。

    ``PROP`` 结构：``"SND " + size(8)`` 然后是一串子 chunk，其中 ``FS  `` 给
    采样率、``CHNL`` 给声道数（前 2 字节）。
    """
    if len(payload) < 12 or payload[:4] != b"SND ":
        return 0, 0
    rate = 0
    channels = 0
    pos = 12                                  # "SND " + 8 字节长度
    while pos + _CHUNK_HEADER_SIZE <= len(payload):
        sub_id = payload[pos:pos + 4]
        sub_size = int.from_bytes(payload[pos + 4:pos + 12], "big")
        body_start = pos + _CHUNK_HEADER_SIZE
        body = payload[body_start:body_start + sub_size]
        if sub_id == b"FS  " and len(body) >= 4:
            rate = int.from_bytes(body[:4], "big")
        elif sub_id == b"CHNL" and len(body) >= 2:
            channels = int.from_bytes(body[:2], "big")
        pos = body_start + sub_size + (sub_size % 2)
    return rate, channels


# ---------------------------------------------------------------- 位流直读


class DsdSource:
    """一条 DSD 会话：读位流 → DoP 打包 → 写环形缓冲。

    与 ``decoder.FfmpegDecoder`` **同构**，方法名、语义、线程模型一一对应：

    ==========================  ==================================================
    ``start()``                 解析文件头、定位到 ``spec.start_ms``（块对齐）、
                                推模式下起读线程
    ``read_block()``            产出一块 ``(frames, channels) int32`` DoP 帧；
                                EOF 返回 ``None``
    ``seek(sec)``               块对齐定位（±5 s 内不爆音，量化误差 < 一块）
    ``is_alive() / at_eof``     是否还在产出 / 是否已到末尾
    ``position_sec``            已产出的音频位置（秒）
    ``stop()``                  停线程、关文件；重复调用安全
    ==========================  ==================================================

    Args:
        spec: ``DecodeSpec``；必须带 ``dop=True`` 与 ``dop_rate``（引擎在
            ``_build_spec`` 里已经判定过，见 ``engine._resolve_dop``）。
        ring: 推模式目标环；``None`` 时走拉模式（测试/工具自己 ``read_block``）。
        on_error / on_eof: 与 ffmpeg 路径同签名，**在读线程里**被调用，
            实现方不得在回调里操作 Qt 控件。
        prober: 注入的文件头解析函数（测试用，默认 ``probe_dsd``）。
    """

    def __init__(
        self,
        spec: "DecodeSpec",
        ring: Optional["PcmRingBuffer"] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_eof: Optional[Callable[[], None]] = None,
        *,
        prober: Optional[Callable[[Path], DsdFormat]] = None,
    ) -> None:
        self.spec = spec
        self.ring = ring
        self._prober = prober or probe_dsd
        self._on_error = on_error
        self._on_eof = on_eof

        self._fp: Optional[BinaryIO] = None
        self._fmt: Optional[DsdFormat] = None
        self._thread: Optional[threading.Thread] = None
        self._io_lock = threading.Lock()
        self._stop = threading.Event()
        self._eof = threading.Event()
        # 喂数据闸门：暂停时关掉，让读线程「等」而不是把位流丢掉
        self._gate = threading.Event()
        self._gate.set()

        self._channels = max(1, int(getattr(spec, "channels", 0) or DEFAULT_CHANNELS))
        self._rate = int(getattr(spec, "dop_rate", 0) or 0)
        self._packer = DopPacker(self._channels, lsb_first=True)
        self._pending = np.zeros((self._channels, 0), dtype=np.uint8)
        self._base_sec = 0.0
        self._base_bits = 0
        self._frames_produced = 0
        self._frames_pushed = 0
        self._dropped_frames = 0
        self._stall_reported = False
        self._started = False
        self._returncode: Optional[int] = None
        self._max_frames = 0

    # ------------------------------------------------------------ 只读属性

    @property
    def format(self) -> Optional[DsdFormat]:
        """本次会话的位流描述（``start()`` 之前为 ``None``）。"""
        return self._fmt

    @property
    def stopped(self) -> bool:
        """是否被调用方 ``stop()`` 主动停止（区别于读到文件末尾）。"""
        return self._stop.is_set()

    @property
    def returncode(self) -> Optional[int]:
        """``0`` = 正常读到末尾；``None`` = 还没结束或被主动停止。

        刻意不发明错误码：DSD 位流读失败会走 ``on_error`` 回调并终止线程。
        """
        return self._returncode

    @property
    def frames_pushed(self) -> int:
        """真正写进环形缓冲的帧数。"""
        return self._frames_pushed

    @property
    def dropped_frames(self) -> int:
        """环满而被丢掉的帧数（解码停滞的量化指标）。"""
        return self._dropped_frames

    @property
    def position_sec(self) -> float:
        """当前已产出的音频位置（秒，含 seek 后的起点）。

        用「已读出的帧数」而不是「已写入环的帧数」：引擎的位置时钟走
        ``ring.frames_read``，这里只做对账，两者不该互相拖累。

        **单位是秒**，与 ``QueueItem.duration``（秒，float）及引擎的
        ``duration_ms()``（毫秒整数）换算口径一致：1 个 DoP 帧 = 1/176400 s
        音频，因此 ``frames / dop_rate`` 就是真实播放时间（不是 16 倍快进）。
        """
        rate = float(self._rate or 1)
        return self._base_sec + self._frames_produced / rate

    @property
    def duration_sec(self) -> float:
        """本次会话的时长（**秒**，float），与 ``QueueItem.duration`` 同单位。

        三个口径必须对齐，否则播放条会跳或直接跑飞：

        * ``DsdFormat.sample_count`` 是**每声道位数**，除以 ``dsd_rate`` 得秒；
        * CUE 子轨给了 ``duration_ms`` 时以它为准（与 ffmpeg 的 ``-t`` 同语义）；
        * 引擎内部用毫秒（``PlayerEngine.duration_ms()``），自己乘 1000，
          本模块**不**返回毫秒 —— 混用单位是这类进度条 bug 的根源。
        """
        fmt = self._fmt
        if fmt is None:
            return 0.0
        if self._max_frames > 0 and self._rate > 0:
            return self._max_frames / float(self._rate)
        return float(fmt.duration_sec)

    @property
    def at_eof(self) -> bool:
        """位流是否已经读完（读过一次 ``None`` 之后恒为 ``True``）。"""
        return self._eof.is_set()

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """解析文件头、定位、启动读线程（推模式）。

        Raises:
            DsdDecodeError: 文件头非法、载体率/声道数与 ``spec`` 不一致
                （不一致继续播只会出一段错速噪声，宁可当场报错）。
        """
        if self._started:
            raise DsdDecodeError("DSD 解码器已经启动过，请新建实例")
        path = Path(getattr(self.spec, "path", "") or "")
        if not path.is_file():
            raise DsdDecodeError(f"文件不存在：{path}")

        fmt = self._prober(path)
        requested_dop = int(getattr(self.spec, "dop_rate", 0) or 0)
        if requested_dop and requested_dop != fmt.dop_rate:
            raise DsdDecodeError(
                f"DoP 载体率不匹配：文件 {fmt.dop_rate} Hz，请求 {requested_dop} Hz"
            )
        sample_rate = int(getattr(self.spec, "sample_rate", 0) or 0)
        if sample_rate and sample_rate != fmt.dop_rate:
            raise DsdDecodeError(
                f"DoP 载体率不匹配：文件 {fmt.dop_rate} Hz，输出 {sample_rate} Hz"
            )
        channels = int(getattr(self.spec, "channels", 0) or 0)
        if channels and channels != fmt.channels:
            raise DsdDecodeError(
                f"声道数不匹配：文件 {fmt.channels} ch，输出 {channels} ch"
            )

        self._fmt = fmt
        self._channels = int(fmt.channels)
        self._rate = int(fmt.dop_rate)
        self._packer = DopPacker(self._channels, lsb_first=fmt.lsb_first)
        self._pending = np.zeros((self._channels, 0), dtype=np.uint8)
        duration_ms = int(getattr(self.spec, "duration_ms", 0) or 0)
        self._max_frames = (
            int(round(duration_ms * self._rate / 1000.0)) if duration_ms > 0 else 0
        )

        try:
            self._fp = path.open("rb")
        except OSError as exc:
            raise DsdDecodeError(f"打不开位流：{path.name}（{exc}）") from exc

        self._started = True
        self._stop.clear()
        self._eof.clear()
        self._gate.set()
        self._returncode = None
        self._frames_produced = 0
        self._frames_pushed = 0
        self._dropped_frames = 0
        self._stall_reported = False

        # 起播偏移（seek / CUE 子轨）按容器块对齐
        start_sec = float(getattr(self.spec, "start_sec", 0.0) or 0.0)
        self.seek(start_sec)

        log.info(
            "DSD%d → DoP @%d Hz（%d ch）", fmt.dsd_multiple, fmt.dop_rate, fmt.channels
        )
        if self.ring is not None:
            self._thread = threading.Thread(
                target=self._pump, name="seqaudio-dsd-pump", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """停止读线程并关闭文件；重复调用安全。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=DECODER_JOIN_TIMEOUT_SEC)
        self._thread = None
        fp = self._fp
        with self._io_lock:
            self._fp = None
        if fp is not None:
            try:
                fp.close()
            except OSError as exc:  # pragma: no cover - 极端竞态
                log.debug("关闭 DSD 位流失败：%s", exc)

    def is_alive(self) -> bool:
        """是否还能继续产出（读线程活着，或拉模式下文件还开着）。"""
        thread = self._thread
        if thread is not None:
            return thread.is_alive()
        return bool(self._started and self._fp is not None and not self._eof.is_set())

    # ------------------------------------------------------------ 定位

    def seek(self, position_sec: float) -> None:
        """定位到 ``position_sec``（按容器块边界向下对齐）。

        为什么不做「精确到样本」的定位：DSF 的位流按块交错，块中间不是任何声道
        的合法起点；硬要精确就只能整块读进来再丢字节，代价是每次 seek 多读一块
        （DSD64 下 11.6 ms 的数据），收益为零 —— PRD 只要求 ±5 s 不爆音。
        对齐误差 < 1 块（DSD64 ≈ 11.6 ms / DSD128 ≈ 5.8 ms），``position_sec``
        报的是**对齐后**的真实起点，不虚报。
        """
        fmt = self._fmt or self._prober(Path(getattr(self.spec, "path", "") or ""))
        self._fmt = fmt
        target = max(0.0, float(position_sec or 0.0))
        bits = int(target * fmt.dsd_rate)
        aligned_bits = fmt.align_bits(bits)
        offset = int(fmt.data_offset) + int(fmt.byte_offset(aligned_bits))
        with self._io_lock:
            fp = self._fp
            if fp is None:
                raise DsdDecodeError("位流尚未打开，无法定位")
            fp.seek(offset, 0)
            self._pending = np.zeros((self._channels, 0), dtype=np.uint8)
            self._packer.reset()
            self._base_bits = int(aligned_bits)
            self._base_sec = aligned_bits / float(fmt.dsd_rate) if fmt.dsd_rate else 0.0
        self._eof.clear()
        log.debug(
            "DSD seek → %.3f s（块对齐 %.4f s，文件偏移 %d 字节）",
            target, self._base_sec, offset,
        )

    # ------------------------------------------------------------ 读块

    def read_block(self) -> Optional[np.ndarray]:
        """产出下一块 DoP 帧（``(frames, channels) int32``）；EOF 返回 ``None``。

        块长取 ``spec.block_frames``（与 ffmpeg 路径同一个值），保证引擎的
        「预填两块」「位置时钟」两处算法对两条通路都成立。
        """
        fmt = self._fmt
        if fmt is None or self._fp is None:
            return None
        frames = max(1, int(getattr(self.spec, "block_frames", 1024) or 1024))
        if self._max_frames > 0:
            remaining = int(self._max_frames) - int(self._frames_produced)
            if remaining <= 0:
                self._returncode = 0
                self._eof.set()                 # 拉模式也要如实报 EOF
                return None
            frames = min(frames, remaining)

        need = frames * 2                       # 每帧每声道吃 2 字节
        if not self._ensure(need):
            available = int(self._pending.shape[1])
            frames = available // 2
            if frames <= 0:
                self._returncode = 0
                self._eof.set()                 # 同上：`None` 与 `at_eof` 必须一致
                return None
            need = frames * 2

        pending = self._pending
        pairs = pending[:, :need].reshape(self._channels, frames, 2).transpose(1, 0, 2)
        self._pending = pending[:, need:]
        words = self._packer.pack(pairs)
        self._frames_produced += int(words.shape[0])
        if self._pending.shape[1] < 2 and self._max_frames and self._frames_produced >= self._max_frames:
            self._returncode = 0
        return words

    def _ensure(self, need: int) -> bool:
        """保证每声道待处理字节 ≥ ``need``；到文件末尾且不够时返回 ``False``。"""
        if self._pending.shape[1] >= need:
            return True
        while self._pending.shape[1] < need:
            chunk = self._read_group(need - int(self._pending.shape[1]))
            if chunk is None or chunk.shape[1] == 0:
                return False
            if self._pending.shape[1] == 0:
                self._pending = chunk
            else:
                self._pending = np.concatenate((self._pending, chunk), axis=1)
        return True

    def _read_group(self, min_bytes: int) -> Optional[np.ndarray]:
        """读一段位流并**解交错**成 ``(channels, n) uint8``。

        * DSF：整块组读入（``block_bytes × channels``），reshape 后转置 ——
          块内是同声道连续字节，块间才是交错，因此必须整块读。
        * DFF：按声道数成组读（一个字节帧 = 各声道各 1 字节），按 4 KiB 向上
          取整以减少系统调用。
        """
        fmt = self._fmt
        if fmt is None:
            return None
        channels = max(1, int(fmt.channels))
        if fmt.block_bytes > 1:
            group = int(fmt.block_bytes) * channels
        else:
            frames = max(1, (max(1, int(min_bytes)) + channels - 1) // channels)
            group = ((frames * channels + 4095) // 4096) * 4096
            group -= group % channels
        with self._io_lock:
            fp = self._fp
            if fp is None or self._stop.is_set():
                return None
            try:
                raw = fp.read(group)
            except OSError as exc:
                self._report_error(f"读取 DSD 位流失败：{exc}")
                self._returncode = None
                return None
        if not raw:
            self._returncode = 0                # 自然读到末尾
            return None

        if fmt.block_bytes > 1:
            block = int(fmt.block_bytes)
            usable = len(raw) - (len(raw) % (block * channels))
            if usable <= 0:
                # 不足一组块：正常文件不该出现（DSF 的 data chunk 一定是整数块），
                # 出现了说明文件被截断，丢掉尾部比播出一段错位噪声好
                log.debug("DSD 尾部不足一组块（%d 字节），已丢弃", len(raw))
                self._returncode = 0
                return None
            if usable != len(raw):
                log.debug("DSD 尾部 %d 字节不足一组块，已丢弃", len(raw) - usable)
            buf = np.frombuffer(raw[:usable], dtype=np.uint8)
            blocks = usable // (block * channels)
            return buf.reshape(blocks, channels, block).transpose(1, 0, 2).reshape(
                channels, blocks * block
            )

        usable = len(raw) - (len(raw) % channels)
        if usable <= 0:
            return None
        buf = np.frombuffer(raw[:usable], dtype=np.uint8)
        return buf.reshape(-1, channels).T

    # ------------------------------------------------------------ 线程体

    def _pump(self) -> None:
        """读线程体：读块 → 写环，环满则短暂自旋。"""
        try:
            while not self._stop.is_set():
                block = self.read_block()
                if block is None:
                    break
                self._push_block(block)
        except BaseException as exc:  # noqa: BLE001 - 线程里必须兜住一切
            log.exception("DSD 读线程异常退出")
            self._report_error(f"DSD 读线程异常：{exc}")
        finally:
            self._eof.set()
            if self._returncode is None and not self._stop.is_set():
                self._returncode = 0
            log.debug(
                "DSD 读完：%s（%d 帧，退出码 %s）",
                Path(getattr(self.spec, "path", "")).name,
                self._frames_pushed,
                self._returncode,
            )
            if self._on_eof is not None:
                try:
                    self._on_eof()
                except BaseException:  # noqa: BLE001 - 回调实现方的问题不能拖垮线程
                    log.exception("on_eof 回调抛异常")

    def _push_block(self, block: np.ndarray) -> None:
        """把一块 DoP 帧完整写进环；空间不足时自旋等待，超时丢剩余部分。"""
        ring = self.ring
        if ring is None:  # pragma: no cover - 只会在拉模式误调
            return
        offset = 0
        total = int(block.shape[0])
        blocked_since: Optional[float] = None
        while offset < total:
            if self._stop.is_set():
                return
            if not self._gate.is_set():
                # 暂停中：等引擎恢复。耗住的是读线程，PortAudio 回调不受影响
                self._gate.wait(FEED_STALL_SLEEP_SEC * 10)
                blocked_since = None
                continue
            written = ring.write(block[offset:])
            if written > 0:
                offset += written
                self._frames_pushed += written
                blocked_since = None
                continue
            now = time.monotonic()
            if blocked_since is None:
                blocked_since = now
            elif now - blocked_since > DECODE_STALL_TIMEOUT_SEC:
                dropped = total - offset
                self._dropped_frames += dropped
                if not self._stall_reported:
                    self._stall_reported = True
                    log.warning(
                        "环形缓冲 %.0f ms 内没有腾出空间，丢弃 %d 帧（DSD 解码停滞）",
                        DECODE_STALL_TIMEOUT_SEC * 1000, dropped,
                    )
                return
            time.sleep(FEED_STALL_SLEEP_SEC)

    def _report_error(self, message: str) -> None:
        log.error("%s", message)
        if self._on_error is None:
            return
        try:
            self._on_error(message)
        except BaseException:  # noqa: BLE001
            log.exception("on_error 回调抛异常")

    # ------------------------------------------------------------ 暂停喂数据

    def pause_feeding(self) -> None:
        """暂停向环形缓冲喂数据（暂停播放时用）。"""
        self._gate.clear()

    def resume_feeding(self) -> None:
        """恢复向环形缓冲喂数据。"""
        self._gate.set()

    def __repr__(self) -> str:  # pragma: no cover - 日志/调试
        fmt = self._fmt
        if fmt is None:
            return f"<DsdSource {Path(getattr(self.spec, 'path', '')).name} 未启动>"
        return (
            f"<DsdSource {fmt.container.upper()} {fmt.dsd_label} "
            f"{fmt.dop_rate} Hz {fmt.channels}ch>"
        )


__all__ = [
    "DSD_RATE_64", "DSD_RATE_128", "DSD_RATE_256", "DSD_RATE_512",
    "DSD_RATES", "DOP_RATE_OF",
    "DOP_MARKER_FIRST", "DOP_MARKER_SECOND", "DOP_FRAMES_PER_CYCLE",
    "DOP_MARKER_SHIFT", "DOP_PAYLOAD_SHIFT",
    "DOP_MARKER_TABLE",
    "DsdDecodeError", "DsdFormat", "DsdSource", "DopPacker",
    "probe_dsd", "dop_unpack", "dop_markers", "dop_int24_bytes", "dop_marker_ok",
    "rev8", "REV8", "REV8_TABLE",
    "format_rate_khz", "dsd_label_for", "dop_label_for", "dop_rate_for",
]
