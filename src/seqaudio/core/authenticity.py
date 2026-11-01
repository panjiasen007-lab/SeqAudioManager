"""真假无损鉴定。

原理
----
「假无损」绝大多数是**有损音频转码后重新封装**成 FLAC/APE/WAV 的结果。有损编码器
为了省码率会直接砍掉高频，所以在频谱上留下一条**悬崖**：

* 128kbps MP3 → 通常硬切在 16kHz
* 192kbps MP3 → 约 18kHz
* 320kbps MP3 → 约 20kHz（与真无损很接近，需要看陡峭度）
* CD 音源真无损 → 能量平滑延伸到 20kHz 以上（Nyquist 22.05kHz）

另一种假货是**升频**：44.1kHz/16bit 的内容被"转"成 96kHz/24bit，
频谱在 22.05kHz 以上一片空白，位深最低几位全是 0。

判定因此看三件事：
1. **频谱截止频率**：从 Nyquist 往回扫，找能量跌破阈值的点；
2. **截止陡峭度**：有损编码是断崖式下跌，真实内容衰减平缓；
3. **有效位深**：低位恒零说明是低位深内容填充上来的。

DSD 单独处理：真 DSD 因为噪声整形，20kHz 以上能量异常高且能延伸到 100kHz 附近；
PCM 转的假 DSD 在 22.05kHz 以上会直接掉到地板。
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from .model import AudioInfo

log = logging.getLogger(__name__)

#: 每段分析时长（秒）。太短频率分辨率不够，太长浪费时间
SEGMENT_SECONDS = 2.0
#: 取样位置（占整曲的比例），避开前奏静音与结尾淡出
SAMPLE_POSITIONS = (0.15, 0.35, 0.55, 0.75)
#: FFT 点数，44.1kHz 下频率分辨率约 5.4Hz
FFT_SIZE = 8192
#: 单段解码超时
DECODE_TIMEOUT = 20.0

# ---------------------------------------------------------------- DSD 判据
# 参考 check-dsd（lossless-checker）的做法：
#   * 噪声整形斜率是**主判据** —— 真 DSD 的 Sigma-Delta 量化噪声被整形到
#     超声区，50kHz→100kHz 呈明显上升；PCM 转的假 DSD 在该频段是平坦的。
#   * 超声能量比（>50kHz）作为辅助。
#   * 基带截止只在 24kHz 以下找 —— 避免把 DXD 工作流的 176kHz 拐点误当成 CD 墙。

#: 噪声整形分析频段
DSD_SLOPE_BAND = (50_000.0, 100_000.0)
#: 超声能量参考频段
DSD_ULTRA_BAND = (50_000.0, 120_000.0)
#: 基带截止扫描上限
DSD_BASEBAND_MAX = 24_000.0

#: 噪声整形斜率阈值（dB/oct）
DSD_SLOPE_GENUINE = 6.0
DSD_SLOPE_SUSPECT = 0.5

#: 基带"硬悬崖"：低于该频率且陡度超过阈值，视为 CD/有损源转制的痕迹
DSD_BASEBAND_CLIFF_HZ = 21_500.0
DSD_BASEBAND_CLIFF_STEEP = 36.0

VERDICT_TRUE = "真无损"
VERDICT_SUSPECT = "疑似假无损"
VERDICT_FAKE = "假无损"
VERDICT_UNKNOWN = "无法判定"
VERDICT_DSD = "真 DSD"
VERDICT_FAKE_DSD = "疑似假 DSD"
VERDICT_UPSAMPLED = "疑似升频"

VERDICT_COLORS = {
    VERDICT_TRUE: "#34d399",
    VERDICT_DSD: "#22d3ee",
    VERDICT_UPSAMPLED: "#fbbf24",
    VERDICT_SUSPECT: "#fbbf24",
    VERDICT_FAKE_DSD: "#fbbf24",
    VERDICT_FAKE: "#f87171",
    VERDICT_UNKNOWN: "#5a6478",
}


@dataclass
class AuthenticityResult:
    verdict: str = VERDICT_UNKNOWN
    score: float = 0.0                 # 0~1，越高越像真无损
    cutoff_hz: int = 0                 # 检测到的频谱截止
    nyquist_hz: int = 0                # 理论最高频率
    steepness_db: float = 0.0          # 截止处每 Hz 的下降幅度（dB/kHz）
    bit_depth_declared: int = 0
    bit_depth_effective: int = 0
    high_band_energy_db: float = 0.0   # 20kHz 以上相对能量
    noise_shaping_slope: float = 0.0   # DSD 噪声整形斜率（dB/oct，真 DSD 为正）
    ultrasonic_ratio: float = 0.0      # 50kHz 以上相对能量（dB）
    baseband_cutoff: int = 0           # 基带（≤24kHz）截止频率
    analyzed_seconds: float = 0.0
    reasons: list[str] = field(default_factory=list)
    error: str = ""
    #: 判定所用那一段的 (频率轴, dB) 频谱，供界面画图；不参与比较与打印
    spectrum: Optional[tuple] = field(default=None, repr=False, compare=False)

    @property
    def is_true_lossless(self) -> bool:
        return self.verdict in (VERDICT_TRUE, VERDICT_DSD)

    @property
    def is_suspicious(self) -> bool:
        return self.verdict in (VERDICT_FAKE, VERDICT_SUSPECT, VERDICT_FAKE_DSD, VERDICT_UPSAMPLED)

    @property
    def text(self) -> str:
        if self.verdict == VERDICT_UNKNOWN:
            return self.error or VERDICT_UNKNOWN
        return self.verdict

    @property
    def detail_text(self) -> str:
        if self.verdict == VERDICT_UNKNOWN:
            return self.error or "未能分析"
        parts = [f"截止 {self.cutoff_hz / 1000:.1f}kHz / 上限 {self.nyquist_hz / 1000:.1f}kHz"]
        if self.steepness_db:
            parts.append(f"陡度 {self.steepness_db:.0f}dB/kHz")
        if self.noise_shaping_slope:
            parts.append(f"噪声整形 {self.noise_shaping_slope:+.1f}dB/oct")
        if self.ultrasonic_ratio:
            parts.append(f"50kHz 以上 {self.ultrasonic_ratio:.0f}dB")
        # DSD 声明位深是 1bit，解码后是浮点/32bit，对比没有意义，不显示
        if (
            self.bit_depth_effective
            and self.bit_depth_declared
            and self.bit_depth_declared > 1
        ):
            parts.append(f"有效位深 {self.bit_depth_effective}/{self.bit_depth_declared}bit")
        return " · ".join(parts)


# ---------------------------------------------------------------- ffmpeg 定位


def find_ffmpeg() -> Optional[str]:
    """优先用打包内置的 ffmpeg，其次找系统 PATH。"""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and Path(exe).exists():
            return exe
    except Exception:  # noqa: BLE001 - 未安装时静默回退
        pass
    return shutil.which("ffmpeg")


def ffmpeg_available() -> bool:
    return find_ffmpeg() is not None


# ---------------------------------------------------------------- 解码


def _probe_duration(ffmpeg: str, path: Path) -> float:
    """用 ffmpeg -i 读时长（不依赖 ffprobe）。"""
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", str(path)],
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = (proc.stderr or b"").decode("utf-8", "replace")
        for line in text.splitlines():
            if "Duration:" in line:
                stamp = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = stamp.split(":")
                return int(h) * 3600 + int(m) * 60 + float(s)
    except Exception:  # noqa: BLE001
        pass
    return 0.0


def _decode_segment(
    ffmpeg: str,
    path: Path,
    start: float,
    seconds: float,
    sample_rate: Optional[int] = None,
) -> Optional[tuple[np.ndarray, int]]:
    """解码一段音频为单声道 float32。

    注意**不要重采样**：重采样会在 Nyquist 附近做抗混叠滤波，
    把我们要找的"频谱悬崖"抹平，鉴定就失效了。
    """
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, start):.3f}",
        "-t", f"{seconds:.3f}",
        "-i", str(path),
        "-map", "0:a:0",
        # 保留立体声再在 Python 里取左声道：若用 "-ac 1" 做下混，
        # ffmpeg 会算 (L+R)/2 产生半整数，最低位被破坏，位深就测不准了
        "-ac", "2",
        # 用 32 位整数而不是 float：float32 只有 24 位尾数，
        # 16bit 内容左移后尾随零会被浮点误差吃掉
        "-f", "s32le",
        "-acodec", "pcm_s32le",
        "-",
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=DECODE_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        log.debug("解码超时 %s @%.1fs", path.name, start)
        return None
    except OSError as exc:
        log.debug("解码失败 %s：%s", path, exc)
        return None

    if proc.returncode != 0 or not proc.stdout:
        return None
    data = np.frombuffer(proc.stdout, dtype="<i4")
    if data.size < FFT_SIZE * 2:
        return None
    # 交错立体声，取左声道即可（不做混音，保持原始整数精度）
    data = data[0::2].copy()

    # 采样率从样本数反推：ffmpeg 保留原采样率，时长已知
    effective_sr = int(round(data.size / seconds)) if seconds > 0 else 0
    # 对齐到常见采样率，避免 ±1 的取整误差影响 Nyquist 判断
    if effective_sr:
        for candidate in (44100, 48000, 88200, 96000, 176400, 192000,
                          352800, 384000, 705600, 1411200, 2822400, 5644800):
            if abs(effective_sr - candidate) / candidate < 0.02:
                effective_sr = candidate
                break
    return data, effective_sr


# ---------------------------------------------------------------- 频谱


def _average_spectrum(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """分帧加窗做 FFT，返回 (频率轴, 平均幅度 dB)。

    ``samples`` 可以是 int32（解码直出）或 float；统一转 float64 保证 FFT 精度。
    """
    samples = np.asarray(samples, dtype=np.float64)
    if samples.size < FFT_SIZE:
        samples = np.pad(samples, (0, FFT_SIZE - samples.size))
    hop = FFT_SIZE // 2
    window = np.hanning(FFT_SIZE)
    frames: list[np.ndarray] = []
    for start in range(0, samples.size - FFT_SIZE + 1, hop):
        chunk = samples[start : start + FFT_SIZE]
        if not np.any(chunk):
            continue
        spectrum = np.abs(np.fft.rfft(chunk * window))
        frames.append(20.0 * np.log10(spectrum + 1e-10))
    if not frames:
        return np.array([]), np.array([])
    avg = np.mean(np.stack(frames), axis=0)
    freqs = np.fft.rfftfreq(FFT_SIZE, d=1.0 / sample_rate)
    # 轻平滑，抑制单帧毛刺
    if avg.size > 9:
        kernel = np.ones(9) / 9.0
        avg = np.convolve(avg, kernel, mode="same")
    return freqs, avg


def _find_cutoff(
    freqs: np.ndarray,
    db: np.ndarray,
    max_hz: Optional[float] = None,
) -> tuple[float, float]:
    """找频谱截止：从最高频往回扫，第一个「连续多个点都有信号」的位置即截止。

    为什么要求连续：有损编码在高频会留下零星残留脉冲（量化噪声），
    只要有一个点超过阈值就认作"有信号"会严重误判。要求连续 5 个点
    （44.1kHz 下约 27Hz 宽）能有效过滤这类毛刺。

    ``max_hz`` 限制扫描上限：DSD 的基带分析只扫 24kHz 以下，避免把噪声整形
    或 DXD 工作流的超声拐点误当成 CD 墙。
    """
    if freqs.size == 0:
        return 0.0, 0.0

    limit = freqs.size
    if max_hz and max_hz > 0:
        limit = int(np.searchsorted(freqs, max_hz, side="right"))
        limit = max(5, min(limit, freqs.size))

    # 参考能量取 300Hz~3kHz（音乐主体），比取全局最大值稳
    ref_mask = (freqs >= 300) & (freqs <= 3000)
    reference = float(np.median(db[ref_mask])) if np.any(ref_mask) else float(np.max(db))
    threshold = reference - 62.0          # 低于主体 62dB 视为无有效信号

    above = db[:limit] > threshold
    run = 0
    cutoff_index = 0
    for i in range(above.size - 1, -1, -1):
        if above[i]:
            run += 1
            if run >= 5:
                cutoff_index = i + 4
                break
        else:
            run = 0

    cutoff = float(freqs[min(cutoff_index, limit - 1)])

    # 陡度：截止点往前 2kHz 内的平均下降速度（dB/kHz）
    band = (freqs >= max(1000.0, cutoff - 2000)) & (freqs <= cutoff + 200)
    if np.count_nonzero(band) >= 6:
        seg_db = db[band]
        seg_hz = freqs[band]
        drop = float(np.median(seg_db[:3]) - np.median(seg_db[-3:]))
        span_khz = max(0.001, (seg_hz[-1] - seg_hz[0]) / 1000.0)
        steepness = drop / span_khz
    else:
        steepness = 0.0
    return cutoff, steepness


def _noise_shaping_slope(freqs: np.ndarray, db: np.ndarray, nyquist: float) -> float:
    """噪声整形斜率：50~100kHz 区间每倍频程的能量变化（dB/oct）。

    真 DSD 的 Sigma-Delta 量化噪声被整形到超声区，该频段能量随频率**上升**
    （典型 +6 ~ +18dB/oct）；PCM 转制的假 DSD 在此频段平坦（≈0）或下降。
    区间内有效点太少时返回 0（表示无法判断，交给上层按可疑处理）。
    """
    low, high = DSD_SLOPE_BAND
    high = min(high, nyquist * 0.95)
    if high <= low:
        return 0.0
    mask = (freqs >= low) & (freqs <= high)
    if np.count_nonzero(mask) < 16:
        return 0.0
    x = np.log2(np.maximum(freqs[mask], 1.0))
    y = db[mask]
    ok = np.isfinite(y)
    if np.count_nonzero(ok) < 16:
        return 0.0
    try:
        return float(np.polyfit(x[ok], y[ok], 1)[0])
    except (np.linalg.LinAlgError, ValueError):
        return 0.0


def _band_energy(freqs: np.ndarray, db: np.ndarray, low: float, high: float) -> float:
    """指定频段相对主体能量的 dB 值。"""
    ref_mask = (freqs >= 300) & (freqs <= 3000)
    band_mask = (freqs >= low) & (freqs <= high)
    if not np.any(ref_mask) or not np.any(band_mask):
        return -120.0
    reference = float(np.median(db[ref_mask]))
    return float(np.median(db[band_mask]) - reference)


def _effective_bit_depth(samples: np.ndarray) -> int:
    """从 32 位整数样本推导有效位深。

    ffmpeg 把 16bit 源输出成 s32 时会左移 16 位（值 = s16 << 16），
    24bit 源左移 8 位。所以「所有样本共同的尾随零个数」直接对应原始位深：
    尾随 16 个零 -> 16bit；8 个零 -> 24bit；0 个零 -> 32bit。
    """
    ints = np.asarray(samples)
    if ints.size == 0:
        return 0
    ints = np.abs(ints.astype(np.int64))
    ints = ints[ints > 0]
    if ints.size == 0:
        return 0

    common_zeros = 32
    for value in np.unique(ints)[:4000]:
        v = int(value)                      # numpy.int64 没有 bit_length，必须先转 Python int
        if v <= 0:
            continue
        trailing = (v & -v).bit_length() - 1
        common_zeros = min(common_zeros, trailing)
        if common_zeros == 0:
            break
    return max(0, 32 - common_zeros)


# ---------------------------------------------------------------- 判定


def _judge_dsd(
    slope: float,
    ultra: float,
    base_cutoff: float,
    base_steep: float,
) -> tuple[str, float, list[str]]:
    """DSD 判定（参考 check-dsd）：噪声整形斜率是主判据。

    * 真 DSD 的 Sigma-Delta 量化噪声被整形到超声区，50→100kHz 呈明显上升
      （典型 +6 ~ +18dB/oct）；
    * PCM 转制的假 DSD 在该频段平坦（≈0）或下降；
    * 基带截止只作辅助 —— 噪声整形成立时，基带的自然滚降是正常母带特征，
      只有「硬悬崖」（<21.5kHz 且陡度 >36dB/kHz）才说明源头是 CD/有损。
    """
    reasons: list[str] = []

    if slope >= DSD_SLOPE_GENUINE and ultra > -50:
        if (
            base_cutoff > 0
            and base_cutoff < DSD_BASEBAND_CLIFF_HZ
            and base_steep > DSD_BASEBAND_CLIFF_STEEP
        ):
            reasons.append(
                f"噪声整形斜率 {slope:+.1f}dB/oct，但基带在 "
                f"{base_cutoff / 1000:.1f}kHz 硬截止（陡度 {base_steep:.0f}dB/kHz），"
                "疑似 CD/有损源经调制器转成 DSD"
            )
            return VERDICT_FAKE_DSD, 0.4, reasons
        reasons.append(
            f"噪声整形斜率 {slope:+.1f}dB/oct、50kHz 以上能量 {ultra:.0f}dB，"
            "符合 Sigma-Delta 调制特征"
        )
        return VERDICT_DSD, 0.9, reasons

    if slope >= DSD_SLOPE_SUSPECT and ultra > -58:
        reasons.append(
            f"噪声整形偏弱（斜率 {slope:+.1f}dB/oct、50kHz 以上 {ultra:.0f}dB），"
            "可能是高规格 PCM 转制、模拟母带转录，或解码器做了超声低通"
        )
        return VERDICT_FAKE_DSD, 0.5, reasons

    reasons.append(
        f"超声区平坦（斜率 {slope:+.1f}dB/oct、50kHz 以上仅 {ultra:.0f}dB），"
        "缺少 Sigma-Delta 噪声整形，疑似 PCM 升频/转制为 DSD"
    )
    return VERDICT_FAKE_DSD, 0.25, reasons


def _judge(
    info: AudioInfo,
    cutoff: float,
    steepness: float,
    high_band: float,
    eff_depth: int,
    *,
    slope: float = 0.0,
    ultra: float = 0.0,
    base_cutoff: float = 0.0,
    base_steep: float = 0.0,
) -> tuple[str, float, list[str]]:
    reasons: list[str] = []
    sample_rate = info.sample_rate or 44100
    nyquist = sample_rate / 2.0
    is_dsd = info.fmt in ("DSF", "DFF") or bool(info.dsd_rate)
    ratio = (cutoff / nyquist) if nyquist else 0.0

    if is_dsd:
        return _judge_dsd(slope, ultra, base_cutoff, base_steep)

    if nyquist <= 24000:
        # CD 标准音源（44.1k / 48k）。
        #
        # 关键：cutoff 偏低不等于假无损。1980~90 年代的老录音、模拟母带转录，
        # 高频本来就不丰富，频谱会**平滑滚降**到 16~18kHz —— 这是真 CD 的
        # 正常特征，不能按「转码砍频」判假。
        # 区分依据是陡度：有损转码是断崖式下跌（>25dB/kHz），老录音是平缓滚降
        # （通常 <15dB/kHz）。陡度为 0 表示无法计算，保守按转码处理。
        smooth_rolloff = 0.5 < steepness < 25.0

        if cutoff < 17000:
            if smooth_rolloff:
                # 截止过低（<17k）即使平滑也偏保守：可能是低码率转码后被平滑处理，
                # 也可能是音质极差的老录音。判「疑似」，交给用户复核，不直接判假。
                reasons.append(
                    f"频谱在 {cutoff / 1000:.1f}kHz 平滑滚降（陡度 {steepness:.0f}dB/kHz），"
                    "截止偏低，可能是音质受限的老录音，建议人工复核"
                )
                return VERDICT_SUSPECT, 0.5, reasons
            reasons.append(f"频谱在 {cutoff / 1000:.1f}kHz 断崖截止，符合 128kbps 有损转码特征")
            return VERDICT_FAKE, 0.9, reasons
        if cutoff < 19500:
            if smooth_rolloff:
                reasons.append(
                    f"频谱在 {cutoff / 1000:.1f}kHz 平滑滚降（陡度 {steepness:.0f}dB/kHz），"
                    "符合老录音/模拟母带的高频自然衰减，非有损转码"
                )
                return VERDICT_TRUE, 0.7, reasons
            reasons.append(
                f"频谱在 {cutoff / 1000:.1f}kHz 截止（陡度 {steepness:.0f}dB/kHz），"
                "符合 192kbps 有损转码特征"
            )
            return VERDICT_FAKE, 0.75, reasons
        if cutoff < 20500:
            if smooth_rolloff:
                reasons.append(
                    f"频谱在 {cutoff / 1000:.1f}kHz 平滑滚降（陡度 {steepness:.0f}dB/kHz），"
                    "符合老录音高频自然衰减，非高码率转码"
                )
                return VERDICT_TRUE, 0.72, reasons
            reasons.append(
                f"频谱在 {cutoff / 1000:.1f}kHz 断崖截止（陡度 {steepness:.0f}dB/kHz），"
                "符合高码率有损转码特征（256~320kbps）"
            )
            return VERDICT_SUSPECT, 0.45, reasons
        reasons.append(f"频谱平滑延伸到 {cutoff / 1000:.1f}kHz（上限 {nyquist / 1000:.1f}kHz）")
        if eff_depth and info.bit_depth and eff_depth < info.bit_depth - 2:
            reasons.append(
                f"有效位深仅 {eff_depth}bit，声明 {info.bit_depth}bit，疑似低位深填充"
            )
            return VERDICT_SUSPECT, 0.6, reasons
        return VERDICT_TRUE, 0.85, reasons

    # 高采样率（88.2kHz 及以上）
    if ratio < 0.55:
        reasons.append(
            f"采样率 {sample_rate / 1000:.1f}kHz，但频谱只到 {cutoff / 1000:.1f}kHz"
            f"（带宽利用率 {ratio:.0%}），疑似 44.1kHz 内容升频"
        )
        return VERDICT_UPSAMPLED, 0.35, reasons
    if ratio < 0.70 and steepness > 40:
        reasons.append(f"高频在 {cutoff / 1000:.1f}kHz 陡降（{steepness:.0f}dB/kHz），疑似升频")
        return VERDICT_UPSAMPLED, 0.45, reasons
    reasons.append(
        f"高频延伸到 {cutoff / 1000:.1f}kHz（带宽利用率 {ratio:.0%}），符合高解析音源特征"
    )
    return VERDICT_TRUE, 0.8, reasons


# ---------------------------------------------------------------- 对外入口


def analyze(
    path: Path | str,
    info: Optional[AudioInfo] = None,
    *,
    positions: tuple[float, ...] = SAMPLE_POSITIONS,
    segment_seconds: float = SEGMENT_SECONDS,
) -> AuthenticityResult:
    """分析单个文件的真假无损。"""
    path = Path(path)
    result = AuthenticityResult()
    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        result.error = "未找到 ffmpeg，无法解码音频"
        return result
    if not path.exists():
        result.error = "文件不存在"
        return result

    if info is None:
        from .probe import probe

        info = probe(path, with_cue=False).info
    result.bit_depth_declared = info.bit_depth or 0
    sample_rate = info.sample_rate or 0
    result.nyquist_hz = sample_rate // 2

    duration = info.duration or _probe_duration(ffmpeg, path)
    if not duration or duration < 3:
        result.error = "时长过短，无法分析"
        return result

    spectra: list[tuple[np.ndarray, np.ndarray]] = []
    deepest_depth = 0
    analyzed = 0.0
    for position in positions:
        start = max(1.0, duration * position - segment_seconds / 2)
        if start + segment_seconds > duration:
            start = max(0.0, duration - segment_seconds)
        decoded = _decode_segment(ffmpeg, path, start, segment_seconds)
        if decoded is None:
            continue
        samples, effective_sr = decoded
        analyzed += segment_seconds
        if effective_sr:
            sample_rate = effective_sr
            result.nyquist_hz = sample_rate // 2
        freqs, db = _average_spectrum(samples, sample_rate)
        if freqs.size:
            spectra.append((freqs, db))
        depth = _effective_bit_depth(samples)
        deepest_depth = max(deepest_depth, depth)

    if not spectra:
        result.error = "解码失败：ffmpeg 无法解析该文件"
        return result

    # 每段各算一个截止频率，用**中位数**做判定。
    #
    # 早先用「最小值」（最差的一段）判定，问题是：前奏静音 / 结尾淡出那一段
    # 本来高频能量就低，_find_cutoff 会报出一个偏低的截止，把真无损误判成假。
    # 中位数对这类离群段不敏感，只有当多数段都被砍了高频才判假 —— 这才是
    # "有损转码"的真实特征（编码器对整首歌一视同仁地砍高频）。
    cutoffs: list[float] = []
    for freqs, db in spectra:
        cutoff, _ = _find_cutoff(freqs, db)
        if cutoff > 0:
            cutoffs.append(cutoff)
    if not cutoffs:
        result.error = "频谱中未检测到有效信号"
        return result

    cutoff = float(np.median(cutoffs))
    cutoff_min = float(np.min(cutoffs))
    cutoff_max = float(np.max(cutoffs))

    # 展示频谱选「截止频率最接近中位数」的那一段，而不是随便取第一段
    chosen_index = min(range(len(spectra)), key=lambda i: abs(cutoffs[i] - cutoff))
    freqs, db = spectra[chosen_index]
    result.spectrum = (freqs, db)

    _, steepness = _find_cutoff(freqs, db)
    # 各段截止差异过大：多为包含前奏/静音的正常歌曲，在理由里注明，避免用户误以为鉴定不稳定
    spread_note = ""
    if cutoff_max - cutoff_min > 3000:
        spread_note = f"（各段截止 {cutoff_min / 1000:.1f}~{cutoff_max / 1000:.1f}kHz，含静音/前奏段）"

    is_dsd = info.fmt in ("DSF", "DFF") or bool(info.dsd_rate)
    slope = 0.0
    ultra = 0.0
    base_cutoff = 0.0
    base_steep = 0.0
    if is_dsd:
        nyq = float(result.nyquist_hz or 352800)
        # 噪声整形斜率（主判据）
        slope = _noise_shaping_slope(freqs, db, nyq)
        # 超声能量比（辅助）
        ultra = _band_energy(freqs, db, DSD_ULTRA_BAND[0], min(DSD_ULTRA_BAND[1], nyq))
        # 基带截止只在 24kHz 以下找，避免 DXD 的 176kHz 缺口被误当 CD 墙
        base_cutoff, base_steep = _find_cutoff(freqs, db, max_hz=DSD_BASEBAND_MAX)
        high_band = _band_energy(freqs, db, 40000, min(nyq, 140000))
        result.noise_shaping_slope = slope
        result.ultrasonic_ratio = ultra
        result.baseband_cutoff = int(base_cutoff)
    else:
        high_band = _band_energy(freqs, db, 20000, min(result.nyquist_hz or 22050, 40000))
    result.cutoff_hz = int(cutoff)
    result.steepness_db = steepness
    result.high_band_energy_db = high_band
    result.bit_depth_effective = deepest_depth
    result.analyzed_seconds = analyzed

    verdict, score, reasons = _judge(
        info,
        cutoff,
        steepness,
        high_band,
        deepest_depth,
        slope=slope,
        ultra=ultra,
        base_cutoff=base_cutoff,
        base_steep=base_steep,
    )
    result.verdict = verdict
    result.score = score
    result.reasons = reasons
    if spread_note and verdict == VERDICT_TRUE:
        result.reasons.append(spread_note)
    return result


def full_spectrum(
    path: Path | str,
    info: Optional[AudioInfo] = None,
    *,
    target_frames: int = 1000,
    max_seconds: Optional[float] = 600.0,
) -> Optional[tuple[np.ndarray, np.ndarray, int]]:
    """解码整首歌，返回完整频谱 ``(db_matrix, freqs, sample_rate)``。

    用于 Spek 式瀑布图（时间 × 频率）。为控制内存，时间轴压缩到约
    ``target_frames`` 列；频率分辨率由自动选取的 FFT 长度决定。
    dB 已减去 300Hz~3kHz 的主体能量（相对 dB），可直接按固定色阶绘制。
    """
    ffmpeg = find_ffmpeg()
    path = Path(path)
    if ffmpeg is None or not path.exists():
        return None

    if info is None:
        from .probe import probe

        info = probe(path, with_cue=False).info

    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error"]
    if max_seconds:
        cmd += ["-t", f"{max_seconds:.1f}"]
    is_dsd = info.fmt in ("DSF", "DFF") or bool(info.dsd_rate)
    cmd += [
        "-i", str(path),
        "-map", "0:a:0",
        "-ac", "1",
    ]
    if is_dsd:
        # DSD 解码后采样率极高（DSD64 → 352.8kHz），整轨一次性载入内存吃不消；
        # 降到 176.4kHz 仍能保留 88kHz 以下的噪声整形信息。
        cmd += ["-ar", "176400"]
    cmd += ["-f", "s16le", "-acodec", "pcm_s16le", "-"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None

    data = np.frombuffer(proc.stdout, dtype="<i2").astype(np.float32)
    total = int(data.size)
    if total < 4096:
        return None

    duration = info.duration or _probe_duration(ffmpeg, path)
    sample_rate = (
        int(round(total / duration))
        if duration and duration > 0
        else (info.sample_rate or 44100)
    )
    if sample_rate <= 0:
        sample_rate = info.sample_rate or 44100
    # 对齐常见采样率，避免 ±1 的取整误差
    for candidate in (44100, 48000, 88200, 96000, 176400, 192000,
                      352800, 384000, 705600, 1411200):
        if abs(sample_rate - candidate) / candidate < 0.02:
            sample_rate = candidate
            break

    hop = max(512, total // max(1, target_frames))
    fft_size = 1 << max(11, (hop - 1).bit_length())
    if fft_size > 32768:
        fft_size = 32768
        hop = max(512, min(hop, 32768))
    n_frames = max(0, (total - fft_size) // hop + 1)
    if n_frames < 1:
        return None

    window = np.hanning(fft_size).astype(np.float32)
    bins = fft_size // 2 + 1
    out = np.empty((n_frames, bins), dtype=np.float32)
    batch = max(1, min(128, n_frames))
    for start in range(0, n_frames, batch):
        end = min(start + batch, n_frames)
        idx = np.arange(fft_size)[None, :] + hop * np.arange(start, end)[:, None]
        frames = data[idx] * window[None, :]
        spec = np.abs(np.fft.rfft(frames, axis=1))
        out[start:end] = (20.0 * np.log10(spec + 1e-10)).astype(np.float32)

    freqs = np.fft.rfftfreq(fft_size, d=1.0 / sample_rate).astype(np.float32)

    ref_mask = (freqs >= 300) & (freqs <= 3000)
    reference = float(np.median(out[:, ref_mask])) if np.any(ref_mask) else float(np.max(out))
    out -= reference
    return out, freqs, sample_rate


def analyze_many(
    tracks,
    *,
    workers: int = 4,
    progress=None,
    cancel=None,
) -> dict[str, AuthenticityResult]:
    """批量鉴定。用线程池并行跑 ffmpeg 子进程。"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results: dict[str, AuthenticityResult] = {}
    items = [(t.path, t.info) for t in tracks]
    total = len(items)
    if total == 0:
        return results

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(analyze, path, info): path for path, info in items
        }
        for future in as_completed(futures):
            path = futures[future]
            try:
                results[str(path)] = future.result()
            except Exception as exc:  # noqa: BLE001
                log.exception("鉴定失败 %s", path)
                results[str(path)] = AuthenticityResult(error=f"分析异常：{exc}")
            done += 1
            if progress is not None:
                progress(done, total)
            if cancel is not None and cancel.is_set():
                for pending in futures:
                    pending.cancel()
                break
    return results


