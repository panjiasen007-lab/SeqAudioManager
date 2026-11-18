"""DSP 固定链：增益 → 10 段 EQ → 斜坡（全流程就地处理 float32）。

为什么这条链必须就地、无分配、无三角函数
----------------------------------------
``DspChain.process()`` 是在 PortAudio 回调线程里被调用的（见 output.py 的回调
七条禁令）。回调里一旦分配内存或做 O(N·段数) 的三角函数，就会在几百毫秒一次
的时机里抖动出爆音；所以：

* 增益只是**一次标量乘法**，0 dB 时连乘都不做（零拷贝直通）；
* EQ 的系数、合并频响全部在 **GUI 线程**（``prepare`` / ``set_gains``）算好，
  回调只读取一个已算好的复数数组引用（整体替换 = 原子）；
* 斜坡用一个**预分配**的包络数组做乘法，绝不用 ``np.arange``/``np.linspace``。

EQ 为什么不是逐样本的 IIR 循环
------------------------------
二阶 IIR（RBJ 峰值滤波器）本身是逐样本递推的。Python 里 1024 帧 × 10 段 ×
2 声道 = 2 万次解释器级迭代，单次回调要几毫秒且全程持有 GIL —— 那才是真正
违反「回调不做重活」的做法。这里改用**重叠保留法（overlap-save）**：把 10 段
biquad 的级联频响在 FFT 频点上逐点相乘，一次 IFFT 得到整块输出。

数学上等价：biquad 的 H(z) 在每个频点上取精确值，窗口长度 8192 / 跳距 1024
意味着「等效冲激响应」被截断到 7168 帧（44.1k 下 162 ms）。31 Hz 最低段在
162 ms 内已衰减约 −97 dB，时间混叠远低于任何可听阈值。代价是 numpy 的
``rfft``/``irfft`` 内部会申请临时数组（约 130 KB/块）—— 这一点无法用纯 numpy
消除（pocketfft 没有可复用的 plan API），因此 **EQ 默认关闭**，直通路径仍然
是零分配的。

不做的事
--------
* 交叉淡变与无缝：弹性项，T05 再说（本期 ``playback_crossfade_sec`` 只影响标注）；
* 音量渐变：产品要求本期音量是「立即生效 + 0 dB 旁路」，渐变属于淡变范畴；
* 整数通路：定标/饱和处理留到 v1.7，本期统一浮点（``playback_int_pipeline``
  只影响解码侧取样格式与信号路径标注）。
"""

from __future__ import annotations

import logging
import math
from typing import Any, Optional, Sequence

import numpy as np

from .constants import (
    BLOCK_FRAMES_DEFAULT,
    DB_FLOOR_MUTE,
    EQ_BANDS_HZ,
    EQ_MAX_DB,
    EQ_MIN_DB,
    EQ_Q,
    RAMP_MS_DEFAULT,
    RAMP_MS_MAX,
    RAMP_MS_MIN,
)

log = logging.getLogger(__name__)


def db_to_linear(db: float) -> float:
    """dB → 线性增益；``-60 dB`` 及以下按静音处理（返回 0.0）。"""
    value = float(db)
    if value <= DB_FLOOR_MUTE:
        return 0.0
    return float(10.0 ** (value / 20.0))


def int_pipeline_allowed(settings: Any, volume_db: Optional[float] = None) -> bool:
    """整数通路是否**真的**可行（与「DSP 是否会改样本」交叉校验）。

    架构文档 FR-DEC-009 说得很清楚：整数通路只在「音量旁路、无 EQ、无淡变」时
    才成立 —— 一旦有任何一级要动样本，就必须在浮点上做（定点乘增益要额外缩放
    与饱和处理，EQ 的 biquad 更是浮点算法）。

    这个判断以前散在引擎里，QA 实测发现漏了：``int_pipeline=True`` +
    ``dsp_bypass=False`` + 软件音量/EQ 时，环形缓冲是 int16，回调里
    ``buf *= np.float32(...)`` 在 numpy 2.x 直接抛 ``UFuncOutputCastingError``，
    异常被回调兜底吞掉 → **整场静音，界面只有计数器在动**。所以把裁决收敛成
    这一个函数，解码器（决定样本格式）与引擎（决定要不要挂 DSP 钩子）共用它，
    谁都不许自己再推一遍。

    Args:
        settings: ``Settings``（只读，缺字段按默认值兜底）。
        volume_db: 覆盖设置里的音量（引擎用运行期实际值）。
    """
    wants_int = bool(
        getattr(settings, "playback_int_pipeline", False)
        or getattr(settings, "playback_bit_perfect", False)
    )
    if not wants_int:
        return False
    # 交叉淡变必须浮点（本期默认 0，但别留个定时炸弹）
    if float(getattr(settings, "playback_crossfade_sec", 0.0) or 0.0) > 0.0:
        return False
    if not bool(getattr(settings, "playback_dsp_bypass", True)):
        mode = str(getattr(settings, "playback_volume_mode", "bypass") or "bypass")
        db = float(
            volume_db
            if volume_db is not None
            else (getattr(settings, "playback_volume_db", 0.0) or 0.0)
        )
        if mode == "software" and abs(db) > 1e-9:
            return False
        if bool(getattr(settings, "playback_eq_enabled", False)):
            return False
    return True


