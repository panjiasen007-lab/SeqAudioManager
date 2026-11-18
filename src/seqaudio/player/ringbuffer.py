"""单生产者/单消费者（SPSC）无锁 PCM 环形缓冲。

为什么不用 ``threading.Lock``
-----------------------------
消费者是 PortAudio 的实时回调线程。实时音频里任何一次可能被抢占的加锁，
都会让回调错过截止时间，表现就是爆音/欠载。而这里的访问模式天生简单：

* 只有解码线程**写**、只有回调线程**读**；
* 缓冲区开局按容量预分配，运行期**从不扩容**（没有内存分配、没有 GC 压力）。

因此用两个单调递增的整数游标就够了：写者先把样本写进数组，**再**发布
``_write_total``；读者先读 ``_write_total`` 再拷贝数据。CPython 里对 ``int``
属性的单次赋值是原子的（引用替换），读到的值也许「旧」，但绝不会「撕裂」，
旧值只会让读者少读几帧（下一轮补上），不会读到未写完的样本。

容量语义
--------
``available == _write_total - _read_total``，上限是 ``capacity``。
两个游标只增不减（``reset()`` 除外），因此不需要处理回绕乘法，
回绕只发生在取模得到的数组下标上。
"""

from __future__ import annotations

import logging

import numpy as np

log = logging.getLogger(__name__)