# ---------------------------------------------------------------- 分组（界面筛选 / 批量清理共用）

#: 判定分组：把一个结论映射到「真 / 假 / 可疑 / 未鉴定」四类，
#: 界面筛选与清理范围都用这一份口径，避免两处逻辑各自漂移
VERDICT_GROUPS: dict[str, tuple[str, ...]] = {
    "true": (VERDICT_TRUE, VERDICT_DSD),
    "fake": (VERDICT_FAKE,),
    "suspect": (VERDICT_SUSPECT, VERDICT_FAKE_DSD, VERDICT_UPSAMPLED),
    "unknown": (VERDICT_UNKNOWN, ""),
}

GROUP_LABELS = {
    "true": "真无损",
    "fake": "假无损",
    "suspect": "可疑",
    "unknown": "未鉴定",
}


def group_of(verdict: str) -> str:
    for key, members in VERDICT_GROUPS.items():
        if verdict in members:
            return key
    return "unknown"


def removable_tracks(tracks, *, include_suspect: bool = True):
    """挑出可清理的曲目：结论为假无损，或（可选）疑似假无损/升频。

    判定为「无法判定」的一律不动 —— 没测出结果不等于有问题。
    """
    wanted = set(VERDICT_GROUPS["fake"])
    if include_suspect:
        wanted |= set(VERDICT_GROUPS["suspect"])
    return [t for t in tracks if t.authenticity is not None and t.authenticity.verdict in wanted]