def _next_pow2(value: int) -> int:
    """不小于 ``value`` 的最小 2 的幂（FFT 长度取 2 的幂最快）。"""
    size = 1
    target = max(1, int(value))
    while size < target:
        size <<= 1
    return size


class GainStage:
    """软件音量：一次标量乘法，0 dB 时完全不动缓冲区。"""

    def __init__(self, db: float = 0.0) -> None:
        self._db = 0.0
        self._linear = 1.0
        self._muted = False
        self.set_db(db)

    @property
    def db(self) -> float:
        return self._db

    @property
    def linear(self) -> float:
        return self._linear

    @property
    def active(self) -> bool:
        """是否真的在改样本（0 dB 且未静音时返回 False → 零拷贝）。"""
        return self._muted or abs(self._linear - 1.0) > 1e-9

    def set_db(self, db: float) -> None:
        value = max(-120.0, min(24.0, float(db)))
        self._db = value
        self._linear = db_to_linear(value)
        self._muted = value <= DB_FLOOR_MUTE

    def process(self, buf: np.ndarray) -> None:
        """就地乘增益；不分配。"""
        if not self.active:
            return
        if self._muted:
            buf.fill(0)
            return
        buf *= np.float32(self._linear)


class BiquadEq:
    """10 段图形 EQ：RBJ 峰值滤波器级联 + 重叠保留 FFT 实现。

    Args:
        gains_db: 10 段增益（dB），长度不符时截断/补零。
        bands_hz: 中心频率。
        q: 品质因数（ISO 倍频程用 1.41）。
    """

    def __init__(
        self,
        gains_db: Optional[Sequence[float]] = None,
        bands_hz: Sequence[float] = EQ_BANDS_HZ,
        q: float = EQ_Q,
    ) -> None:
        self._bands = tuple(float(f) for f in bands_hz)
        self._q = float(q)
        self._gains: tuple[float, ...] = self._normalize_gains(gains_db)
        self._sample_rate = 0
        self._block_frames = BLOCK_FRAMES_DEFAULT
        self._enabled = False

        # ---- prepare() 之后才有效 ----
        self._fft_size = 0
        self._hop = 0
        self._hist_len = 0
        self._response: Optional[np.ndarray] = None  # (bins,) complex128，整体替换
        self._wrap: Optional[np.ndarray] = None      # (fft, ch) float32 预分配
        self._hist: Optional[np.ndarray] = None      # (hist_len, ch) float32
        self._channels = 0

    # ------------------------------------------------------------ 配置

    def _normalize_gains(self, gains: Optional[Sequence[float]]) -> tuple[float, ...]:
        values = list(gains or [])
        count = len(self._bands)
        cleaned: list[float] = []
        for index in range(count):
            raw = values[index] if index < len(values) else 0.0
            try:
                value = float(raw)
            except (TypeError, ValueError):
                value = 0.0
            cleaned.append(max(EQ_MIN_DB, min(EQ_MAX_DB, value)))
        return tuple(cleaned)

    @property
    def gains(self) -> list[float]:
        return list(self._gains)

    @property
    def prepared(self) -> bool:
        return self._response is not None

    @property
    def enabled(self) -> bool:
        return self._enabled and self.prepared

    @property
    def active(self) -> bool:
        """启用且至少有一段非 0 dB 才算「真的改了音频」。"""
        return self.enabled and any(abs(g) > 1e-9 for g in self._gains)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def set_gains(self, gains: Sequence[float]) -> None:
        """更新 10 段增益（GUI 线程调用）：先算新频响，再整体替换引用。"""
        self._gains = self._normalize_gains(gains)
        if self._sample_rate > 0:
            self._rebuild_response()

    def set_block_frames(self, block_frames: int) -> None:
        """回调块变化时重算窗口（只在开流前调用）。"""
        frames = int(block_frames) if int(block_frames) > 0 else BLOCK_FRAMES_DEFAULT
        if frames == self._block_frames and self._response is not None:
            return
        self._block_frames = frames
        if self._sample_rate > 0:
            self.prepare(self._sample_rate, self._channels or 2)

    def prepare(self, sample_rate: int, channels: int = 2) -> None:
        """按采样率与声道数分配缓冲并计算频响（GUI 线程，可分配）。"""
        rate = int(sample_rate)
        if rate <= 0:
            raise ValueError(f"采样率必须为正，收到 {sample_rate!r}")
        ch = max(1, int(channels))
        self._sample_rate = rate
        self._channels = ch

        hop = self._block_frames
        # 8 倍块长的窗口：等效冲激响应足够长，31 Hz 段的时间混叠低到听不见
        self._fft_size = _next_pow2(max(4096, hop * 8))
        self._hop = hop
        self._hist_len = self._fft_size - hop
        self._wrap = np.zeros((self._fft_size, ch), dtype=np.float32)
        self._hist = np.zeros((self._hist_len, ch), dtype=np.float32)
        self._rebuild_response()
        log.debug(
            "EQ 就绪：%d Hz %d ch，fft=%d hop=%d 段数=%d",
            rate, ch, self._fft_size, hop, len(self._bands),
        )

    def reset_state(self) -> None:
        """清空滤波器历史（切歌/seek 后调用，避免拖尾串到新曲目）。"""
        if self._hist is not None:
            self._hist.fill(0)
        if self._wrap is not None:
            self._wrap.fill(0)

    # ------------------------------------------------------------ 系数与频响

    def coefficients(self) -> list[tuple[float, float, float, float, float, float]]:
        """每段的 RBJ 峰值滤波器系数 ``(b0,b1,b2,a1,a2)``（已按 a0 归一）。"""
        result: list[tuple[float, float, float, float, float, float]] = []
        rate = self._sample_rate
        if rate <= 0:
            return result
        for freq, gain_db in zip(self._bands, self._gains):
            f0 = min(freq, rate * 0.49)  # 超过 Nyquist 的频点在低采样率下没有意义
            a_amp = 10.0 ** (gain_db / 40.0)
            w0 = 2.0 * math.pi * f0 / rate
            cos_w0 = math.cos(w0)
            alpha = math.sin(w0) / (2.0 * self._q)
            a0 = 1.0 + alpha / a_amp
            b0 = (1.0 + alpha * a_amp) / a0
            b1 = (-2.0 * cos_w0) / a0
            b2 = (1.0 - alpha * a_amp) / a0
            a1 = (-2.0 * cos_w0) / a0
            a2 = (1.0 - alpha / a_amp) / a0
            result.append((b0, b1, b2, a1, a2))
        return result

    def _rebuild_response(self) -> None:
        """把 10 段 biquad 的级联频响算到 FFT 频点上（GUI 线程）。"""
        size = self._fft_size
        if size <= 0:
            return
        bins = size // 2 + 1
        omega = 2.0 * np.pi * np.arange(bins, dtype=np.float64) / float(size)
        z1 = np.exp(-1j * omega)
        z2 = z1 * z1
        response = np.ones(bins, dtype=np.complex128)
        for b0, b1, b2, a1, a2 in self.coefficients():
            numerator = b0 + b1 * z1 + b2 * z2
            denominator = 1.0 + a1 * z1 + a2 * z2
            response *= numerator / denominator
        self._response = response  # 引用整体替换：回调看到的永远是完整数组

    # ------------------------------------------------------------ 处理

    def process(self, buf: np.ndarray) -> None:
        """就地滤波（重叠保留法）；``buf`` 形状 ``(frames, channels)``。"""
        response = self._response
        wrap = self._wrap
        hist = self._hist
        if response is None or wrap is None or hist is None:
            return
        if buf.ndim != 2 or buf.shape[1] != self._channels:
            raise ValueError(
                f"EQ 期望 ({self._channels} 声道) 的二维缓冲，收到 {buf.shape}"
            )

        total = int(buf.shape[0])
        if total <= 0:
            return
        hop = self._hop
        offset = 0
        while offset < total:
            count = min(hop, total - offset)
            self._process_chunk(buf[offset:offset + count], count)
            offset += count

    def _process_chunk(self, chunk: np.ndarray, count: int) -> None:
        """单块重叠保留：窗口 = 历史 + 当前块，取 IFFT 尾部 ``hop`` 有效样本。"""
        wrap = self._wrap
        hist = self._hist
        response = self._response
        assert wrap is not None and hist is not None and response is not None
        hop = self._hop
        hist_len = self._hist_len

        wrap[:hist_len] = hist
        wrap[hist_len:hist_len + count] = chunk
        if count < hop:
            # 不足一块时补零：补的是「静音」，不改变输出语义
            wrap[hist_len + count:hist_len + hop] = 0.0

        spectrum = np.fft.rfft(wrap, axis=0)
        spectrum *= response[:, None]
        filtered = np.fft.irfft(spectrum, n=self._fft_size, axis=0)
        chunk[:] = filtered[hist_len:hist_len + count]

        # 历史窗口右移：保留本次窗口的最后 hist_len 个输入样本
        hist[:] = wrap[self._fft_size - hist_len:]


