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

#: CD 的 Nyquist 边界（22.05kHz）+ 检测容差。
#: 基带截止落在这条线以下，说明源头最高只到 CD 规格。
DSD_CD_BOUNDARY_HZ = 22_550.0
#: 基带截止之上的「数字静音」阈值。
#: CD 转 DSD 在 22.05kHz 之上是数字静音（实测 -83 ~ -94dB）；
#: 真 DSD 的噪声整形在那里仍有 -60 ~ -75dB 的能量，不会低于这条线。
DSD_DIGITAL_SILENCE_DB = -78.0
#: 数字静音检测频段（CD Nyquist 之上、噪声整形主峰之前）
DSD_SILENCE_BAND = (22_500.0, 24_000.0)
#: 基带陡度辅助阈值：断崖式截止同样视为 CD/有损源
DSD_BASEBAND_CLIFF_STEEP = 30.0

#: 常见 PCM 采样率的 Nyquist 网格（Hz）。
#: PCM 转 DSD 时，源码的带宽边界会原封不动地留在基带上：
#: 44.1k→22.05k、48k→24k、88.2k→44.1k、96k→48k、176.4k→88.2k、192k→96k。
#: 断崖落在这些点上且之上是数字静音，就是 PCM 转制的铁证。
PCM_NYQUISTS = (22_050.0, 24_000.0, 32_000.0, 44_100.0, 48_000.0, 88_200.0, 96_000.0)
#: 断崖判定：Nyquist 前后各取一段，能量落差超过这个值才算断崖
PCM_CLIFF_STEP_DB = 20.0
#: 断崖之上的「数字静音」阈值：PCM 的 Nyquist 之上本来就是空白，
#: 被 DSD 调制器抬起后仍显著低于真 DSD 的噪声整形基底
PCM_CLIFF_SILENCE_DB = -75.0
#: 真 DSD 的噪声整形基底：30~40kHz 应已有可观能量（实测 -29 ~ -46dB），
#: PCM 转制只有 -59 ~ -61dB。仅作辅助理由，不单独判决
DSD_ULTRA_BODY_DB = -52.0

#: PCM 升采样的「源码边界台阶」阈值（dB）。
#: 44.1k→48k 升采样后，22.05kHz 处会留下一个台阶；有些工具会加 dither 把台阶
#: 填成噪声伪装成内容（实测台阶之上 -53 ~ -60dB），但台阶本身 ≥18dB 就能
#: 暴露断层 —— 自然滚降不会在某个采样率边界上突然跌 18dB。
PCM_UPSAMPLE_STEP_DB = 18.0

#: 信号边界判据（V1.2.6）：分频段统计**帧间电平波动**。
#:
#: 为什么需要它：上面两条判据看的都是「电平台阶」，而台阶会被噪声盖住 ——
#: 调制器带内噪声高的转制文件（实测 Adele 有一半、Taylor Swift 全部），
#: 22kHz 之上的噪声电平与音乐高频相当，频谱上看不到任何台阶，于是被判成真 DSD。
#:
#: 但噪声和音乐有一个本质区别：**音乐随乐句起伏，噪声不随**。实测真/假 DSD
#: 在纯噪声区的帧间标准差只有 0.6~1.5dB，而音乐内容的频段高达 5~12dB。
#: 所以「波动从高跌到噪声水平」的频率就是源码的带宽边界，与绝对电平无关。
DSD_EDGE_VARIATION_DB = 2.0
#: 波动边界允许匹配的 PCM Nyquist 网格。只保留最常见四档：
#: 44.1k / 48k / 96k / 192k 源码。88.2k(44.1k 边界) 与 64k(32k 边界)
#: 极少见，放进来容易把真 DSD 在 40~45kHz 的自然滚降误判成 88.2k 转制。
DSD_EDGE_GRID = (22_050.0, 24_000.0, 48_000.0, 96_000.0)
#: 带宽连 CD 都不到（< 21.5kHz）：只可能来自有损编码或低采样率 PCM。
#: 实测库里 34 个文件边界落在 16/18/20kHz —— 正好是 MP3 128k/192k/320k
#: 的低通位置，属于「有损转 DSD」，比 CD 转 DSD 更离谱。
DSD_SUB_CD_EDGE_HZ = 21_500.0

# ---------------------------------------------------------------- PCM 判据
# 参考 2026 年开源实现的共识（lossless-checker / Lossless Audio Checker /
# FlacCompagnon / audiojs measure-lossy）：
#   * 截止频率只是**证据**，不是结论 —— 母带自然滚降与编码器低通在频谱上
#     无法用单一阈值分开；
#   * 判据要分三类独立成立：转码（丢信息）、升采样（采样率注水）、
#     升位深（位深注水）；
#   * 参考系必须取**轨道自身频谱峰值**，取噪声底会把高频本来就弱的
#     管弦/人声读成「满带宽」。

#: 截止判据：相对频谱峰值的阈值（参考 lossless-checker 的 -65dB）
CUTOFF_PEAK_DB = 65.0
#: 截止判据：相对音乐主体（300Hz~3kHz 中位数）的阈值
CUTOFF_MEDIAN_DB = 62.0

#: 陡度分界（dB/oct）。心理声学编码器的低通远陡于 60dB/oct；
#: 话筒、磁带、老录音的自然滚降是个位数到二十几。
STEEP_ENCODER_OCT = 60.0
STEEP_NATURAL_OCT = 28.0

#: 升采样：截止低于该比例 × Nyquist，且扩展带能量低于阈值
UPSAMPLE_RATIO = 0.96
UPSAMPLE_EDGE_DB = -95.0
#: 「截止之上是数字静音」的阈值：低于它说明那里没有模拟底噪，只剩编码器空白
DIGITAL_SILENCE_EDGE_DB = -95.0
#: 扩展带起点相对截止的偏移，跳过过渡带
UPSAMPLE_EDGE_OFFSET_HZ = 1000.0

#: 频谱空洞：4~16kHz 内的窄带凹陷（低码率编码器「位不够」的瑞士奶酪特征）
HOLE_BAND = (4000.0, 16000.0)
HOLE_DEPTH_DB = 18.0
HOLE_MAX_WIDTH_HZ = 600.0

#: sfb21 notch：MP3 最后一个长窗 scalefactor band 边界上的特征凹陷
SFB21_BAND = (15_500.0, 16_800.0)
SFB21_DEPTH_DB = 8.0

VERDICT_TRUE = "真无损"
VERDICT_SUSPECT = "疑似假无损"
VERDICT_FAKE = "假无损"
VERDICT_UNKNOWN = "无法判定"
VERDICT_DSD = "真 DSD"
VERDICT_FAKE_DSD = "疑似假 DSD"
VERDICT_UPSAMPLED = "疑似升频"
VERDICT_UPSCALED = "疑似升位深"