class PcmRingBuffer:
    """预分配的定长 PCM 环。样本布局固定为 ``(帧, 声道)``。

    Args:
        capacity_frames: 环形缓冲容量（帧）。应 ≥ 2 个回调块，否则一开流就欠载。
        channels: 声道数。
        dtype: numpy dtype 或字符串（``int16`` / ``int32`` / ``float32``）。
    """

    __slots__ = (
        "_buf",
        "_capacity",
        "_channels",
        "_dtype",
        "_read_total",
        "_write_total",
        "_starved_writes",
        "_partial_writes",
    )

    def __init__(self, capacity_frames: int, channels: int = 2, dtype: str = "int16") -> None:
        capacity = int(capacity_frames)
        if capacity <= 0:
            raise ValueError(f"环形缓冲容量必须为正，收到 {capacity_frames!r}")
        ch = int(channels)
        if ch <= 0:
            raise ValueError(f"声道数必须为正，收到 {channels!r}")

        self._channels = ch
        self._dtype = np.dtype(dtype)
        self._capacity = capacity
        # 一次性预分配：运行期不再申请内存（回调线程的安全前提）
        self._buf = np.zeros((capacity, ch), dtype=self._dtype)
        self._read_total = 0
        self._write_total = 0
        self._starved_writes = 0
        self._partial_writes = 0

    # ------------------------------------------------------------ 构造辅助

    @classmethod
    def from_seconds(
        cls, seconds: float, sample_rate: int, channels: int = 2, dtype: str = "int16"
    ) -> "PcmRingBuffer":
        """按「秒」构造缓冲：``容量帧 = 秒 × 采样率``（至少 1 帧）。"""
        frames = max(1, int(round(float(seconds) * int(sample_rate))))
        return cls(frames, channels=channels, dtype=dtype)

    # ------------------------------------------------------------ 只读属性

    @property
    def capacity(self) -> int:
        """容量（帧）。"""
        return self._capacity

    @property
    def channels(self) -> int:
        """声道数。"""
        return self._channels

    @property
    def dtype(self) -> np.dtype:
        """样本 dtype（由它派生 PortAudio 流格式，见 output.py）。"""
        return self._dtype

    @property
    def available(self) -> int:
        """可读帧数（消费者视角，保守值）。"""
        return self._write_total - self._read_total

    @property
    def free(self) -> int:
        """可写帧数（生产者视角，保守值）。"""
        return self._capacity - (self._write_total - self._read_total)

    @property
    def frames_written(self) -> int:
        """累计写入帧数（单调递增，用于位置/统计）。"""
        return self._write_total

    @property
    def frames_read(self) -> int:
        """累计读取帧数（单调递增）。"""
        return self._read_total

    @property
    def starved_writes(self) -> int:
        """因「环满」而整块写失败的次数（解码侧停滞的量化指标）。"""
        return self._starved_writes

    @property
    def partial_writes(self) -> int:
        """发生短写的次数（空间不足但非全满）。"""
        return self._partial_writes

    def frames_available(self) -> int:
        """``available`` 的方法形式，给习惯显式调用的地方用。"""
        return self.available

    def frames_free(self) -> int:
        """``free`` 的方法形式。"""
        return self.free

    # ------------------------------------------------------------ 写（唯一生产者）

    def write(self, frames: np.ndarray) -> int:
        """写入若干帧，返回**实际写入**帧数（空间不足时短写，绝不阻塞）。

        Args:
            frames: ``(n, channels)`` 数组；单声道也接受 ``(n,)``。

        Returns:
            实际写入帧数；环满时返回 0。
        """
        data = frames
        if data.ndim == 1:
            if self._channels != 1:
                # 立体声环收到单声道数据是明确的调用错误，宁可早失败
                raise ValueError(f"环形缓冲为 {self._channels} 声道，不接受一维数据")
            data = data.reshape(-1, 1)
        elif data.ndim != 2:
            raise ValueError(f"期望二维 PCM 数组，收到 {data.ndim} 维")
        if data.shape[1] != self._channels:
            raise ValueError(
                f"声道数不匹配：缓冲 {self._channels}，数据 {data.shape[1]}"
            )

        count = int(data.shape[0])
        if count <= 0:
            return 0
        free = self.free
        if free <= 0:
            self._starved_writes += 1
            return 0

        n = count if count <= free else free
        if n < count:
            self._partial_writes += 1

        start = self._write_total % self._capacity
        first = n if n <= self._capacity - start else self._capacity - start
        # 第一段：写到数组尾部；第二段：回绕到头部
        self._buf[start:start + first] = data[:first]
        if n > first:
            self._buf[0:n - first] = data[first:n]

        # 关键顺序：数据落地后再发布写游标，读者才可能看到这些样本
        self._write_total = self._write_total + n
        return n

    # ------------------------------------------------------------ 读（唯一消费者）

    def read_into(self, out: np.ndarray) -> int:
        """把最多 ``len(out)`` 帧读进 ``out``，返回实际读取帧数。

        调用方负责把剩余部分清零（欠载补零）。本方法**不分配**任何大对象，
        只做切片赋值，可以在 PortAudio 回调里安全调用。

        Args:
            out: ``(frames, channels)`` 或单声道时的 ``(frames,)`` 数组。
        """
        if out.ndim == 2 and out.shape[1] != self._channels:
            raise ValueError(
                f"输出声道数不匹配：缓冲 {self._channels}，目标 {out.shape[1]}"
            )
        if out.ndim not in (1, 2):
            raise ValueError(f"输出必须是 1 维或 2 维数组，收到 {out.ndim} 维")
        if out.ndim == 1 and self._channels != 1:
            raise ValueError("立体声缓冲不能读进一维数组")

        wanted = int(out.shape[0])
        if wanted <= 0:
            return 0
        available = self.available
        if available <= 0:
            return 0

        n = wanted if wanted <= available else available
        start = self._read_total % self._capacity
        first = n if n <= self._capacity - start else self._capacity - start

        if out.ndim == 1:
            out[:first] = self._buf[start:start + first, 0]
            if n > first:
                out[first:n] = self._buf[0:n - first, 0]
        else:
            out[:first] = self._buf[start:start + first]
            if n > first:
                out[first:n] = self._buf[0:n - first]

        # 关键顺序：拷完再推进读游标，写者据此判断空闲空间
        self._read_total = self._read_total + n
        return n

    # ------------------------------------------------------------ 生命周期

    def reset(self) -> None:
        """清空游标，回到「空环」状态。

        只能在**消费者已停**（输出流已 close）时调用 —— 切歌跨规格会重建
        整个环而不是复用，所以这里不做跨线程握手，靠调用时机保证。
        """
        self._read_total = 0
        self._write_total = 0
        self._starved_writes = 0
        self._partial_writes = 0

    def clear(self) -> None:
        """``reset()`` 的别名（架构文档 3.1 类图用的是 ``clear``）。"""
        self.reset()

    def __repr__(self) -> str:  # pragma: no cover - 仅日志/调试用
        return (
            f"<PcmRingBuffer {self._channels}ch {self._dtype} "
            f"{self.available}/{self._capacity} 帧>"
        )