class RampController:
    """切歌/暂停/seek 处的 1~5 ms 线性斜坡，用来消掉波形突变的爆音。

    斜坡**不含淡变语义**（交叉淡变是另一个模块）：它只是把「突然从 0.8 跳到 0」
    变成 5 ms 内平滑降到 0，人耳听不出，但能避免扬声器"啪"一声。
    """

    def __init__(self, ramp_ms: int = RAMP_MS_DEFAULT) -> None:
        self._ramp_ms = 0
        self._sample_rate = 0
        self._block_frames = BLOCK_FRAMES_DEFAULT
        self._gain = 1.0
        self._target = 1.0
        self._step = 0.0
        self._ramp_frames = 0
        self._active = False
        self._index: Optional[np.ndarray] = None   # 预分配：0..block_frames-1
        self._env: Optional[np.ndarray] = None     # 预分配包络
        self.set_ramp_ms(ramp_ms)

    # ---- 配置 ----

    @property
    def ramp_ms(self) -> int:
        return self._ramp_ms

    @property
    def gain(self) -> float:
        return self._gain

    @property
    def active(self) -> bool:
        return self._active

    @property
    def muted(self) -> bool:
        """淡出已到位（0）——此后每块都要写静音，直到 ``arm("in")``。"""
        return self._target <= 0.0 and self._gain <= 0.0

    def set_ramp_ms(self, ramp_ms: int) -> None:
        self._ramp_ms = max(RAMP_MS_MIN, min(RAMP_MS_MAX, int(ramp_ms)))

    def prepare(self, sample_rate: int, block_frames: int) -> None:
        rate = int(sample_rate)
        frames = int(block_frames) if int(block_frames) > 0 else BLOCK_FRAMES_DEFAULT
        self._sample_rate = rate
        self._block_frames = frames
        self._index = np.arange(frames, dtype=np.float32)
        self._env = np.ones(frames, dtype=np.float32)
        self._ramp_frames = max(1, int(round(rate * self._ramp_ms / 1000.0)))

    # ---- 控制 ----

    def arm(self, direction: str) -> None:
        """``"in"`` 淡入到 1.0 / ``"out"`` 淡出到 0.0（无采样率时直接到位）。"""
        target = 1.0 if str(direction).lower() == "in" else 0.0
        if self._index is None:
            self._gain = target
            self._target = target
            self._active = False
            return
        if abs(self._gain - target) < 1e-6:
            self._gain = target
            self._target = target
            self._active = False
            return
        self._target = target
        remaining = self._ramp_frames
        if remaining <= 0:
            self._gain = target
            self._active = False
            return
        self._step = (target - self._gain) / float(remaining)
        self._active = True

    def settle(self) -> None:
        """立即到位（停止时用：反正马上要关流，不必再等斜坡）。"""
        self._gain = self._target
        self._active = False

    def reset(self) -> None:
        self._gain = 1.0
        self._target = 1.0
        self._active = False

    # ---- 处理 ----

    def process(self, buf: np.ndarray) -> None:
        if not self._active:
            if self.muted and buf.size:
                # 淡出走完之后必须**继续**写静音：斜坡不是"一次性装饰"，
                # 否则从淡出结束到关流/暂停之间的那几块会原音漏出去（爆音）。
                buf.fill(0)
            return
        if self._env is None or self._index is None:
            return
        count = int(buf.shape[0])
        if count <= 0:
            return
        env = self._env
        index = self._index
        used = min(count, env.shape[0])
        env[:used] = self._gain + self._step * index[:used]
        np.clip(env[:used], 0.0, 1.0, out=env[:used])
        buf[:used] *= env[:used, None] if buf.ndim == 2 else env[:used]
        self._gain = float(env[used - 1])
        if count > used:  # 块比预分配还大（不该发生）时按终点值处理剩余部分
            if buf.ndim == 2:
                buf[used:] *= np.float32(self._gain)
            else:
                buf[used:] *= np.float32(self._gain)
        if (self._step > 0 and self._gain >= self._target) or (
            self._step < 0 and self._gain <= self._target
        ):
            self._gain = self._target
            self._active = False