VERDICT_COLORS = {
    VERDICT_TRUE: "#34d399",
    VERDICT_DSD: "#22d3ee",
    VERDICT_UPSAMPLED: "#fbbf24",
    VERDICT_UPSCALED: "#fbbf24",
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
    steepness_db: float = 0.0          # 截止处下降速度（dB/kHz，历史字段）
    steepness_oct: float = 0.0         # 截止处下降速度（dB/oct，与文献可比）
    peak_db: float = 0.0               # 轨道频谱峰值（绝对 dB），判据的参考点
    edge_energy_db: float = 0.0        # 截止之上到 Nyquist 的能量（相对峰值）
    bandwidth_ratio: float = 0.0       # 截止 / Nyquist
    spectral_holes: float = 0.0        # 每帧平均频谱空洞数（4~16kHz 窄凹）
    sfb21_notch_db: float = 0.0        # 15.5~16.8kHz 凹陷深度（MP3 特征）
    bit_depth_padding: bool = False    # 疑似低位深零填充（升位深）
    bit_above_16: bool = False         # 存在超过 16bit 上限的样本
    bit_depth_declared: int = 0
    bit_depth_effective: int = 0
    spectral_roughness: float = 0.0    # 4~16kHz 相邻频点幅度差 RMS（有损编码"锯齿"度）
    high_band_energy_db: float = 0.0   # 20kHz 以上相对能量
    noise_shaping_slope: float = 0.0   # DSD 噪声整形斜率（dB/oct，真 DSD 为正）
    ultrasonic_ratio: float = 0.0      # 50kHz 以上相对能量（dB）
    baseband_cutoff: int = 0           # 基带（≤24kHz）截止频率
    baseband_silence: float = 0.0      # 基带截止之上（22.5~24kHz）的相对能量
    signal_edge_hz: int = 0            # 音频内容的上边界（帧间波动跌回噪声水平处）
    signal_edge_std: float = 0.0       # 边界下方的帧间波动（dB），用来解释判据
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
        return self.verdict in (
            VERDICT_FAKE,
            VERDICT_SUSPECT,
            VERDICT_FAKE_DSD,
            VERDICT_UPSAMPLED,
            VERDICT_UPSCALED,
        )

    @property
    def text(self) -> str:
        if self.verdict == VERDICT_UNKNOWN:
            return self.error or VERDICT_UNKNOWN
        return self.verdict

    @property
    def detail_text(self) -> str:
        if self.verdict == VERDICT_UNKNOWN:
            return self.error or "未能分析"
        # DSD 的「截止」永远是 DSD 自身的 Nyquist（352.5/705.2kHz），报带宽 100%
        # 毫无意义；DSD 要看的是「内容边界」和「噪声整形」两项
        if self.noise_shaping_slope or self.signal_edge_hz:
            parts = []
            if self.signal_edge_hz:
                parts.append(f"信号边界 {self.signal_edge_hz / 1000:.1f}kHz")
            if self.noise_shaping_slope:
                parts.append(f"噪声整形 {self.noise_shaping_slope:+.1f}dB/oct")
            if self.ultrasonic_ratio:
                parts.append(f"50kHz 以上 {self.ultrasonic_ratio:.0f}dB")
            # 基带扫描上限就是 24kHz，报到 24k 等于「基带内没有截止」，没有信息量
            if 0 < self.baseband_cutoff < 23_500:
                parts.append(f"基带截止 {self.baseband_cutoff / 1000:.1f}kHz")
            return " · ".join(parts) or self.verdict
        parts = [f"截止 {self.cutoff_hz / 1000:.1f}kHz / 上限 {self.nyquist_hz / 1000:.1f}kHz"]
        if self.bandwidth_ratio:
            parts.append(f"带宽 {self.bandwidth_ratio:.0%}")
        if self.steepness_oct:
            parts.append(f"陡度 {self.steepness_oct:.0f}dB/oct")
        if self.edge_energy_db:
            parts.append(f"扩展带 {self.edge_energy_db:.0f}dB")
        if self.spectral_holes:
            parts.append(f"空洞 {self.spectral_holes:.1f}/帧")
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
    *,
    reference: str = "peak",
) -> tuple[float, float, float]:
    """找频谱截止，返回 ``(截止频率Hz, 陡度dB/kHz, 陡度dB/oct)``。

    判据参考 2026 年开源实现的共识：以**轨道自身频谱峰值**为参考
    （``reference="peak"``，-65dB），而不是以近静音的高频噪声底或
    300Hz~3kHz 的中位数为参考。原因很实际：管弦、人声、老录音的高频
    本来就弱，用中位数做参考会把它们读成「满带宽」，漏掉真正的转码。

    ``reference="median"`` 保留给 DSD 的基带分析 —— 那里要判断的是
    「CD 边界之上是不是数字静音」，用主体中位数更稳。

    为什么要求连续 5 个点：有损编码在高频会留下零星残留脉冲（量化噪声），
    只要有一个点超过阈值就认作"有信号"会严重误判。

    ``max_hz`` 限制扫描上限：DSD 的基带分析只扫 24kHz 以下，避免把噪声整形
    或 DXD 工作流的超声拐点误当成 CD 墙。
    """
    if freqs.size == 0:
        return 0.0, 0.0, 0.0

    limit = freqs.size
    if max_hz and max_hz > 0:
        limit = int(np.searchsorted(freqs, max_hz, side="right"))
        limit = max(5, min(limit, freqs.size))

    scope = db[:limit]
    if reference == "median":
        ref_mask = (freqs[:limit] >= 300) & (freqs[:limit] <= 3000)
        base = float(np.median(scope[ref_mask])) if np.any(ref_mask) else float(np.max(scope))
        threshold = base - CUTOFF_MEDIAN_DB
    elif reference == "peak":
        threshold = float(np.max(scope)) - CUTOFF_PEAK_DB
    else:
        # auto：两种参考各算一个阈值，取**更严格**（更高）的那个。
        #
        # 单一参考都有盲区：只按主体中位数算，高频本来就弱的管弦/人声会被
        # 读成「早早就截止」；只按峰值算，低频能量很强的流行/摇滚会把编码器
        # 残留的量化噪声也算作内容，128k 转码被读成满带宽（实测过）。
        # 取交集后两种素材都不会走偏。
        peak_thr = float(np.max(scope)) - CUTOFF_PEAK_DB
        ref_mask = (freqs[:limit] >= 300) & (freqs[:limit] <= 3000)
        if np.any(ref_mask):
            median_thr = float(np.median(scope[ref_mask])) - CUTOFF_MEDIAN_DB
        else:
            median_thr = peak_thr
        threshold = max(peak_thr, median_thr)

    above = scope > threshold
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

    # 陡度：截止点往前 2kHz 内的平均下降速度，同时给出 dB/kHz 与 dB/oct
    band = (freqs >= max(1000.0, cutoff - 2000)) & (freqs <= cutoff + 200)
    steepness = 0.0
    steepness_oct = 0.0
    if np.count_nonzero(band) >= 6:
        seg_db = db[band]
        seg_hz = freqs[band]
        drop = float(np.median(seg_db[:3]) - np.median(seg_db[-3:]))
        span_khz = max(0.001, (seg_hz[-1] - seg_hz[0]) / 1000.0)
        steepness = drop / span_khz
        # 倍频程数：log2(f_high / f_low)，与文献中的 dB/oct 阈值可比
        if seg_hz[0] > 0 and seg_hz[-1] > seg_hz[0]:
            octaves = float(np.log2(seg_hz[-1] / seg_hz[0]))
            if octaves > 0.02:
                steepness_oct = drop / octaves
    return cutoff, steepness, steepness_oct


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