class DspChain:
    """固定链：增益 → EQ → 斜坡。全部就地，返回「是否改了样本」。"""

    def __init__(
        self,
        *,
        gain_db: float = 0.0,
        eq_gains: Optional[Sequence[float]] = None,
        eq_enabled: bool = False,
        ramp_ms: int = RAMP_MS_DEFAULT,
        enabled: bool = True,
    ) -> None:
        self.gain = GainStage(gain_db)
        self.eq = BiquadEq(eq_gains)
        self.ramp = RampController(ramp_ms)
        self._enabled = bool(enabled)
        self._sample_rate = 0
        self._channels = 0
        self._block_frames = BLOCK_FRAMES_DEFAULT
        self.eq.set_enabled(bool(eq_enabled))

    # ------------------------------------------------------------ 配置

    @property
    def enabled(self) -> bool:
        """全局直通开关（关掉后一切都不处理）。"""
        return self._enabled

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def block_frames(self) -> int:
        return self._block_frames

    def prepare(
        self, sample_rate: int, channels: int, block_frames: int = BLOCK_FRAMES_DEFAULT
    ) -> None:
        """开流前调用一次（GUI 线程）：分配所有回调要用的缓冲。"""
        self._sample_rate = int(sample_rate)
        self._channels = max(1, int(channels))
        self._block_frames = int(block_frames) if int(block_frames) > 0 else BLOCK_FRAMES_DEFAULT
        self.eq.set_block_frames(self._block_frames)
        self.eq.prepare(self._sample_rate, self._channels)
        self.ramp.prepare(self._sample_rate, self._block_frames)

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)

    def set_gain_db(self, db: float) -> None:
        self.gain.set_db(db)

    def set_eq_enabled(self, enabled: bool) -> None:
        self.eq.set_enabled(bool(enabled))

    def set_eq_gains(self, gains: Sequence[float]) -> None:
        self.eq.set_gains(gains)

    def set_ramp_ms(self, ramp_ms: int) -> None:
        self.ramp.set_ramp_ms(ramp_ms)
        if self._sample_rate > 0:
            self.ramp.prepare(self._sample_rate, self._block_frames)

    def arm_fade_in(self) -> None:
        self.ramp.arm("in")

    def arm_fade_out(self) -> None:
        self.ramp.arm("out")

    def reset_state(self) -> None:
        """切歌/seek 后清空滤波器历史与斜坡状态。"""
        self.eq.reset_state()
        self.ramp.reset()

    # ------------------------------------------------------------ 处理

    def process(self, buf: np.ndarray) -> bool:
        """就地把一块浮点 PCM 过完整条链；返回是否发生了改动。

        这里**不能**分配数组、不能打日志、不能抛异常给回调（调用方 output.py
        已包了兜底，但保持本方法的纯净是更稳的做法）。
        """
        if buf.size == 0:
            return False
        if buf.dtype != np.float32:
            # 防未来再犯的护栏：浮点链只处理 float32。整数缓冲走到这里说明
            # 「整数通路与 DSP 互斥」这道门被绕过了 —— 宁可静默跳过（不崩、
            # 不抛进回调），也不要让 numpy 的 casting 异常变成整场静音。
            return False
        if not self._enabled:
            return False
        changed = False
        if self.gain.active:
            self.gain.process(buf)
            changed = True
        if self.eq.enabled:
            self.eq.process(buf)
            changed = True
        if self.ramp.active or self.ramp.muted:
            self.ramp.process(buf)
            changed = True
        return changed

    def snapshot(self) -> dict[str, Any]:
        """各环节状态快照（信号路径面板据此标注 改变/旁路）。"""
        return {
            "enabled": bool(self._enabled),
            "gain_db": float(self.gain.db),
            "gain_active": bool(self.gain.active),
            "eq_enabled": bool(self.eq.enabled),
            "eq_active": bool(self.eq.active),
            "eq_gains": self.eq.gains,
            "ramp_active": bool(self.ramp.active),
            "ramp_ms": int(self.ramp.ramp_ms),
            "ramp_gain": float(self.ramp.gain),
            "sample_rate": int(self._sample_rate),
            "channels": int(self._channels),
        }


__all__ = [
    "DspChain",
    "int_pipeline_allowed",
    "GainStage",
    "BiquadEq",
    "RampController",
    "db_to_linear",
]