def _edge_energy(
    freqs: np.ndarray,
    db: np.ndarray,
    cutoff: float,
    nyquist: float,
    peak: float,
) -> float:
    """截止之上到 Nyquist 的能量（相对频谱峰值），用于判断「扩展带是否空着」。

    升采样的核心破绽在这里：44.1kHz 素材升到 48kHz 后，22.05kHz 以上只剩下
    插值滤波器的残余，能量比真 48kHz 母带低几十 dB（实测 -114 ~ -124dB）；
    而真母带在那个区间仍有 -60 ~ -80dB 的音乐内容。
    """
    low = cutoff + UPSAMPLE_EDGE_OFFSET_HZ
    high = nyquist * 0.98
    if high <= low:
        return -200.0
    mask = (freqs >= low) & (freqs <= high)
    if not np.any(mask):
        return -200.0
    return float(np.median(db[mask]) - peak)


def _spectral_holes(freqs: np.ndarray, db: np.ndarray) -> float:
    """4~16kHz 内窄带凹陷的个数，低码率编码器「位不够」时留下的瑞士奶酪特征。

    做法：用滑动中位数当基线，统计「低于基线 HOLE_DEPTH_DB 以上、宽度小于
    HOLE_MAX_WIDTH_HZ」的连续段。自然音乐的谱线也有起伏，但很少形成这种
    「窄而深」的缺口，所以这个指标只做辅助证据，不单独改判。
    """
    mask = (freqs >= HOLE_BAND[0]) & (freqs <= HOLE_BAND[1])
    count = int(np.count_nonzero(mask))
    if count < 64:
        return 0.0
    seg = db[mask]
    seg_f = freqs[mask]
    # 基线窗口约 500Hz，够宽以跨过单个谐波，又够窄以保留编码器的宽凹陷
    span = max(9, int(round(500.0 / max(1e-6, seg_f[1] - seg_f[0]))))
    if span % 2 == 0:
        span += 1
    if span >= seg.size:
        return 0.0
    padded = np.pad(seg, span // 2, mode="edge")
    windows = np.lib.stride_tricks.sliding_window_view(padded, span)
    baseline = np.median(windows, axis=1)
    dip = seg < (baseline - HOLE_DEPTH_DB)

    holes = 0
    index = 0
    while index < dip.size:
        if not dip[index]:
            index += 1
            continue
        end = index
        while end + 1 < dip.size and dip[end + 1]:
            end += 1
        width = float(seg_f[min(end, seg_f.size - 1)] - seg_f[index])
        if width <= HOLE_MAX_WIDTH_HZ:
            holes += 1
        index = end + 1
    return float(holes)


def _sfb21_notch(freqs: np.ndarray, db: np.ndarray) -> float:
    """15.5~16.8kHz 的凹陷深度（dB）。

    MP3 最后一个长窗 scalefactor band 的边界（sfb21）即使在标称截止之上
    也常常位不够，留下一个窄凹。深度超过阈值时是很强的 MP3 证据。
    """
    band = (freqs >= SFB21_BAND[0]) & (freqs <= SFB21_BAND[1])
    if np.count_nonzero(band) < 8:
        return 0.0
    left = (freqs >= 13_000.0) & (freqs < SFB21_BAND[0])
    right = (freqs > SFB21_BAND[1]) & (freqs <= 19_000.0)
    refs = [
        float(np.median(db[m]))
        for m in (left, right)
        if np.count_nonzero(m) >= 8
    ]
    if not refs:
        return 0.0
    reference = float(np.mean(refs))
    dip = float(np.min(db[band]))
    return max(0.0, reference - dip)


def _spectral_roughness(freqs: np.ndarray, db: np.ndarray) -> float:
    """4~16kHz 内相邻频点幅度差的均方根（dB）。

    有损编码把频谱系数逐个量化成离散台阶，相邻频点之间会留下不自然的幅度
    跳变（"锯齿"）；真无损的频谱包络平滑，相邻频点差异小。与
    :func:`_spectral_holes` 互补 —— 空洞看"窄而深的缺口"，这里看"整段的
    锯齿程度"，主要用来抓 256~320k / AAC 这种"高频看似完整、实则被量化
    打得稀碎"的高码率转码（现有 cutoff 判据的盲区）。
    """
    mask = (freqs >= HOLE_BAND[0]) & (freqs <= HOLE_BAND[1])
    if np.count_nonzero(mask) < 64:
        return 0.0
    seg = db[mask]
    if seg.size < 2:
        return 0.0
    diff = np.diff(seg)
    return float(np.sqrt(np.mean(diff * diff)))


def _bit_depth_evidence(samples: np.ndarray) -> tuple[bool, float]:
    """返回 ``(是否存在超过 16bit 上限的样本, 峰值电平 dBFS)``。

    ffmpeg 把 16bit 源解码成 s32 时左移 16 位，24bit 源左移 8 位。
    所以「样本值是否超过 ``32767 << 16``」直接回答「这份内容到底有没有
    用到 16bit 以上的精度」，比看容器声明可靠 —— 16bit 素材零填充成
    24bit 后，样本永远碰不到 16bit 天花板。

    峰值电平用来排除「整段近乎无声」的情况：那种文件本来就用不满动态
    范围，据此判位深注水会冤枉人。
    """
    if samples.size == 0:
        return False, -200.0
    values = np.abs(np.asarray(samples).astype(np.int64))
    peak = int(values.max()) if values.size else 0
    above_16 = peak > (32767 << 16)
    # s32 满刻度 = 2**31
    peak_dbfs = 20.0 * np.log10(max(peak, 1) / float(1 << 31))
    return above_16, float(peak_dbfs)


def _frame_spectra(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """分帧 FFT，返回 ``(每帧 dB 矩阵, 频率轴)``。

    与 :func:`_average_spectrum` 的区别是**不做平均**：保留每一帧，才能统计
    「电平随时间起伏多少」。这是区分「音乐内容」与「调制器噪声」的关键。
    """
    x = np.asarray(samples, dtype=np.float64)
    if x.size < FFT_SIZE:
        return np.empty((0, 0)), np.empty(0)
    window = np.hanning(FFT_SIZE)
    hop = FFT_SIZE // 2
    rows = []
    for start in range(0, x.size - FFT_SIZE + 1, hop):
        chunk = x[start : start + FFT_SIZE]
        if not np.any(chunk):
            continue
        rows.append(20.0 * np.log10(np.abs(np.fft.rfft(chunk * window)) + 1e-10))
    if not rows:
        return np.empty((0, 0)), np.empty(0)
    return np.array(rows), np.fft.rfftfreq(FFT_SIZE, d=1.0 / sample_rate)


def _variation_edge(
    rows: np.ndarray, freqs: np.ndarray, nyquist: float
) -> tuple[float, float, float]:
    """用「帧间电平波动」定位音频内容的上边界。

    PCM 源码在 Nyquist 之上只剩调制器噪声，电平不随音乐走（实测标准差
    0.6~1.5dB）；音乐内容所在的频段会随乐句大幅起伏（5~12dB）。所以波动
    曲线从「高」跌回噪声水平的那个频率，就是源码的带宽边界。

    这条判据的最大好处是**不看绝对电平** —— 调制器噪声抬得再高，也抬不出
    「跟着音乐一起动」这个特征，因此能抓住台阶被噪声盖住的转制文件。

    返回 ``(边界频率Hz, 边界下方波动dB, 噪声基准波动dB)``；无法判定返回全 0。
    """
    if rows.size == 0 or freqs.size == 0:
        return 0.0, 0.0, 0.0
    frames = rows.shape[0]
    if frames < 8:
        return 0.0, 0.0, 0.0

    ref_mask = (freqs >= 300) & (freqs <= 3000)
    if not np.any(ref_mask):
        return 0.0, 0.0, 0.0
    reference = float(np.median(np.median(rows[:, ref_mask], axis=1)))

    # 2kHz 一档从 14kHz 扫到 min(0.45·Nyquist, 150kHz)。
    # 起点取 14k（而不是 15k）是为了让档位边界落在 22/24/48/96kHz 这些
    # PCM Nyquist 上 —— 否则 21~23kHz 这种跨界档会把台阶抹平，边界估偏 1kHz。
    step = 2_000.0
    top = min(nyquist * 0.45, 150_000.0)
    bands: list[tuple[float, float]] = []
    freq = 14_000.0
    while freq + step <= top:
        mask = (freqs >= freq) & (freqs < freq + step)
        if np.count_nonzero(mask) >= 8:
            per_frame = np.median(rows[:, mask], axis=1) - reference
            bands.append((freq, float(np.std(per_frame))))
        freq += step
    if len(bands) < 4:
        return 0.0, 0.0, 0.0

    # 噪声基准取 40kHz 以上各段波动的最小值：真 DSD 的整形噪声与 PCM 之上的
    # 调制器噪声都不随音乐起伏，标准差都趋近 1dB，最小值最能代表「纯噪声」。
    # 但要**封顶**：若整条频谱都在起伏（内容一路延伸上去），最小值也会很高，
    # 阈值跟着抬高就会把「处处是内容」误判成「处处是边界」。
    above = [std for freq, std in bands if freq >= 40_000.0]
    floor = min(above) if above else min(std for _, std in bands)
    floor = min(floor, DSD_EDGE_VARIATION_DB)
    threshold = max(DSD_EDGE_VARIATION_DB, floor * 1.8)

    # 从低往高扫：第一个「它自己以及它之上全部不超过阈值」的频段就是边界。
    # 必须要求「之上全部」都低，否则会把某段安静的乐句误当成边界。
    for index, (freq, std) in enumerate(bands):
        if std <= threshold and all(s <= threshold for _, s in bands[index:]):
            return freq, std, floor
    return 0.0, 0.0, floor


def _match_edge_grid(edge_hz: float) -> float:
    """把信号边界匹配到 PCM 采样率网格，命中则返回源码采样率（Hz）。

    容差取 ``max(2kHz, 10%×边界)`` 并允许边界略低于网格点，原因有两个：
    转换链上的重采样滤波器会把边界向上抹宽（实测 44.1k 素材在 48k 链路上
    可拖到 25kHz、96k 素材可拖到 52kHz），而分段统计本身有 ±2kHz 的量化误差。
    22.05k 与 24k 相距太近会互相吞掉，但两者都意味着「PCM 转制」，
    命中哪一个都不影响结论。
    """
    best = 0.0
    best_delta = 9e9
    for boundary in DSD_EDGE_GRID:
        tol = max(2_000.0, boundary * 0.10)
        delta = abs(edge_hz - boundary)
        if delta <= tol and delta < best_delta:
            best, best_delta = boundary * 2.0, delta
    return best


def _pcm_upsample_cliff(
    freqs: np.ndarray, db: np.ndarray, nyquist: float
) -> tuple[float, float]:
    """PCM 文件里检测「更低的 PCM 采样率」留下的带宽台阶。

    44.1k→48k 升采样后，22.05kHz 处会有一个台阶；48k→96k 在 24kHz、
    96k→192k 在 48kHz。与 :func:`_pcm_source_cliff` 的区别是：这里**不看
    台阶之上的绝对电平**，只看台阶本身 —— 因为有些工具会加 dither 把台阶
    填成噪声伪装成内容（实测台阶之上只有 -53 ~ -60dB，不是数字静音），
    但台阶 ≥18dB 本身就是「上面是断层、不是自然滚降」的铁证。

    返回 ``(源码采样率Hz, 台阶深度dB)``；没找到返回 ``(0, 0)``。
    """
    best: tuple[float, float] = (0.0, 0.0)
    for boundary in PCM_NYQUISTS:
        if boundary >= nyquist * 0.97:
            continue
        below = _band_energy(freqs, db, boundary * 0.93, boundary * 0.99)
        above = _band_energy(freqs, db, boundary * 1.01, boundary * 1.08)
        step = below - above
        if step >= PCM_UPSAMPLE_STEP_DB and step > best[1]:
            best = (boundary * 2.0, step)
    return best


def _pcm_source_cliff(
    freqs: np.ndarray, db: np.ndarray, nyquist: float
) -> tuple[float, float, float]:
    """找 PCM 转 DSD 留下的带宽断崖。

    返回 ``(源采样率Hz, 落差dB, 断崖之上能量dB)``；没找到返回 ``(0, 0, 0)``。

    原理：PCM 源码的带宽边界转成 DSD 后仍然留在基带上 —— 44.1k 素材在
    22.05kHz 以上是空白，48k 在 24kHz 以上，96k 在 48kHz 以上。DSD 调制器
    会把这片空白抬成噪声整形，但抬升后的电平仍显著低于真 DSD 在超声区的
    基底（实测 PCM 转制 -80dB 上下，真 SACD -40dB 上下）。

    所以「Nyquist 处有 20dB 以上断崖 + 断崖之上是数字静音」几乎只可能是转制。
    比原来的「固定查 22.05kHz」通用得多：48k/96k/192k 源码都能抓。
    """
    best: tuple[float, float, float] = (0.0, 0.0, 0.0)
    for boundary in PCM_NYQUISTS:
        if boundary * 1.07 >= nyquist:
            continue
        below = _band_energy(freqs, db, boundary * 0.93, boundary * 0.99)
        above = _band_energy(freqs, db, boundary * 1.01, boundary * 1.06)
        step = below - above
        if step >= PCM_CLIFF_STEP_DB and above <= PCM_CLIFF_SILENCE_DB and step > best[1]:
            best = (boundary * 2.0, step, above)
    return best


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
    base_silence: float,
    *,
    pcm_source: float = 0.0,
    pcm_step: float = 0.0,
    pcm_above: float = 0.0,
    ultra_body: float = 0.0,
    edge_hz: float = 0.0,
    edge_std: float = 0.0,
    edge_floor: float = 0.0,
) -> tuple[str, float, list[str]]:
    """DSD 判定：先确认「是不是真 DSD 比特流」，再找源码带宽的破绽。

    * **噪声整形斜率**（50→100kHz，dB/oct）确认比特流本身是真的 —— PCM 直接
      改后缀的假 DSD 在超声区是平坦的；
    * **PCM 断崖** —— 源码的带宽边界会留在基带上：44.1k 源在 22.05kHz、
      48k 源在 24kHz、96k 源在 48kHz。断崖之上若是数字静音，说明那片高频
      从来就不存在，只是被调制器抬起来了；
    * **信号边界**（V1.2.6，与断崖正交）—— 用帧间电平波动定位「还跟着音乐
      起伏」的最高频率。断崖判据依赖绝对电平，调制器带内噪声高的转制文件
      （Taylor Swift 那类 DSD128）台阶会被噪声完全盖住；波动判据不看电平，
      只看「那里还有没有内容」，因此能补上这个盲区；
    * **CD 边界**（兜底）—— 基带截止落在 22.05kHz 附近且之上为数字静音；
    * **超声基底**（辅助）—— 真 DSD 在 30~40kHz 已有 -29 ~ -46dB 的噪声整形
      能量，PCM 转制只有 -59 ~ -61dB。
    """
    reasons: list[str] = []

    # ---- 1. 噪声整形：区分「真 DSD 比特流」与「PCM 改后缀」 ----
    if slope < DSD_SLOPE_SUSPECT or ultra < -58:
        reasons.append(
            f"超声区平坦（斜率 {slope:+.1f}dB/oct、50kHz 以上仅 {ultra:.0f}dB），"
            "缺少 Sigma-Delta 噪声整形，疑似 PCM 升频/转制为 DSD"
        )
        return VERDICT_FAKE_DSD, 0.25, reasons

    # ---- 2. PCM 带宽断崖：通用于 44.1 / 48 / 96 / 192kHz 源码 ----
    if pcm_source > 0:
        reasons.append(
            f"基带在 {pcm_source / 2000:.2f}kHz 出现 {pcm_step:.0f}dB 断崖，"
            f"正是 {pcm_source / 1000:.1f}kHz PCM 的 Nyquist 边界；"
            f"断崖之上仅 {pcm_above:.0f}dB（数字静音）"
        )
        reasons.append(
            "真 DSD 的能量会一路延伸进噪声整形区、不会在某个 PCM 采样率边界"
            "整齐断掉，因此判定为 PCM 转制"
        )
        return VERDICT_FAKE_DSD, 0.3, reasons

    # ---- 3. 信号边界落在 PCM 网格：内容只在 PCM 带宽内存在 ----
    # 与上一条的区别：上一条要求「断崖之上是数字静音」，这一条只要求
    # 「之上没有内容了」—— 噪声抬得再高也逃不掉。
    if edge_hz >= 12_000.0:
        if edge_hz < DSD_SUB_CD_EDGE_HZ:
            reasons.append(
                f"音频内容在 {edge_hz / 1000:.1f}kHz 处就不再随音乐起伏"
                f"（40kHz 以上噪声基准 {edge_floor:.1f}dB），带宽连 CD 的 22.05kHz 都不到"
            )
            reasons.append(
                "这个位置正是有损编码器的低通截止（128k / 192k / 320k MP3 分别切在 "
                "16 / 19 / 20kHz），说明源码是有损文件，不可能是 DSD 录音"
            )
            return VERDICT_FAKE_DSD, 0.3, reasons
        matched = _match_edge_grid(edge_hz)
        if matched:
            reasons.append(
                f"音频内容在 {edge_hz / 1000:.1f}kHz 处不再随音乐起伏"
                f"（该段帧间波动 {edge_std:.1f}dB，40kHz 以上噪声基准 {edge_floor:.1f}dB），"
                f"边界正好落在 {matched / 1000:.1f}kHz PCM 的 Nyquist 上"
            )
            reasons.append(
                "DSD 的超声带宽是它的立身之本：内容在某个 PCM 采样率边界上整齐"
                "失去，只可能是 PCM 源码被调制器「抬」成了 DSD，判定为 PCM 转制"
            )
            return VERDICT_FAKE_DSD, 0.35, reasons

    # ---- 4. CD 边界（旧判据兜底）：截止落在 22.05kHz 且之上数字静音 ----
    cd_boundary = 0.0 < base_cutoff < DSD_CD_BOUNDARY_HZ
    digital_silence = base_silence < DSD_DIGITAL_SILENCE_DB
    hard_cliff = base_steep > DSD_BASEBAND_CLIFF_STEEP

    if cd_boundary and (digital_silence or hard_cliff):
        detail = []
        if digital_silence:
            detail.append(f"截止之上为数字静音（{base_silence:.0f}dB）")
        if hard_cliff:
            detail.append(f"断崖截止（陡度 {base_steep:.0f}dB/kHz）")
        reasons.append(
            f"基带在 {base_cutoff / 1000:.2f}kHz 截止（CD 的 22.05kHz 边界），"
            f"且{'、'.join(detail)}；"
            f"噪声整形（{slope:+.1f}dB/oct）来自 PCM→DSD 调制器，"
            "疑似 CD/有损源转制"
        )
        return VERDICT_FAKE_DSD, 0.3, reasons

    # ---- 5. 噪声整形弱、但基带不是 CD 边界：可能是高规格 PCM 转制 ----
    if slope < DSD_SLOPE_GENUINE:
        reasons.append(
            f"噪声整形偏弱（斜率 {slope:+.1f}dB/oct、50kHz 以上 {ultra:.0f}dB），"
            "可能是高规格 PCM 转制或解码器做了超声低通"
        )
        return VERDICT_FAKE_DSD, 0.5, reasons

    reasons.append(
        f"噪声整形斜率 {slope:+.1f}dB/oct、50kHz 以上能量 {ultra:.0f}dB，"
        f"且基带未在 PCM 采样率边界断崖（{base_cutoff / 1000:.1f}kHz），"
        "符合真 DSD 录音特征"
    )
    if ultra_body > DSD_ULTRA_BODY_DB:
        reasons.append(
            f"30~40kHz 已有 {ultra_body:.0f}dB 的噪声整形基底，与真 SACD 一致"
        )
    return VERDICT_DSD, 0.9, reasons


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
    base_silence: float = 0.0,
    steep_oct: float = 0.0,
    edge_db: float = 0.0,
    ratio: float = 0.0,
    holes: float = 0.0,
    sfb21: float = 0.0,
    above16: bool = False,
    peak_dbfs: float = 0.0,
    depth_padding: bool = False,
    pcm_source: float = 0.0,
    pcm_step: float = 0.0,
    pcm_above: float = 0.0,
    ultra_body: float = 0.0,
    edge_hz: float = 0.0,
    edge_std: float = 0.0,
    edge_floor: float = 0.0,
    upsample_src: float = 0.0,
    upsample_step: float = 0.0,
) -> tuple[str, float, list[str]]:
    """三维度判定：转码 / 升采样 / 升位深，各自独立成立。

    早先是一棵「只看截止频率」的决策树，两端都会错：老磁带被当成转码，
    320k 又被当成真无损。现在按证据类型分开判 —— 一份文件可以同时是
    「CD 转 320k MP3 再升采样到 96k/24bit」，只报一个结论装不下。

    判定顺序按证据的确定性排：升采样（扩展带是否空着）> 升位深（样本是否
    碰到 16bit 天花板）> 转码（截止位置 + 陡度 + 频谱空洞）。
    """
    reasons: list[str] = []
    sample_rate = info.sample_rate or 44100
    nyquist = sample_rate / 2.0
    is_dsd = info.fmt in ("DSF", "DFF") or bool(info.dsd_rate)
    ratio = ratio or ((cutoff / nyquist) if nyquist else 0.0)

    if is_dsd:
        return _judge_dsd(
            slope, ultra, base_cutoff, base_steep, base_silence,
            pcm_source=pcm_source, pcm_step=pcm_step,
            pcm_above=pcm_above, ultra_body=ultra_body,
            edge_hz=edge_hz, edge_std=edge_std, edge_floor=edge_floor,
        )

    # ---- 1. 升采样：声明高采样率，但扩展带是空的 ----
    #
    # 44.1kHz 素材升到 48/96kHz 后，原 Nyquist 之上只剩插值滤波器的残余，
    # 能量比真母带低几十 dB（实测 -114 ~ -124dB）。两条独立证据任一成立即可：
    #   * 截止正好落在 CD 的 22.05kHz 边界 —— 升采样最典型的指纹；
    #   * 扩展带能量低到 -105dB 以下 —— 连插值残余都算不上，只能是数字静音。
    if nyquist >= 23000 and ratio < UPSAMPLE_RATIO and edge_db < UPSAMPLE_EDGE_DB:
        at_cd_edge = abs(cutoff - 22050.0) <= 400.0
        if at_cd_edge or edge_db < -105.0:
            reasons.append(
                f"声明 {sample_rate / 1000:.1f}kHz（上限 {nyquist / 1000:.1f}kHz），"
                f"但内容在 {cutoff / 1000:.2f}kHz 就结束（带宽 {ratio:.0%}），"
                f"截止之上仅 {edge_db:.0f}dB"
            )
            if at_cd_edge:
                reasons.append("截止位置正好落在 CD 的 22.05kHz 边界，符合 44.1kHz 升采样特征")
            reasons.append("扩展带为空说明上面没有真实录音内容，疑似采样率注水")
            return VERDICT_UPSAMPLED, 0.3, reasons

    # ---- 1b. 源码带宽台阶：声明 hi-res，但内容停在更低的采样率边界 ----
    #
    # 上面的分支要求「扩展带是数字静音（<-95dB）」，但有些升采样工具会故意
    # 加 dither 把台阶之上填成 -50~-70dB 的噪声，伪装成「有超声内容」。
    # 这种时候扩展带不空，但「台阶」仍在 —— 自然滚降不会在某个采样率边界
    # 上突然跌 18dB。所以在 analyze() 里已经用 _pcm_upsample_cliff 找过台阶，
    # 或判断「截止正好落在 22.05k」。命中任一即判升频。
    if nyquist >= 23000 and upsample_src > 0:
        reasons.append(
            f"声明 {sample_rate / 1000:.1f}kHz（上限 {nyquist / 1000:.1f}kHz），"
            f"但内容在 {upsample_src / 2000:.2f}kHz 处出现台阶"
            + (f"（{upsample_step:.0f}dB）" if upsample_step else "")
            + f"，正是 {upsample_src / 1000:.1f}kHz PCM 的 Nyquist 边界"
        )
        reasons.append(
            "台阶之上是被填充的噪声而非真实内容 —— 44.1kHz 升采样后加噪声"
            "伪装成 48kHz 的典型手法"
        )
        return VERDICT_UPSAMPLED, 0.3, reasons

    # ---- 2. 升位深：声明 24bit，但样本从未碰到 16bit 天花板 ----
    #
    # 16bit 素材零填充成 24bit 时，样本绝对值永远 ≤ 32767（在 s32 容器里
    # 对应 32767<<16）。LAC 2015 靠这一条做到 100% 准确率，因为它是纯时域
    # 事实，不依赖「音乐该长什么样」。
    #
    # 这里刻意不依赖「尾随零个数」推断位深：ffmpeg 把 s24le 解码成 s32 时
    # 会经浮点中转，低位不一定保持全零（实测低 8 位零只占 9%），用尾随零
    # 判断会漏掉填充。只看「有没有样本越过 16bit 上限」+「电平是否正常」。
    if (
        info.bit_depth
        and info.bit_depth >= 24
        and not above16
        and peak_dbfs > -40.0
    ):
        reasons.append(
            f"声明 {info.bit_depth}bit，但所有样本都未超出 16bit 动态范围"
            f"（峰值 {peak_dbfs:.1f}dBFS），疑似低位深零填充"
        )
        return VERDICT_UPSCALED, 0.35, reasons

    # ---- 3. 数字静音：截止之上没有模拟底噪，是有损转码的铁证 ----
    #
    # 陡度分不开「编码器低通」与「母带自然滚降」—— 实测 192k/320k 转码的
    # 陡度只有 16dB/kHz，与模拟母带转录（14~16dB/kHz）完全重叠。
    # 但两者的**噪声性质**不同：编码器砍掉高频后上面是数字空白（-100dB 以下），
    # 磁带/话筒/母带在截止之上总有模拟底噪（-70 ~ -85dB）。
    # 所以「带宽不足 + 截止之上是数字静音」比陡度可靠得多。
    if (
        0 < cutoff < nyquist * 0.95
        and -195.0 < edge_db < DIGITAL_SILENCE_EDGE_DB
    ):
        reasons.append(
            f"内容在 {cutoff / 1000:.1f}kHz 结束（带宽 {ratio:.0%}），"
            f"截止之上为数字静音（{edge_db:.0f}dB）"
        )
        reasons.append(
            "母带/磁带在截止之上会有模拟底噪，数字空白说明高频是被编码器砍掉的"
        )
        if ratio < 0.85:
            return VERDICT_FAKE, 0.85, reasons
        return VERDICT_SUSPECT, 0.5, reasons

    # ---- 4. 转码检测：按截止位置分档 ----
    if nyquist <= 24000:
        # CD 标准音源（44.1k / 48k）。
        #
        # 关键：cutoff 偏低不等于假无损。1980~90 年代的老录音、模拟母带转录，
        # 高频本来就不丰富，频谱会**平滑滚降**到 16~18kHz —— 这是真 CD 的
        # 正常特征，不能按「转码砍频」判假。
        # 区分依据是陡度：有损转码是断崖式下跌（>25dB/kHz），老录音是平缓滚降
        # （通常 <15dB/kHz）。陡度为 0 表示无法计算，保守按转码处理。
        smooth_rolloff = 0.5 < steepness < 25.0

        # 频谱空洞、sfb21 凹陷：低码率 MP3 的辅助证据，只用来加重措辞
        mp3_marks = []
        if holes >= 3.0:
            mp3_marks.append(f"4~16kHz 有 {holes:.1f} 个/帧窄带空洞")
        if sfb21 >= SFB21_DEPTH_DB:
            mp3_marks.append(f"16kHz 附近有 {sfb21:.0f}dB 的 sfb21 凹陷")

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
            if mp3_marks:
                reasons.append("佐证：" + "、".join(mp3_marks))
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
            if mp3_marks:
                reasons.append("佐证：" + "、".join(mp3_marks))
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
            if mp3_marks:
                reasons.append("佐证：" + "、".join(mp3_marks))
            return VERDICT_SUSPECT, 0.45, reasons
        reasons.append(f"频谱平滑延伸到 {cutoff / 1000:.1f}kHz（上限 {nyquist / 1000:.1f}kHz）")
        if mp3_marks:
            # 截止正常但空洞/凹陷明显：典型 320k 的高频「看似完整、实则稀疏」
            reasons.append(
                "但" + "、".join(mp3_marks) + "，仍可能是高码率转码，建议看完整频谱"
            )
            return VERDICT_SUSPECT, 0.5, reasons
        if eff_depth and info.bit_depth and eff_depth < info.bit_depth - 2:
            reasons.append(
                f"有效位深仅 {eff_depth}bit，声明 {info.bit_depth}bit，疑似低位深填充"
            )
            return VERDICT_SUSPECT, 0.6, reasons
        return VERDICT_TRUE, 0.85, reasons

    # 高采样率（88.2kHz 及以上），且扩展带不空 —— 到这里说明带宽是真的
    if ratio < 0.70:
        reasons.append(
            f"采样率 {sample_rate / 1000:.1f}kHz，但频谱只到 {cutoff / 1000:.1f}kHz"
            f"（带宽利用率 {ratio:.0%}），疑似 44.1kHz 内容升频"
        )
        return VERDICT_UPSAMPLED, 0.35, reasons
    reasons.append(
        f"高频延伸到 {cutoff / 1000:.1f}kHz（带宽 {ratio:.0%}、扩展带 {edge_db:.0f}dB），"
        "符合高解析音源特征"
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
    above_16 = False
    peak_dbfs = -200.0
    is_dsd = info.fmt in ("DSF", "DFF") or bool(info.dsd_rate)
    # DSD 的「信号边界」判据要保留整段样本做分帧统计，取能量最大的一段
    # （前奏/淡出段内容少，波动曲线会失真）
    edge_samples: Optional[np.ndarray] = None
    edge_rate = 0
    edge_peak = 0.0
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
        seg_above16, seg_dbfs = _bit_depth_evidence(samples)
        above_16 = above_16 or seg_above16
        peak_dbfs = max(peak_dbfs, seg_dbfs)
        if is_dsd:
            seg_peak = float(np.max(np.abs(samples))) if samples.size else 0.0
            if seg_peak > edge_peak:
                edge_samples, edge_rate, edge_peak = samples, sample_rate, seg_peak

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
    hole_counts: list[float] = []
    notch_depths: list[float] = []
    for freqs, db in spectra:
        cutoff, _, _ = _find_cutoff(freqs, db)
        if cutoff > 0:
            cutoffs.append(cutoff)
        hole_counts.append(_spectral_holes(freqs, db))
        notch_depths.append(_sfb21_notch(freqs, db))
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

    _, steepness, steep_oct = _find_cutoff(freqs, db)
    # 各段截止差异过大：多为包含前奏/静音的正常歌曲，在理由里注明，避免用户误以为鉴定不稳定
    spread_note = ""
    if cutoff_max - cutoff_min > 3000:
        spread_note = f"（各段截止 {cutoff_min / 1000:.1f}~{cutoff_max / 1000:.1f}kHz，含静音/前奏段）"

    slope = 0.0
    ultra = 0.0
    base_cutoff = 0.0
    base_steep = 0.0
    base_silence = 0.0
    pcm_source = 0.0
    pcm_step = 0.0
    pcm_above = 0.0
    ultra_body = 0.0
    edge_hz = 0.0
    edge_std = 0.0
    edge_floor = 0.0
    if is_dsd:
        nyq = float(result.nyquist_hz or 352800)
        # 噪声整形斜率（确认是不是真的 DSD 比特流）
        slope = _noise_shaping_slope(freqs, db, nyq)
        # 超声能量比（辅助）
        ultra = _band_energy(freqs, db, DSD_ULTRA_BAND[0], min(DSD_ULTRA_BAND[1], nyq))
        # 基带截止只在 24kHz 以下找，避免 DXD 的 176kHz 缺口被误当 CD 墙
        base_cutoff, base_steep, _ = _find_cutoff(
            freqs, db, max_hz=DSD_BASEBAND_MAX, reference="median"
        )
        # 基带截止之上是否数字静音（CD 转 DSD 的核心破绽）
        base_silence = _band_energy(
            freqs, db, DSD_SILENCE_BAND[0], min(DSD_SILENCE_BAND[1], nyq)
        )
        high_band = _band_energy(freqs, db, 40000, min(nyq, 140000))
        # PCM 转制的带宽断崖（44.1/48/96/192k 源码通用）+ 噪声整形基底
        pcm_source, pcm_step, pcm_above = _pcm_source_cliff(freqs, db, nyq)
        ultra_body = _band_energy(freqs, db, 30000, min(40000, nyq))
        # 信号边界：内容消失在哪个频率（不看电平，只看波动）
        if edge_samples is not None and edge_rate:
            rows, row_freqs = _frame_spectra(edge_samples, edge_rate)
            edge_hz, edge_std, edge_floor = _variation_edge(
                rows, row_freqs, float(edge_rate) / 2.0
            )
        result.noise_shaping_slope = slope
        result.ultrasonic_ratio = ultra
        result.baseband_cutoff = int(base_cutoff)
        result.baseband_silence = base_silence
        result.signal_edge_hz = int(edge_hz)
        result.signal_edge_std = edge_std
    else:
        high_band = _band_energy(freqs, db, 20000, min(result.nyquist_hz or 22050, 40000))
    result.cutoff_hz = int(cutoff)
    result.steepness_db = steepness
    result.steepness_oct = steep_oct
    result.high_band_energy_db = high_band
    result.bit_depth_effective = deepest_depth
    result.analyzed_seconds = analyzed

    # ---- 三维度证据：峰值 / 扩展带 / 带宽比例 / 空洞 / 位深 ----
    peak = float(np.max(db))
    nyq = float(result.nyquist_hz or 0)
    bandwidth = (cutoff / nyq) if nyq else 0.0
    edge_db = _edge_energy(freqs, db, cutoff, nyq, peak) if nyq else 0.0
    # PCM 升采样：声明 hi-res，但内容停在更低的 PCM 采样率边界。
    # 噪声填充会把台阶之上填到 -50~-70dB，所以单看 edge_db 会漏；
    # 这里直接测「台阶本身」，再补一条「截止正好落在 22.05k」。
    upsample_src = 0.0
    upsample_step = 0.0
    if not is_dsd and nyq >= 23000:
        upsample_src, upsample_step = _pcm_upsample_cliff(freqs, db, nyq)
        if not upsample_src and abs(cutoff - 22050.0) <= 400.0:
            upsample_src, upsample_step = 44100.0, 0.0
    holes = float(np.median(hole_counts)) if hole_counts else 0.0
    notch = float(np.median(notch_depths)) if notch_depths else 0.0
    roughness = _spectral_roughness(freqs, db)
    # 位深注水：声明 24bit 却从没用到 16bit 以上（且电平正常，排除近乎无声的文件）
    depth_padding = bool(
        info.bit_depth
        and info.bit_depth >= 24
        and not above_16
        and peak_dbfs > -40.0
    )
    result.peak_db = peak
    result.edge_energy_db = edge_db
    result.bandwidth_ratio = bandwidth
    result.spectral_holes = holes
    result.sfb21_notch_db = notch
    result.bit_above_16 = above_16
    result.bit_depth_padding = depth_padding
    result.spectral_roughness = roughness

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
        base_silence=base_silence,
        steep_oct=steep_oct,
        edge_db=edge_db,
        ratio=bandwidth,
        holes=holes,
        sfb21=notch,
        above16=above_16,
        peak_dbfs=peak_dbfs,
        depth_padding=depth_padding,
        pcm_source=pcm_source,
        pcm_step=pcm_step,
        pcm_above=pcm_above,
        ultra_body=ultra_body,
        edge_hz=edge_hz,
        edge_std=edge_std,
        edge_floor=edge_floor,
        upsample_src=upsample_src,
        upsample_step=upsample_step,
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
    # 同批一致性：同一文件夹的曲目出处一致，个别漏网的按调制器指纹补判
    apply_batch_consensus([(Path(key), value) for key, value in results.items()])
    return results


# ---------------------------------------------------------------- 同批一致性

#: 同批推定：同目录里已确认 PCM 转制的比例达到该值，就对剩余曲目做指纹比对
BATCH_FAKE_RATIO = 0.5
#: 同批推定：调制器指纹（噪声整形斜率 dB/oct）允许的偏差。
#: 实测同一批转换内部标准差 ≤0.2（赵雷 18 首全在 26.79±0.09），
#: 不同转换器之间相差 3~20（全库 6.5~43.5），0.8 是非常宽松的界限。
BATCH_SLOPE_TOLERANCE = 0.8


def apply_batch_consensus(
    items: list[tuple[Path | str, AuthenticityResult]],
) -> list[tuple[Path, AuthenticityResult]]:
    """同目录一致性推定：一批文件出处相同，不会真假混杂。

    现实中「同一文件夹里一首假、其余真」几乎不会发生 —— 同一批资源的来源
    只有一次。而边界判据会漏掉一类情况：**源码音乐本身在 PCM Nyquist 之前
    就自然滚降完了**（人声/民谣的 20kHz 以上只剩气声，30kHz 前已低于 DSD
    噪声），此时 PCM 的硬边界被调制器噪声盖住，看不到，文件就被放行。

    但转换器的噪声整形曲线是一枚稳定的指纹 —— 实测赵雷 18 首跨四张专辑，
    斜率全在 26.79±0.09；而全库不同转换器之间从 6.5 到 43.5 不等。
    所以：同目录里已确认转制的曲目过半时，剩余曲目只要指纹与之一致，
    就是同一次转换的产物。

    只在 DSD 上生效：PCM 侧的「同目录」可能真的是不同来源拼的合辑，
    而 DSD 的批量转制几乎总是一次性作业。

    返回被改判的条目，供界面刷新。
    """
    groups: dict[Path, list[tuple[Path, AuthenticityResult]]] = {}
    for path, result in items:
        groups.setdefault(Path(path).parent, []).append((Path(path), result))

    changed: list[tuple[Path, AuthenticityResult]] = []
    for group in groups.values():
        dsd = [
            (p, r)
            for p, r in group
            if r.verdict in (VERDICT_DSD, VERDICT_FAKE_DSD)
        ]
        if len(dsd) < 3:
            continue
        confirmed = [(p, r) for p, r in dsd if r.verdict == VERDICT_FAKE_DSD]
        if len(confirmed) < 2 or len(confirmed) / len(dsd) < BATCH_FAKE_RATIO:
            continue
        reference = float(np.median([r.noise_shaping_slope for _, r in confirmed]))
        for path, result in dsd:
            if result.verdict != VERDICT_DSD or not result.noise_shaping_slope:
                continue
            if abs(result.noise_shaping_slope - reference) > BATCH_SLOPE_TOLERANCE:
                continue
            result.verdict = VERDICT_FAKE_DSD
            result.score = 0.3
            result.reasons = [
                f"同目录 {len(confirmed)}/{len(dsd)} 首已确认是 PCM 转制；本首虽未找到 "
                f"PCM 边界，但噪声整形斜率 {result.noise_shaping_slope:.1f}dB/oct 与之一致"
                f"（{reference:.1f}±{BATCH_SLOPE_TOLERANCE}），出自同一次转换",
                "同一批资源的出处只有一次：真 SACD 与转制文件不会混在同一批里",
            ] + result.reasons[:1]
            changed.append((path, result))
    return changed


# ---------------------------------------------------------------- 分组（界面筛选 / 批量清理共用）

#: 判定分组：把一个结论映射到「真 / 假 / 可疑 / 未鉴定」四类，
#: 界面筛选与清理范围都用这一份口径，避免两处逻辑各自漂移
VERDICT_GROUPS: dict[str, tuple[str, ...]] = {
    "true": (VERDICT_TRUE, VERDICT_DSD),
    "fake": (VERDICT_FAKE,),
    "suspect": (VERDICT_SUSPECT, VERDICT_FAKE_DSD, VERDICT_UPSAMPLED, VERDICT_UPSCALED),
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
