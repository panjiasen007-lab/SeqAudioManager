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
## 判定与界面配色无关

所有判据都在 **dB 域** 用 numpy 数值计算（FFT 幅度、频段能量、帧间波动），
不读取界面渲染出的像素或颜色；`ui/spectrum.py` 只负责把 dB 矩阵映射成 RGB
用于显示。因此切换主题、调整色阶、换调色板都不会改变鉴定结论。

参考（2026）
------------
* D'Alessandro & Shi, *MP3 Bit Rate Quality Detection through Frequency
  Spectrum Analysis*（ACM MM&Sec 2009）：16~20kHz 的功率谱密度是码率指纹，
  转码后不会改变；256k CBR 与 VBR-0 是唯一天然重叠区。
* Lacroix et al., *Lossless Audio Checker*（AES 139th, 2015）：升位深用纯时域
  判决（样本是否越过 16bit 上限）；升采样看扩展带能量是否高于声明位深对应的
  量化噪声底。
* FLAC Detective（2026）的 Gate D 经验：**「斜率不是墙」**。截止位置本身
  无法区分编码器低通与母带自然滚降，真正能分开的是**截止处 250Hz 内的落差**：
  编码器低通在 250Hz 内掉 20~40dB，自然滚降通常只有几 dB。
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

#: 每段分析时长（秒）。太短频率分辨率不够，太长浪费时间。
#: V1.5.2：4 个取样点 × 4s = 16s。实测 PCM 判定在 8s/16s/24s 下结论一致，
#: 但 16s 的截止/断崖测量更稳（短段受前奏、淡出、单句内容影响更大）。
SEGMENT_SECONDS = 4.0
#: DSD 单独取 3s：DSD 解码本身慢（DSD64 约 2.5s/文件/8s），12s 总时长在
#: 判定不变的前提下把信号边界统计的帧数翻倍。
DSD_SEGMENT_SECONDS = 3.0
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

#: 扩展带「频谱平坦度」阈值（0~1）。这是「谐波相干性」判据：真实乐器的
#: 超声内容是离散谐波峰（平坦度→0），dither 填充是平坦白噪声（→1）。
#: 有些升频工具把 dither 加到与音乐同水平、把台阶完全填平，台阶判据失效；
#: 但填充物的"噪声本质"藏不住。实测纯白噪声 ≈0.99、纯谐波 ≈0.00。
#: 阈值取 0.7 偏保守，只抓"明显是噪声"的扩展带，避免误伤超声区含噪声底
#: 的真实 hi-res（人声/民谣的超声区可能以本底噪声为主）。
EXTENDED_FLATNESS_NOISE = 0.7

#: 频谱平坦度的最少频点数。低于这个数（例如 176.4kHz 采样率、8192 点 FFT
#: 下 1kHz 只有约 46 个频点）几何/算术均值比没有统计意义，函数返回 None。
MIN_FLATNESS_BINS = 32

#: 高采样率 PCM 的「超声衰减」阈值（dB）：80kHz 段相对 20kHz 段的能量差。
#: 真 192k 母带在 40kHz 以上会随频率快速衰减（80kHz 处通常已是数字静音，
#: 相对 20kHz 衰减 -50dB 起步）；而把 44.1k/48k 升到 192k 冒充 hi-res 时
#: 常往超声区灌宽带噪声，实测 Linkin Park 那批 24/192 只衰减 -4 ~ -33dB。
#: 取 -35dB：填充分布（-4 ~ -33）全在阈值之上，自然衰减（-50 以下）留 15dB 余量。
ULTRA_DECAY_MIN_DB = -35.0
#: 超声区**内部**的衰减阈值（dB）：80kHz 段相对 40kHz 段的能量差。
#: 这条最敏感也最本质 —— 只看 40kHz 以上的走势，不受基带内容多寡影响。
#: 灌噪声的实测只有 -2 ~ -9dB（几乎是一条直线）；自然录音在 40kHz 以上
#: 至少有 -20dB 以上的衰减（话筒/ADC 的超声滚降 + 空气吸收）。
ULTRA_SLOPE_MIN_DB = -15.0
#: 80kHz 段的绝对能量上限（相对频谱峰值）。真 192k 母带即使有超声内容，
#: 到这个频率也已经很弱（-90dB 上下）；灌噪声的实测 -64 ~ -84dB。
#: 取 -85：只抓「高得不正常」的那部分，给真母带留余量（主判据是相对衰减，
#: 这条只做补充，宁可漏判也不误伤真 hi-res）。
ULTRA_HIGH_ENERGY_DB = -85.0

#: SBR copy-up 去趋势相关性阈值（0~1）。HE-AAC 的 SBR 把低频子带精确复制
#: 到高频（频率平移），去趋势后高频仍是低频的镜像（实测 ≈0.97）；普通音乐
#: 高低频去趋势后各自独立（实测真 CD 抓轨 0.19~0.46）。0.6 位于两者之间。
SBR_COPYUP_CORR = 0.6

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
#: 边界处必须有的最小电平跌落（dB）。信号边界判据看的是「帧间波动塌陷」，
#: 但波动塌陷不一定是**内容**截止 —— 真 SACD 老录音的高频内容本身就平稳，
#: 穿过 22kHz 时电平只掉几个 dB（实测梅艳芳 BRASIL 89 某首仅 -7.4dB），
#: 帧间波动统计照样判成「边界」。而真 PCM 转制在源码 Nyquist 之上会掉到
#: 调制器噪声底，实测 -41 ~ -71dB。要求至少掉 20dB，才不会把
#: 「高频平缓的老录音」误判成 PCM 转制。
DSD_EDGE_MIN_DROP_DB = 20.0
#: 判定「平缓延续」的最大跌落（dB）。只有真正只掉几个 dB 的老录音才受保护；
#: 原先把门槛放到 -20dB，会把调制噪声已经掉到 -54dB 的 PCM 转制也放行
#: （李烁《花间梦》实测 -54 ~ -59dB，却被当成「平缓延续」）。
DSD_EDGE_SHALLOW_MAX_DB = 12.0
#: 「单音测试信号」的基带能量集中度阈值（前 5 个频点占总能量的比例）。
#: 只有纯音/扫频才会达到 0.97 以上；真实音乐实测 0.31~0.92（流行乐低频能量
#: 本就集中），阈值定低了会把普通歌曲误当测试信号放行。
TONE_TEST_CONCENTRATION = 0.97

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

#: 「墙 vs 斜坡」判据（参考 FLAC Detective Gate D）：
#: 在截止点 ±1kHz 内按 250Hz 分格，取相邻格的最大落差。
#: 编码器低通在 250Hz 内通常掉 20~40dB；母带/磁带自然滚降只有几 dB。
#: 12dB 位于两者之间（实测 LAME 转码 19~51dB，自然滚降 4.5~6.2dB）。
WALL_CELL_HZ = 250.0
WALL_SEARCH_HZ = 1000.0
WALL_MIN_STEP_DB = 12.0
#: 320k 分支的残余能量阈值：断崖之上仍有模拟底噪时更像母带低通，
#: 而不是码率压缩留下的数字空白。
W320_RESIDUAL_DB = -85.0

#: 升采样：截止低于该比例 × Nyquist，且扩展带能量低于阈值
UPSAMPLE_RATIO = 0.96
UPSAMPLE_EDGE_DB = -95.0
#: 「截止之上是数字静音」的阈值：低于它说明那里没有模拟底噪，只剩编码器空白
#: （V1.5.3）原值 -95dB 只覆盖 MP3/Opus 这类「断崖式」低通，漏掉了低通更缓的
#: 编码器：Vorbis q5（约 160k）在 19.1kHz 是软滚降（250Hz 内仅 7.5dB），WMA 192
#: 在 17.7kHz 仅 5.4dB。放宽到 -90dB 可覆盖它们，但**必须同时**用相对音乐主体
#: 的能量否决，否则会误伤老录音（见 ANALOG_FLOOR_MAX_DB）。
DIGITAL_SILENCE_EDGE_DB = -90.0
#: 截止之上的能量相对音乐主体（300Hz~3kHz 中位数）的阈值。
#: 「相对峰值」会随低频峰高低漂移：老录音《今宵多珍重》峰值比主体高 31.6dB，
#: 底噪相对主体仅 -55dB，换算成相对峰值是 -86.6dB，旧阈值会把它当成数字静音。
#: 真正的编码器空白相对主体通常低于 -66dB；磁带/模拟底噪实测 -45 ~ -58dB
#: （《碟中谍7》48k/24 母带 -45 ~ -65dB）。低于 -64dB 才允许判「数字静音」。
ANALOG_FLOOR_MAX_DB = -64.0
#: 扩展带起点相对截止的偏移，跳过过渡带
UPSAMPLE_EDGE_OFFSET_HZ = 1000.0

#: 频谱空洞：4~16kHz 内的窄带凹陷（低码率编码器「位不够」的瑞士奶酪特征）
HOLE_BAND = (4000.0, 16000.0)
HOLE_DEPTH_DB = 18.0
HOLE_MAX_WIDTH_HZ = 600.0

#: sfb21 notch：MP3 最后一个长窗 scalefactor band 边界上的特征凹陷
SFB21_BAND = (15_500.0, 16_800.0)
SFB21_DEPTH_DB = 8.0

#: 收藏最低规格（V1.5）：低于这个标准的 PCM 直接判假无损。
#: 16bit / 44.1kHz / 立体声 是 CD 基线；单声道、32k/22.05k、8bit 均不达标。
MIN_PCM_SAMPLE_RATE = 44_100
MIN_PCM_BIT_DEPTH = 16
MIN_PCM_CHANNELS = 2

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
    wall_step_db: float = 0.0          # 截止处 250Hz 内的最大落差（墙 vs 斜坡）
    peak_db: float = 0.0               # 轨道频谱峰值（绝对 dB），判据的参考点
    edge_energy_db: float = 0.0        # 截止之上到 Nyquist 的能量（相对峰值）
    edge_body_db: float = 0.0          # 截止之上的能量（相对 300Hz~3kHz 主体）
    bandwidth_ratio: float = 0.0       # 截止 / Nyquist
    spectral_holes: float = 0.0        # 每帧平均频谱空洞数（4~16kHz 窄凹）
    sfb21_notch_db: float = 0.0        # 15.5~16.8kHz 凹陷深度（MP3 特征）
    bit_depth_padding: bool = False    # 疑似低位深零填充（升位深）
    bit_above_16: bool = False         # 存在超过 16bit 上限的样本
    bit_depth_declared: int = 0
    bit_depth_effective: int = 0
    spec_low: bool = False             # 低于 16bit/44.1kHz/立体声，直接判假无损
    spectral_roughness: float = 0.0    # 4~16kHz 相邻频点幅度差 RMS（有损编码"锯齿"度）
    extended_flatness: float = 0.0     # 扩展带频谱平坦度（噪声填充型升频的兜底证据）
    sbr_copyup_score: float = 0.0      # SBR copy-up 去趋势相关性（HE-AAC 转码特征）
    tone_concentration: float = 0.0    # 基带前 5 个频点的能量占比（单音测试信号特征）
    high_band_energy_db: float = 0.0   # 20kHz 以上相对能量
    noise_shaping_slope: float = 0.0   # DSD 噪声整形斜率（dB/oct，真 DSD 为正）
    ultrasonic_ratio: float = 0.0      # 50kHz 以上相对能量（dB）
    baseband_cutoff: int = 0           # 基带（≤24kHz）截止频率
    baseband_silence: float = 0.0      # 基带截止之上（22.5~24kHz）的相对能量
    signal_edge_hz: int = 0            # 音频内容的上边界（帧间波动跌回噪声水平处）
    signal_edge_std: float = 0.0       # 边界下方的帧间波动（dB），用来解释判据
    pcm_cliff_source: int = 0          # PCM 带宽断崖命中的源采样率（Hz）；0 = 无断崖（弱证据）
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
        if self.spec_low:
            return "；".join(self.reasons) or "低于最低规格"
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
        if self.wall_step_db:
            parts.append(f"断崖 {self.wall_step_db:.0f}dB")
        elif self.steepness_oct:
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
    sample_rate_hint: int = 0,
) -> Optional[tuple[np.ndarray, np.ndarray, int]]:
    """解码一段音频为左右声道 float32（供频谱分析）。

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
    # 交错立体声：分开保留 L/R，频谱按功率相加。
    # 只取左声道会漏掉「右声道才有超声内容」的素材；直接下混又会产生
    # 相位抵消（(L+R)/2 在某些频点互相抵消，看起来像高频被砍）。
    frames = data[: data.size - data.size % 2].reshape(-1, 2)
    left = np.ascontiguousarray(frames[:, 0])
    right = np.ascontiguousarray(frames[:, 1])

    # 采样率：优先用文件头声明的值。截断/损坏文件解码出的样本数会少于
    # 请求时长，用 data.size/seconds 反推会把 44.1kHz 误算成 33kHz，
    # 进而让 Nyquist、带宽比例全部出错（旧版「上限 16.5kHz」就是这样来的）。
    if sample_rate_hint > 0:
        effective_sr = sample_rate_hint
    else:
        effective_sr = int(round(left.size / seconds)) if seconds > 0 else 0
    # 对齐到常见采样率，避免 ±1 的取整误差影响 Nyquist 判断
    if effective_sr:
        for candidate in (44100, 48000, 88200, 96000, 176400, 192000,
                          352800, 384000, 705600, 1411200, 2822400, 5644800):
            if abs(effective_sr - candidate) / candidate < 0.02:
                effective_sr = candidate
                break
    return left, right, effective_sr


# ---------------------------------------------------------------- 频谱


def _average_spectrum_stereo(
    left: np.ndarray, right: np.ndarray, sample_rate: int
) -> tuple[np.ndarray, np.ndarray]:
    """立体声分帧 FFT，返回 (频率轴, 平均功率 dB)。

    每个频点取两个声道中**较大的功率**（``max(|L|², |R|²)``）：
    * 不会漏掉「只有右声道才有超声内容」的素材；
    * 不做 ``(L+R)/2`` 下混，避免反相内容互相抵消后被误读成高频被砍；
    * 也不把两声道功率相加 —— 两声道各自的本底噪声互不相关，相加会把
      噪声底抬高 3dB，改变截止判据的相对阈值。
    """
    left = np.asarray(left, dtype=np.float64)
    right = np.asarray(right, dtype=np.float64)
    if right.size != left.size:
        right = np.zeros_like(left)
    if left.size < FFT_SIZE:
        pad = FFT_SIZE - left.size
        left = np.pad(left, (0, pad))
        right = np.pad(right, (0, pad))

    hop = FFT_SIZE // 2
    window = np.hanning(FFT_SIZE)
    frames: list[np.ndarray] = []
    for start in range(0, left.size - FFT_SIZE + 1, hop):
        chunk_l = left[start:start + FFT_SIZE]
        chunk_r = right[start:start + FFT_SIZE]
        if not np.any(chunk_l) and not np.any(chunk_r):
            continue
        power = np.maximum(
            np.abs(np.fft.rfft(chunk_l * window)) ** 2,
            np.abs(np.fft.rfft(chunk_r * window)) ** 2,
        )
        frames.append(10.0 * np.log10(power + 1e-20))
    if not frames:
        return np.array([]), np.array([])
    avg = np.mean(np.stack(frames), axis=0)
    freqs = np.fft.rfftfreq(FFT_SIZE, d=1.0 / sample_rate)
    # 轻平滑，抑制单帧毛刺
    if avg.size > 9:
        kernel = np.ones(9) / 9.0
        avg = np.convolve(avg, kernel, mode="same")
    return freqs, avg


def _average_spectrum(samples: np.ndarray, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """单声道分帧 FFT（保留给测试与内部工具使用）。"""
    samples = np.asarray(samples, dtype=np.float64)
    return _average_spectrum_stereo(samples, np.zeros_like(samples), sample_rate)


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


def _wall_step(freqs: np.ndarray, db: np.ndarray, cutoff: float) -> float:
    """截止点 ±1kHz 内相邻 250Hz 分格的最大落差（dB）。

    这是「墙 vs 斜坡」判据：编码器低通是断崖，250Hz 内掉 20~40dB；
    母带/磁带的自然滚降是斜坡，同样跨度只有几 dB。截止**位置**分不开两者，
    落差可以 —— 参考 FLAC Detective 的 Gate D 经验。

    只统计下降方向，返回 0 表示没测到（例如截止贴着 Nyquist，右侧没有频点）。
    """
    if freqs.size < 8 or cutoff <= 0:
        return 0.0
    lo = max(float(freqs[0]), cutoff - WALL_SEARCH_HZ)
    hi = min(float(freqs[-1]), cutoff + WALL_SEARCH_HZ)
    mask = (freqs >= lo) & (freqs <= hi)
    seg = db[mask]
    seg_f = freqs[mask]
    if seg.size < 8:
        return 0.0
    bin_hz = float(seg_f[1] - seg_f[0])
    if bin_hz <= 0:
        return 0.0
    cell = max(2, int(round(WALL_CELL_HZ / bin_hz)))
    cells = [
        float(np.median(seg[i:i + cell]))
        for i in range(0, seg.size - cell + 1, cell)
    ]
    if len(cells) < 2:
        return 0.0
    drops = [cells[i] - cells[i + 1] for i in range(len(cells) - 1)]
    return float(max(0.0, max(drops)))


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


def _ultrasonic_decay(
    freqs: np.ndarray, db: np.ndarray, peak: float, nyquist: float
) -> tuple[float, float, float]:
    """超声区的「衰减」与「绝对能量」，用于识别假 hi-res；测不了返回 (0.0, -200.0)。

    专治「把 44.1k/48k 升到 192k 后往超声区灌噪声冒充 hi-res」。

    真 192k 母带的内容（哪怕只到 40kHz）在 40kHz 以上会随频率**快速**衰减：
    话筒/ADC 的超声噪声和磁带的超声底噪都是往下走的，80kHz 处通常已是
    数字静音（相对峰值 -120dB 以下），衰减 -50dB 起步。

    而升采样冒充 hi-res 的做法是往超声区灌宽带噪声，把自然衰减抹平 ——
    实测 Linkin Park 那批 24/192：20k=-57dB、40k=-73dB、60k=-81dB、
    80k=-78dB，从 20k 到 80k 只掉了 20dB，几乎是一条直线；个别专辑
    （The Hunting Party）甚至 80kHz 还有 -64dB。

    返回 ``(80k段 - 20k段, 80k段 - 40k段, 80k段绝对能量)``，均相对频谱峰值。
    第二项只看超声区**内部**的衰减，不受基带内容多寡影响，因此最敏感：
    实测灌噪声的 Δ(40k→80k) 只有 -2 ~ -9dB，自然衰减应在 -20dB 以下。
    """
    if nyquist < 90_000:
        return 0.0, 0.0, -200.0

    def band(lo: float, hi: float) -> float:
        mask = (freqs >= lo) & (freqs <= hi)
        if not np.any(mask):
            return -200.0
        return float(np.median(db[mask]) - peak)

    low = band(19_000.0, 22_000.0)
    mid = band(40_000.0, 50_000.0)
    high = band(78_000.0, 92_000.0)
    if low <= -200.0 or high <= -200.0:
        return 0.0, 0.0, high
    return high - low, high - mid, high


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


def _spectral_flatness(
    freqs: np.ndarray, db: np.ndarray, low: float, high: float
) -> Optional[float]:
    """某频段的频谱平坦度（几何均值 / 算术均值），0~1。

    这是「谐波相干性」的核心度量：真实乐器内容在频域是**离散的谐波峰**
    （能量集中在少数频点，平坦度→0）；而升频时填充的 dither 噪声是**平坦
    白噪声**（所有频点能量接近，平坦度→1）。

    用途：当扩展带（cutoff 之上）既不是数字静音（edge_db 不够低）、又被
    怀疑是升频时，用平坦度区分「真实超声内容」与「噪声填充」—— 这是
    :func:`_pcm_upsample_cliff`（看台阶）之外的第二道防线：有些工具把
    dither 加到与音乐同水平，台阶被填平，但填充物的"噪声本质"藏不住。

    **频点不足时返回 ``None``（无法计算），而不是 0.0。** 这一点很关键：
    0.0 的语义是「能量高度集中 = 全是谐波」，若把它当作"算不出来"的哨兵，
    就会把一条窄带（例如 22kHz 边界之上只剩 1kHz、只有 23 个频点）误读成
    「边界之上仍有谐波」，进而错误地跳过 DSD 的边界判据 —— 赵雷那批 44.1k
    转 DSD 的文件就是这样被放行的。调用方必须显式处理 ``None``。
    """
    mask = (freqs >= low) & (freqs <= high)
    if np.count_nonzero(mask) < MIN_FLATNESS_BINS:
        return None
    linear = 10.0 ** (db[mask] / 10.0)
    if linear.size == 0 or np.any(linear <= 0):
        return None
    geo = np.exp(np.mean(np.log(linear + 1e-12)))
    arith = float(np.mean(linear))
    if arith <= 0:
        return None
    return float(np.clip(geo / arith, 0.0, 1.0))


def _band_corr_detrend(
    freqs: np.ndarray, db: np.ndarray,
    lo1: float, hi1: float, lo2: float, hi2: float,
) -> float:
    """两个频段「去趋势后的对数幅度」相关系数（SBR copy-up 检测）。

    HE-AAC 的 SBR 把低频子带**精确复制**到高频（频率平移），所以高频段的
    频谱形状（包括峰谷起伏）是低频段的镜像。普通音乐的高低频虽然也有共同
    的「随频率衰减」宏观趋势，但去趋势（中值滤波）之后，各自的峰谷是独立
    的，相关性会掉下来 —— 实测真无损去趋势后约 0.2~0.36，SBR 复制仍高达
    0.97。正是这个「去趋势」把两者分开。
    """
    def detrend(x: np.ndarray, span: int) -> np.ndarray:
        # 中值滤波（约 500Hz 窗口）估计宏观包络，减去得到峰谷残余
        if span < 3 or span >= len(x):
            return x
        pad = np.pad(x, (span // 2, span // 2), mode="edge")
        windows = np.lib.stride_tricks.sliding_window_view(pad, span)
        return x - np.median(windows, axis=1)

    m1 = (freqs >= lo1) & (freqs <= hi1)
    m2 = (freqs >= lo2) & (freqs <= hi2)
    a = db[m1]
    b = db[m2]
    n = min(a.size, b.size)
    if n < 32:
        return 0.0
    bin_hz = float(freqs[1] - freqs[0]) if freqs.size > 1 else 5.4
    span = max(9, int(round(500.0 / bin_hz)))
    if span % 2 == 0:
        span += 1
    a = detrend(a[:n], span)
    b = detrend(b[:n], span)
    if float(np.std(a)) < 1e-6 or float(np.std(b)) < 1e-6:
        return 0.0
    return float(np.corrcoef(a, b)[0, 1])


def _sbr_copyup_score(freqs: np.ndarray, db: np.ndarray, nyquist: float) -> float:
    """SBR copy-up 评分：低频段与其「频率平移复制品」的最大去趋势相关性。

    扫描多个平移量（3~11kHz），取最大相关 —— HE-AAC 的 crossover 不固定，
    取最大能覆盖不同编码器。返回 0~1，越高越像 SBR 复制（高频是低频的镜像）。
    """
    lo_lo, lo_hi = 1000.0, min(6000.0, nyquist * 0.4)
    if lo_hi - lo_lo < 2000.0:
        return 0.0
    best = 0.0
    shift = 3000.0
    while shift <= 12000.0:
        if lo_hi + shift > nyquist * 0.9:
            break
        corr = _band_corr_detrend(freqs, db, lo_lo, lo_hi, lo_lo + shift, lo_hi + shift)
        best = max(best, corr)
        shift += 1000.0
    return best


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
    # 调制器噪声都不随音乐起伏。
    #
    # V1.5.2：不再把 floor 封顶到 2dB。部分 DSD 母带的调制噪声帧间波动本身
    # 就有 3~5dB（李烁《花间梦》实测 3.5~4.9dB），封顶会让阈值低于噪声自身
    # 的抖动，边界永远找不到。改用 1.25×floor + 连续 4 档确认，既能在高噪声
    # 母带上找到边界，也不会把自然衰减中的单个低点当成边界。
    above = [std for freq, std in bands if freq >= 40_000.0]
    floor = min(above) if above else min(std for _, std in bands)
    threshold = max(DSD_EDGE_VARIATION_DB, floor * 1.25)
    max_std = max(std for _, std in bands)

    # 从低往高扫：第一个「连续 4 档都 ≤ 阈值」的位置就是内容边界。
    # 用连续 4 档（约 8kHz）而不是「之上全部」，是为了容忍真 DSD 在
    # 26~32kHz 的自然滚降中偶尔回升 0.1~0.3dB。
    #
    # 还要要求内容区确实比边界区起伏更大（≥1.4 倍）：若整条频谱直到扫描上限
    # 都在起伏，floor 本身就是内容波动，阈值再高也会「处处低于阈值」，
    # 那种情况说明内容一路延伸，不该报边界。
    run = 0
    for index, (freq, std) in enumerate(bands):
        if std <= threshold:
            run += 1
            if run >= 4:
                start = bands[index - 3]
                if start[1] > 0 and max_std >= start[1] * 1.4:
                    return start[0], start[1], floor
                run = 0
        else:
            run = 0
    return 0.0, 0.0, floor


def _on_pcm_grid(edge_hz: float) -> bool:
    """信号边界是否恰好落在某个 PCM 采样率的 Nyquist 上。

    网格用 :data:`DSD_EDGE_GRID`（22.05k / 24k / 48k / 96k），**不用**
    ``PCM_NYQUISTS`` —— 后者含 32kHz，而 32k 恰是真 DSD 老录音边界的常见
    落点，把它算作 PCM 网格会让真 SACD 的边界命中率虚高、被整批判假。

    容差取 ``max(1kHz, 2%)``：边界统计按 2kHz 分档，量化误差 ±1kHz，所以
    22.0kHz 要能命中 22.05k、24.0kHz 要能命中 24k。**不能再放宽** ——
    10% 容差会把真 SACD 老录音的 26kHz 也吞进 24k 网格（实测林忆莲、梅艳芳
    《赤色风暴》等目录的网格命中率因此虚高，整批被误判成转制）。
    边界漂移（46/48/50/52kHz 其实同属 96k 源）交给
    :func:`apply_batch_consensus` 的「整批都判假就不许改真」来兜底，
    不用放大容差解决 —— 放大容差会连带把真 SACD 误判成转制。
    """
    for boundary in DSD_EDGE_GRID:
        if abs(edge_hz - boundary) <= max(1_000.0, boundary * 0.02):
            return True
    return False


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
        # 台阶必须够陡：只在 1kHz 带宽上取中位数，会把「母带在 21.5kHz
        # 附近平缓滚降」也算成台阶。再看边界处 250Hz 内的局部落差，
        # 真升采样的插值滤波器是墙（≥12dB），自然滚降不是。
        local_wall = _wall_step(freqs, db, boundary)
        if (
            step >= PCM_UPSAMPLE_STEP_DB
            and local_wall >= WALL_MIN_STEP_DB
            and step > best[1]
        ):
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


def _bit_depth_profile(
    ffmpeg: str, path: Path, start: float, seconds: float
) -> tuple[float, int]:
    """用 s24 解码测真实位深，返回 ``(最低字节为零的比例, 有效位深)``。

    为什么不用 s32 反推：不同解码器把 16/24bit 映射到 s32 的缩放方式并不统一
    （FLAC 24bit 左移 8 位、WAV 24bit 保持原值），尾随零个数会把 24bit WAV
    误读成 32bit。s24 才是 24bit 源的真实精度平面：

    * 真 24bit 内容的低 8 位大约只有 1/256 恰为零（实测 0.4%）；
    * 16bit 零填充成 24bit 时，低 8 位 **全部** 为零（比例 100%）；
    * 20bit 源低 4 位为零（有效位深 20）。

    返回比例失败时给 ``(0.0, 0)``，交给上层按「无法判断」处理。
    """
    cmd = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{seconds:.3f}",
        "-i", str(path), "-map", "0:a:0", "-ac", "2",
        "-f", "s24le", "-acodec", "pcm_s24le", "-",
    ]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=DECODE_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (subprocess.TimeoutExpired, OSError):
        return 0.0, 0
    if proc.returncode != 0 or not proc.stdout:
        return 0.0, 0
    raw = np.frombuffer(proc.stdout, dtype="<u1")
    if raw.size < 6 * 1024:
        return 0.0, 0
    triples = raw[: raw.size - raw.size % 3].reshape(-1, 3)
    left = triples[0::2]
    vals = (
        left[:, 0].astype(np.int64)
        | (left[:, 1].astype(np.int64) << 8)
        | (left[:, 2].astype(np.int64) << 16)
    )
    vals = np.where(vals >= (1 << 23), vals - (1 << 24), vals)
    vals = np.abs(vals)
    vals = vals[vals > 0]
    if vals.size < 1024:
        return 0.0, 0
    low_zero = float(np.mean((vals & 0xFF) == 0))
    tz = 24
    for value in np.unique(vals)[:4000]:
        trailing = (int(value) & -int(value)).bit_length() - 1
        if trailing < tz:
            tz = trailing
        if tz == 0:
            break
    return low_zero, max(0, 24 - tz)


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


def _tone_concentration(freqs: np.ndarray, db: np.ndarray, max_hz: float = 24_000.0) -> float:
    """基带内最强的几个频点占总能量的比例，用来识别单音测试信号。

    纯音/单音测试文件的帧间电平几乎不随时间起伏，DSD 的「信号边界」判据
    会把整段音乐误读成「内容提前结束」。这类文件只能靠噪声整形确认比特流。
    """
    mask = freqs <= max_hz
    if not np.any(mask):
        return 0.0
    power = np.power(10.0, db[mask] / 10.0)
    total = float(power.sum())
    if total <= 0:
        return 0.0
    top = np.sort(power)[-5:]
    return float(top.sum() / total)


def _frame_tone_concentration(
    rows: np.ndarray, freqs: np.ndarray, max_hz: float = 24_000.0
) -> float:
    """逐帧计算「前 5 个频点能量占比」并取中位数。

    ``_tone_concentration`` 用整段平均频谱，扫频信号的能量会摊到整条频率轴，
    平均后看起来像普通音乐（实测 sweep 只有 0.20）。但扫频在**每一帧**
    内仍是一个纯音，逐帧集中度接近 1.0。真实音乐即使是电子乐也只有
    0.80~0.84，因此用逐帧中位数可以把扫频/纯音与音乐可靠分开。
    """
    mask = freqs <= max_hz
    if rows.size == 0 or not np.any(mask):
        return 0.0
    vals: list[float] = []
    for row in rows:
        power = np.power(10.0, row[mask] / 10.0)
        total = float(power.sum())
        if total <= 0:
            continue
        vals.append(float(np.sort(power)[-5:].sum() / total))
    if not vals:
        return 0.0
    return float(np.median(vals))


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
    test_signal: float = 0.0,
    edge_above_db: float = -120.0,
    edge_above_flat: float = 1.0,
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

    # ---- 3. 单音测试信号：边界判据不适用，只确认比特流 ----
    #
    # 阈值必须很高：``_tone_concentration`` 算的是基带前 5 个频点的能量占比，
    # 而真实流行乐/人声的低频能量本来就集中 —— 实测 Taylor Swift 那批
    # 0.72~0.92、张学友真 SACD 0.55~0.72、测试碟里的器乐 0.31~0.83，全都在
    # 0.9 附近徘徊。原来取 0.9 会把普通歌曲当成"单音测试信号"直接判真
    # （实测 I Think He Knows 集中度 0.915 就被放行）。纯音/扫频信号的频谱
    # 只有一两个峰，集中度 0.98 以上，用 0.97 才能分开。
    if test_signal >= TONE_TEST_CONCENTRATION:
        reasons.append(
            f"基带能量高度集中在少数频点（集中度 {test_signal:.2f}），"
            "属于单音/测试信号；帧间波动边界判据对恒定电平信号无效"
        )
        if slope >= DSD_SLOPE_GENUINE:
            reasons.append(
                f"噪声整形斜率 {slope:+.1f}dB/oct、50kHz 以上 {ultra:.0f}dB，"
                "比特流本身是真实 DSD"
            )
            return VERDICT_DSD, 0.85, reasons
        return VERDICT_FAKE_DSD, 0.5, reasons

    # ---- 3b. 信号边界落在 PCM 网格：内容只在 PCM 带宽内存在 ----
    # 与上一条的区别：上一条要求「断崖之上是数字静音」，这一条只要求
    # 「之上没有内容了」—— 噪声抬得再高也逃不掉。
    #
    # 误伤保护 A：扫频、窄带测试信号或高频稀疏的真实录音，会让「帧间波动」
    # 在某个频率提前塌下来，但边界之上其实还有谐波内容。此时 edge 不是
    # 真实的内容边界，不能据此判假。用「边界之上的能量 + 平坦度」识别：
    # 仍有谐波（平坦度低）且能量不低 → 跳过边界判据；若是编码器噪声
    # （平坦度高）则照常判假。
    #
    # 前提：edge 必须达到 CD 带宽（21.5kHz）。低于这个位置说明内容连 CD 都
    # 不如（128k/192k 有损编码的低通就在 16~19kHz），这种情形必是有损源，
    # 没有「之上有谐波」的辩护资格 —— 而且 edge 越低、之上可测带宽越宽，
    # 越容易被误判成"有谐波"，逻辑是反的（实测张国荣某目录 11 首 edge 20~26k
    # 都判假，唯独 2 首 edge=18k 反而被判真）。
    harmonic_above = bool(
        edge_hz >= DSD_SUB_CD_EDGE_HZ
        and edge_above_db > -80.0
        and edge_above_flat < 0.5
    )
    # 误伤保护 B：边界处必须有明显电平跌落才叫「内容截止」。真 SACD 老录音
    # 的高频内容本身就平稳，穿过 22kHz 只掉几个 dB，帧间波动照样会塌陷；
    # 而 PCM 转制在源码 Nyquist 之上会掉到调制器噪声底（实测 -41~-71dB）。
    # ``base_silence == 0.0`` 表示没测到，此时不启用该保护（保守判假）。
    shallow_edge = bool(
        edge_hz >= 12_000.0
        and base_silence < 0.0
        and base_silence > -DSD_EDGE_SHALLOW_MAX_DB
    )
    edge_unreliable = harmonic_above or shallow_edge
    if harmonic_above:
        reasons.append(
            f"边界 {edge_hz / 1000:.1f}kHz 之上仍有谐波内容"
            f"（{edge_above_db:.0f}dB，平坦度 {edge_above_flat:.2f}），"
            "该边界不是真实截止，跳过边界判据"
        )
    elif shallow_edge:
        reasons.append(
            f"边界 {edge_hz / 1000:.1f}kHz 处电平只跌落 {abs(base_silence):.1f}dB"
            "（不足 20dB），高频内容其实是平缓延续而非整齐切断，"
            "该边界不是真实截止，跳过边界判据"
        )
    if edge_hz >= 12_000.0 and not edge_unreliable:
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

    if cd_boundary and (digital_silence or hard_cliff) and not edge_unreliable:
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
    wall_step: float = 0.0,
    edge_db: float = 0.0,
    edge_body: float = -120.0,
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
    extended_flat: float = 0.0,
    sbr_score: float = 0.0,
    test_signal: float = 0.0,
    edge_above_db: float = -120.0,
    edge_above_flat: float = 1.0,
    ultra_decay: float = 0.0,
    ultra_high: float = -200.0,
    ultra_slope: float = 0.0,
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
    is_dsd = (
        info.fmt in ("DSF", "DFF")
        or bool(info.dsd_rate)
        or (info.bit_depth == 1 and (info.sample_rate or 0) >= 352_800)
    )
    ratio = ratio or ((cutoff / nyquist) if nyquist else 0.0)

    if is_dsd:
        return _judge_dsd(
            slope, ultra, base_cutoff, base_steep, base_silence,
            pcm_source=pcm_source, pcm_step=pcm_step,
            pcm_above=pcm_above, ultra_body=ultra_body,
            edge_hz=edge_hz, edge_std=edge_std, edge_floor=edge_floor,
            test_signal=test_signal,
            edge_above_db=edge_above_db, edge_above_flat=edge_above_flat,
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
    #
    # 但台阶必须与检测到的截止位置重合：48k 母带在 21.2kHz 有自身低通时，
    # 22.05kHz 处也会量到台阶（1.5kHz 跨度上的滚降），若不加这条，
    # 会把真实的 48k 母带判成 44.1k 升频（V1.5.2 修正）。
    step_boundary = upsample_src / 2.0 if upsample_src > 0 else 0.0
    near_step = bool(step_boundary and abs(cutoff - step_boundary) <= 600.0)
    if nyquist >= 23000 and upsample_src > 0 and near_step:
        reasons.append(
            f"声明 {sample_rate / 1000:.1f}kHz（上限 {nyquist / 1000:.1f}kHz），"
            f"但内容在 {upsample_src / 2000:.2f}kHz 处出现台阶"
            + (f"（{upsample_step:.0f}dB）" if upsample_step else "")
            + f"，正是 {upsample_src / 2000:.2f}kHz PCM 的 Nyquist 边界"
        )
        reasons.append(
            "台阶之上是被填充的噪声而非真实内容 —— 44.1kHz 升采样后加噪声"
            "伪装成 48kHz 的典型手法"
        )
        return VERDICT_UPSAMPLED, 0.3, reasons

    # ---- 1c. 噪声填充型升频（谐波相干性兜底）：扩展带有能量，但那是噪声不是内容 ----
    #
    # 台阶判据看「台阶」，这一条看「台阶之上是什么」：真实乐器的超声内容是
    # 离散谐波峰（频谱平坦度→0），dither 填充是平坦白噪声（→1）。有些工具
    # 把 dither 加到与音乐同水平、把台阶完全填平，台阶判据就失效了；但
    # 填充物的"噪声本质"藏不住 —— 平坦度接近 1 说明那里没有谐波结构。
    # 前提是「cutoff 落在 PCM 采样率边界」：内容刚好停在 22.05k/24k 这类
    # 边界，才是升频的指纹。若 cutoff 是 8k/15k 这种"音乐本身低频"，扩展带
    # 平坦只是自然噪声底，不该判升频。
    near_boundary = any(
        abs(cutoff - b) <= max(400.0, b * 0.02)
        for b in PCM_NYQUISTS
        if b < nyquist * 0.97
    )
    if (
        nyquist >= 23000
        and near_boundary
        and extended_flat >= EXTENDED_FLATNESS_NOISE
        and edge_db > -95.0
    ):
        reasons.append(
            f"声明 {sample_rate / 1000:.1f}kHz（上限 {nyquist / 1000:.1f}kHz），"
            f"但内容在 {cutoff / 1000:.1f}kHz 结束，之上不是数字静音"
            f"（{edge_db:.0f}dB）而是接近白噪声的平坦频谱（平坦度 {extended_flat:.2f}）"
        )
        reasons.append(
            "内容停在 PCM 采样率边界、且之上是平坦噪声：真实乐器的超声内容是"
            "离散谐波峰（平坦度→0），接近 1 的平坦度说明扩展带是被填充的噪声，"
            "判定为噪声填充型升频"
        )
        return VERDICT_UPSAMPLED, 0.4, reasons

    # ---- 2. SBR copy-up：HE-AAC 的高频是低频的平移复制 ----
    #
    # 这条证据与带宽无关：SBR 让高频「看起来有内容」，但那份内容是低频复制
    # 出来的，不是录音。必须先于「带宽满值 → 真无损」判断。
    if sbr_score >= SBR_COPYUP_CORR:
        reasons.append(
            f"高频段与低频段去趋势后频谱形状高度相关（copy-up 相关性 {sbr_score:.2f}），"
            "高频是低频的平移复制"
        )
        reasons.append(
            "这是 HE-AAC SBR 的带宽复制特征：高频不是真实录音而是低频复制出来的，"
            "源码是有损编码，不可能是原生无损"
        )
        return VERDICT_SUSPECT, 0.55, reasons

    # ---- 3. 升位深：声明 24bit，但 s24 解码后最低字节全零 ----
    #
    # 16bit 素材左移 8 位装进 24bit 时，最低 8 位恒为零；真 24bit 内容只有
    # 约 1/256 的样本恰好为零（实测 0.4%）。这是纯时域事实，不依赖音乐内容。
    # 旧版用「峰值有没有越过 16bit 上限」判断，会把峰值 −12dBFS 的真 24bit
    # 录音误判成升位深 —— 现已改为低字节零比例 + 有效位深双条件。
    if depth_padding:
        reasons.append(
            f"声明 {info.bit_depth}bit，但 24bit 解码后最低字节全为零"
            f"（有效位深仅 {eff_depth}bit），是 16bit 内容零填充扩容"
        )
        reasons.append("位深扩容不会增加任何真实精度，属于规格注水")
        return VERDICT_UPSCALED, 0.6, reasons

    # ---- 2b. 内容已铺满声明带宽：不再把「接近 Nyquist」误判成截止 ----
    #
    # 22.05kHz 采样的测试文件、或母带滚降正好落在 Nyquist 附近时，截止
    # 之上本来就没有频段可测（edge 自然是 -200dB）。只要内容到达 Nyquist
    # 的 95% 以上，就不存在「被编码器砍高频」的空间，直接判真。
    if ratio >= 0.95:
        reasons.append(
            f"内容延伸到 {cutoff / 1000:.1f}kHz，占声明 Nyquist 的 {ratio:.0%}，"
            "没有采样率或位深注水"
        )
        if eff_depth and info.bit_depth and eff_depth < info.bit_depth - 2:
            reasons.append(
                f"源精度约 {eff_depth}bit（声明 {info.bit_depth}bit）：这是源本身的精度，"
                "不是零填充，仍属有效无损"
            )
        return VERDICT_TRUE, 0.85, reasons

    # ---- 3. 数字静音：截止之上没有模拟底噪，是有损转码的铁证 ----
    #
    # 陡度分不开「编码器低通」与「母带自然滚降」—— 实测 192k/320k 转码的
    # 陡度只有 16dB/kHz，与模拟母带转录（14~16dB/kHz）完全重叠。
    # 但两者的**噪声性质**不同：编码器砍掉高频后上面是数字空白（-90dB 以下），
    # 磁带/话筒/母带在截止之上总有模拟底噪（-70 ~ -85dB）。
    # 所以「带宽不足 + 截止之上是数字静音」比陡度可靠得多。
    #
    # 这条不要求「断崖」：Vorbis/WMA 的低通是缓坡，250Hz 内只掉几个 dB，
    # 若只让「墙」判据出手，它们会被下面的「平滑滚降」分支误当成老录音。
    # 决定性的仍然是「截止之上有没有模拟底噪」，与滚降快慢无关。
    #
    # 但「相对峰值」会随低频峰高低漂移（V1.5.3 回归：《今宵多珍重》峰值比主体
    # 高 31.6dB，模拟底噪算出来只有 -86.6dB，被误判成数字静音）。所以再加一条
    # 相对音乐主体的否决：真正的编码器空白要低于 -64dB，否则就是模拟底噪。
    if (
        0 < cutoff < nyquist * 0.95
        and -195.0 < edge_db < DIGITAL_SILENCE_EDGE_DB
        and edge_body < ANALOG_FLOOR_MAX_DB
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

    # ---- 4. 转码检测：先看「墙 vs 斜坡」，再看截止位置 ----
    if nyquist <= 24000:
        # CD 标准音源（44.1k / 48k）。
        #
        # 关键：cutoff 偏低不等于假无损。1980~90 年代的老录音、模拟母带转录，
        # 高频本来就不丰富，频谱会**平滑滚降**到 16~18kHz —— 这是真 CD 的
        # 正常特征，不能按「转码砍频」判假。
        #
        # 区分依据从 1.5.2 起改用「断崖落差」而不是只看陡度：
        # 编码器低通在 250Hz 内掉 20~40dB（墙），母带/磁带自然滚降只有几 dB
        # （斜坡）。旧版用 2kHz 平均陡度，会把「中等斜率的老录音」读成断崖。
        is_wall = wall_step >= WALL_MIN_STEP_DB
        smooth_rolloff = not is_wall
        wall_text = (
            f"截止处 250Hz 内落差 {wall_step:.0f}dB（断崖）"
            if is_wall
            else f"截止处 250Hz 内仅落差 {wall_step:.1f}dB（斜坡，非编码器低通）"
        )

        # 频谱空洞、sfb21 凹陷：低码率 MP3 的辅助证据，只用来加重措辞
        mp3_marks = []
        if holes >= 3.0:
            mp3_marks.append(f"4~16kHz 有 {holes:.1f} 个/帧窄带空洞")
        if sfb21 >= SFB21_DEPTH_DB:
            mp3_marks.append(f"16kHz 附近有 {sfb21:.0f}dB 的 sfb21 凹陷")

        # 只要有 MP3 专属的频谱空洞/凹陷，就不能用「平滑滚降」放行：
        # 这些是量化器「位不够」留下的痕迹，自然录音不会形成窄而深的缺口。
        if cutoff < 19500 and mp3_marks:
            reasons.append(
                f"频谱在 {cutoff / 1000:.1f}kHz 截止，且"
                + "、".join(mp3_marks)
                + "，符合有损编码器的量化特征"
            )
            return VERDICT_FAKE, 0.85, reasons

        # 绝对带宽下限：连 14kHz 都不到、带宽不足 65%，无论滚降多平滑都
        # 不可能是正常音乐母带（多为 AC-3/低码率源或有损转码后的再处理）。
        if cutoff < 14000 and ratio < 0.65:
            reasons.append(
                f"内容只到 {cutoff / 1000:.1f}kHz（带宽 {ratio:.0%}），"
                "低于正常音乐母带的带宽下限，疑似有损源"
            )
            return VERDICT_SUSPECT, 0.55, reasons

        if cutoff < 17000:
            if smooth_rolloff:
                reasons.append(
                    f"频谱在 {cutoff / 1000:.1f}kHz 平滑滚降：{wall_text}；"
                    "符合老录音/模拟母带的高频自然衰减，不是编码器低通"
                )
                return VERDICT_TRUE, 0.7, reasons
            reasons.append(
                f"频谱在 {cutoff / 1000:.1f}kHz 截止：{wall_text}，"
                "符合 128kbps 有损转码特征"
            )
            if mp3_marks:
                reasons.append("佐证：" + "、".join(mp3_marks))
            return VERDICT_FAKE, 0.9, reasons
        if cutoff < 19500:
            if smooth_rolloff:
                reasons.append(
                    f"频谱在 {cutoff / 1000:.1f}kHz 平滑滚降：{wall_text}；"
                    "符合老录音/模拟母带的高频自然衰减，非有损转码"
                )
                return VERDICT_TRUE, 0.7, reasons
            reasons.append(
                f"频谱在 {cutoff / 1000:.1f}kHz 截止：{wall_text}，"
                "符合 192kbps 有损转码特征"
            )
            if mp3_marks:
                reasons.append("佐证：" + "、".join(mp3_marks))
                return VERDICT_FAKE, 0.75, reasons
            return VERDICT_SUSPECT, 0.55, reasons
        if cutoff < 20500:
            if smooth_rolloff:
                reasons.append(
                    f"频谱在 {cutoff / 1000:.1f}kHz 平滑滚降：{wall_text}；"
                    "符合老录音高频自然衰减，非高码率转码"
                )
                return VERDICT_TRUE, 0.72, reasons
            # 320k 的断崖很浅，单看位置会与母带低通混淆；再看断崖之上的
            # 残余能量：编码器砍掉的高频是数字空白，母带低通之上仍有模拟底噪。
            if edge_db >= W320_RESIDUAL_DB:
                reasons.append(
                    f"频谱在 {cutoff / 1000:.1f}kHz 有断崖（{wall_text}），"
                    f"但截止之上仍有 {edge_db:.0f}dB 模拟底噪，更像母带/磁带低通，"
                    "不是编码器留下的数字空白"
                )
                return VERDICT_TRUE, 0.7, reasons
            reasons.append(
                f"频谱在 {cutoff / 1000:.1f}kHz 截止：{wall_text}，"
                f"截止之上仅 {edge_db:.0f}dB（数字空白），"
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
                f"源精度约 {eff_depth}bit（声明 {info.bit_depth}bit）：不是零填充，仍按真无损"
            )
        return VERDICT_TRUE, 0.85, reasons

    # ---- 假 hi-res：往超声区灌噪声冒充 192k ----
    #
    # 只在 ≥192kHz（nyquist ≥ 90kHz）可测。真母带的超声内容随频率快速衰减
    # （80k 相对 20k 掉 50dB 以上，通常直接走到数字静音）；灌噪声的则是一条
    # 近乎水平的直线（实测 20k→80k 只掉 4~33dB，80k 绝对能量 -64~-84dB）。
    #
    # 这条必须放在带宽判断之前：有些转制把噪声灌得很满，带宽能到 77~89%，
    # 会被「高频延伸到 xx，符合高解析特征」直接放行（The Hunting Party 那批）。
    if nyquist >= 90_000 and (
        ultra_decay > ULTRA_DECAY_MIN_DB
        or ultra_slope > ULTRA_SLOPE_MIN_DB
        or ultra_high > ULTRA_HIGH_ENERGY_DB
    ):
        if ultra_decay > ULTRA_DECAY_MIN_DB:
            reasons.append(
                f"采样率 {sample_rate / 1000:.1f}kHz，但 20kHz 以上能量几乎不衰减"
                f"（80kHz 处仅比 20kHz 低 {abs(ultra_decay):.0f}dB）"
            )
        elif ultra_slope > ULTRA_SLOPE_MIN_DB:
            reasons.append(
                f"采样率 {sample_rate / 1000:.1f}kHz，但 40kHz 到 80kHz 之间几乎不衰减"
                f"（仅 {abs(ultra_slope):.0f}dB）"
            )
        else:
            reasons.append(
                f"采样率 {sample_rate / 1000:.1f}kHz，但 80kHz 处仍有 {ultra_high:.0f}dB 能量"
            )
        reasons.append(
            "真母带的超声内容会随频率快速衰减，40kHz 以上通常一路走到数字静音"
            f"（-110dB 以下）；这里超声区是一条平坦的宽带噪声，内容本身只到 "
            f"{cutoff / 1000:.1f}kHz（带宽 {ratio:.0%}），属于往高频灌噪声冒充 hi-res"
        )
        return VERDICT_UPSAMPLED, 0.6, reasons

    # 高采样率（88.2kHz 及以上），且扩展带不空 —— 到这里说明带宽是真的
    # 但内容只到一半以下、超声区仍是一片低电平，仍按升频处理
    if sample_rate >= 88200 and ratio < 0.55 and edge_db < -95.0:
        reasons.append(
            f"采样率 {sample_rate / 1000:.1f}kHz，但内容只到 {cutoff / 1000:.1f}kHz"
            f"（带宽 {ratio:.0%}），超声区仅 {edge_db:.0f}dB，疑似低采样率内容升频"
        )
        return VERDICT_UPSAMPLED, 0.35, reasons
    if ratio < 0.70:
        near_pcm_edge = any(
            abs(cutoff - b) <= max(400.0, b * 0.02)
            for b in PCM_NYQUISTS
            if b < nyquist * 0.97
        )
        # 只有「扩展带是数字静音」或「截止处是断崖」才判升频。
        # 若截止之上仍有模拟底噪（edge_db 较高），且滚降平缓，那只是母带
        # 本身高频稀疏，不是把低采样率内容升上来（V1.5.2）。
        if edge_db < -95.0 or (near_pcm_edge and wall_step >= WALL_MIN_STEP_DB):
            reasons.append(
                f"采样率 {sample_rate / 1000:.1f}kHz，但频谱只到 {cutoff / 1000:.1f}kHz"
                f"（带宽利用率 {ratio:.0%}），扩展带 {edge_db:.0f}dB，疑似低采样率内容升频"
            )
            return VERDICT_UPSAMPLED, 0.35, reasons
        reasons.append(
            f"采样率 {sample_rate / 1000:.1f}kHz，内容到 {cutoff / 1000:.1f}kHz"
            f"（带宽 {ratio:.0%}）；高频稀疏是母带自然滚降，扩展带仍有 {edge_db:.0f}dB "
            "模拟底噪，不是插值空白"
        )
        return VERDICT_TRUE, 0.7, reasons
    reasons.append(
        f"高频延伸到 {cutoff / 1000:.1f}kHz（带宽 {ratio:.0%}、扩展带 {edge_db:.0f}dB），"
        "符合高解析音源特征"
    )
    return VERDICT_TRUE, 0.8, reasons


# ---------------------------------------------------------------- 对外入口


def pcm_spec_issues(info: AudioInfo) -> list[str]:
    """返回低于收藏最低规格的原因；空列表表示达标。

    只看声明规格，未知字段跳过（不做猜测）。DSD 不走这条规则。
    """
    issues: list[str] = []
    sample_rate = info.sample_rate or 0
    bits = info.bit_depth or 0
    channels = info.channels or 0
    if sample_rate and sample_rate < MIN_PCM_SAMPLE_RATE:
        issues.append(f"采样率 {sample_rate / 1000:g}kHz")
    if bits and bits < MIN_PCM_BIT_DEPTH:
        issues.append(f"位深 {bits}bit")
    if channels and channels < MIN_PCM_CHANNELS:
        issues.append("单声道" if channels == 1 else f"{channels} 声道")
    return issues


def analyze(
    path: Path | str,
    info: Optional[AudioInfo] = None,
    *,
    positions: tuple[float, ...] = SAMPLE_POSITIONS,
    segment_seconds: Optional[float] = None,
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
    # SACD ISO 是光盘镜像：ffmpeg 无法解码，先分轨成 DSF/DFF 再鉴定
    if path.suffix.lower() == ".iso":
        result.error = "SACD ISO 为光盘镜像，需先分轨为 DSF/DFF 后鉴定"
        return result

    if info is None:
        from .probe import probe

        info = probe(path, with_cue=False).info
    result.bit_depth_declared = info.bit_depth or 0
    sample_rate = info.sample_rate or 0
    result.nyquist_hz = sample_rate // 2

    # 有损格式（MP3/AAC/Vorbis/µ-law/AIFF-ADPCM…）没有「真假无损」可谈：
    # 它们本身就是有损的，再判「真无损」会给出完全错误的结论。
    if not info.lossless:
        result.error = f"有损格式（{info.fmt or '未知'}），不做无损鉴定"
        return result

    is_dsd = (
        info.fmt in ("DSF", "DFF")
        or bool(info.dsd_rate)
        or (info.bit_depth == 1 and (info.sample_rate or 0) >= 352_800)
    )
    # 收藏最低规格（V1.5）：PCM 低于 16bit / 44.1kHz / 立体声 直接判假无损。
    # 放在解码之前，既能省掉无意义的频谱分析，也避免低采样率文件的
    # 11~16kHz 内容被有损判据误读。DSD 有自己的判据，不参与这条。
    if not is_dsd:
        spec_issues = pcm_spec_issues(info)
        if spec_issues:
            result.spec_low = True
            result.verdict = VERDICT_FAKE
            result.score = 0.0
            result.bit_depth_effective = info.bit_depth or 0
            result.reasons = [
                "低于最低规格（要求 16bit / 44.1kHz / 立体声）："
                + "、".join(spec_issues)
            ]
            return result

    duration = info.duration or _probe_duration(ffmpeg, path)
    if not duration or duration < 2:
        result.error = "时长过短，无法分析"
        return result

    # 每段时长：PCM 4s（共 16s），DSD 3s（共 12s）。调用方仍可显式指定。
    segment = float(segment_seconds) if segment_seconds else (
        DSD_SEGMENT_SECONDS if is_dsd else SEGMENT_SECONDS
    )

    spectra: list[tuple[np.ndarray, np.ndarray]] = []
    deepest_depth = 0
    analyzed = 0.0
    above_16 = False
    peak_dbfs = -200.0
    # DSD 的「信号边界」判据要保留整段样本做分帧统计；V1.5.2 改为累积所有
    # 取样段的帧（原先只取最响的一段，长段/短段的边界会在 26~28kHz 之间跳）
    edge_rows: list[np.ndarray] = []
    edge_freqs: Optional[np.ndarray] = None
    sr_hint = 0 if is_dsd else int(info.sample_rate or 0)
    fail_count = 0
    first_start: Optional[float] = None
    for position in positions:
        start = max(1.0, duration * position - segment / 2)
        if start + segment > duration:
            start = max(0.0, duration - segment)
        decoded = _decode_segment(
            ffmpeg, path, start, segment, sample_rate_hint=sr_hint
        )
        if decoded is None:
            fail_count += 1
            continue
        if first_start is None:
            first_start = start
        left, right, effective_sr = decoded
        # 取电平更高的声道做位深/边界统计：另一声道若因录音设置而很轻，
        # 合并统计会被它的量化噪声拉低有效位深。
        samples = left
        if right.size and float(np.max(np.abs(right))) > float(np.max(np.abs(left))):
            samples = right
        analyzed += segment
        if effective_sr:
            sample_rate = effective_sr
            result.nyquist_hz = sample_rate // 2
        freqs, db = _average_spectrum_stereo(left, right, sample_rate)
        if freqs.size:
            spectra.append((freqs, db))
        depth = _effective_bit_depth(samples)
        deepest_depth = max(deepest_depth, depth)
        seg_above16, seg_dbfs = _bit_depth_evidence(samples)
        above_16 = above_16 or seg_above16
        peak_dbfs = max(peak_dbfs, seg_dbfs)
        if is_dsd:
            rows, row_freqs = _frame_spectra(samples, sample_rate)
            if rows.size:
                # 每段先减去自己的基带参考再拼接，避免不同段落的整体电平差
                # 被当成「内容波动」抬高噪声基准
                ref = (row_freqs >= 300) & (row_freqs <= 3000)
                if np.any(ref):
                    rows = rows - float(np.median(np.median(rows[:, ref], axis=1)))
                edge_rows.append(rows)
                edge_freqs = row_freqs

    if not spectra:
        if info.fmt == "WMA Lossless":
            result.error = "当前 ffmpeg 不支持 WMA Lossless 解码，无法做频谱鉴定（容器本身是无损）"
        elif fail_count:
            result.error = "解码不完整（文件可能被截断或损坏）"
        else:
            result.error = "解码失败：ffmpeg 无法解析该文件"
        return result
    # 4 个取样点里有 2 个以上解不出来（通常是 1MB 截断样本），
    # 频谱只覆盖了前半段，据此判真假会把「文件不完整」误判成「转码」。
    if fail_count >= 2:
        result.error = "解码不完整（文件可能被截断或损坏）"
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
    wall_step = 0.0
    if not is_dsd and result.nyquist_hz:
        wall_step = _wall_step(freqs, db, cutoff)
    result.wall_step_db = wall_step
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
    edge_above_db = -120.0
    edge_above_flat = 1.0
    frame_tone = 0.0
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
        # 信号边界：内容消失在哪个频率（不看电平，只看波动）。
        # 用全部取样段拼接后的帧，短段/长段不会再让边界在 26~28kHz 之间跳。
        if edge_rows and edge_freqs is not None:
            rows = np.vstack(edge_rows)
            row_freqs = edge_freqs
            edge_hz, edge_std, edge_floor = _variation_edge(rows, row_freqs, nyq)
            # 边界之上的基带内容：判断这个「边界」是不是真的内容截止。
            # 起点用 +500Hz 而不是 +1kHz：44.1k 源转 DSD 的边界在 22kHz，
            # 到 24kHz 只剩 1kHz，在 176.4kHz/8192 点 FFT 下只有约 23 个频点，
            # 平坦度算不出来（旧版返回 0.0，被误读成「之上仍有谐波」，于是
            # 整批 44.1k 转制的文件都被跳过边界判据放行）。
            low = min(edge_hz + 500.0, 24_000.0)
            high = min(24_000.0, nyq)
            if low < high:
                edge_above_db = _band_energy(freqs, db, low, high)
                flat = _spectral_flatness(freqs, db, low, high)
                # 算不出来就不构成「之上有谐波」的证据，退回 1.0（不触发保护）
                edge_above_flat = 1.0 if flat is None else flat
            # 逐帧集中度：扫频每帧都是纯音，平均频谱却看不出来
            frame_tone = _frame_tone_concentration(rows, row_freqs)
        result.noise_shaping_slope = slope
        result.ultrasonic_ratio = ultra
        result.baseband_cutoff = int(base_cutoff)
        result.baseband_silence = base_silence
        result.signal_edge_hz = int(edge_hz)
        result.signal_edge_std = edge_std
        result.pcm_cliff_source = int(pcm_source)
    else:
        high_band = _band_energy(freqs, db, 20000, min(result.nyquist_hz or 22050, 40000))
    result.cutoff_hz = int(cutoff)
    result.steepness_db = steepness
    result.steepness_oct = steep_oct
    result.high_band_energy_db = high_band
    # 有效位深改用 s24 专属测量：s32 尾随零受解码器缩放方式影响，
    # 会把 24bit WAV 读成 32bit，也会把 16bit 内容读成 32bit。
    declared_depth = int(info.bit_depth or 0)
    pad_ratio = 0.0
    eff_depth = 0
    if not is_dsd:
        if 20 <= declared_depth <= 24 and first_start is not None:
            pad_ratio, prof_depth = _bit_depth_profile(
                ffmpeg, path, first_start, segment
            )
            eff_depth = prof_depth or declared_depth
        elif declared_depth:
            eff_depth = min(declared_depth, deepest_depth or declared_depth)
    result.bit_depth_effective = eff_depth or deepest_depth
    result.analyzed_seconds = analyzed

    # ---- 三维度证据：峰值 / 扩展带 / 带宽比例 / 空洞 / 位深 ----
    peak = float(np.max(db))
    nyq = float(result.nyquist_hz or 0)
    bandwidth = (cutoff / nyq) if nyq else 0.0
    edge_db = _edge_energy(freqs, db, cutoff, nyq, peak) if nyq else 0.0
    # 截止之上的能量，换成相对音乐主体（300Hz~3kHz）的参考系。
    # 「相对峰值」会随低频峰高低漂移，单靠它会误伤峰值突出的老录音。
    edge_body = -120.0
    if nyq and 0 < cutoff < nyq * 0.95:
        edge_body = _band_energy(
            freqs, db, cutoff + UPSAMPLE_EDGE_OFFSET_HZ, nyq * 0.98
        )
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
    # 扩展带平坦度：区分「真实超声内容」与「噪声填充型升频」。
    # 真实乐器内容在频域是离散谐波峰（平坦度→0），dither 填充是平坦噪声（→1）。
    # 只对声明 hi-res、且扩展带确实有能量（cutoff 明显低于 Nyquist）时才有意义。
    extended_flat = 0.0
    if not is_dsd and nyq >= 23000 and 0 < cutoff < nyq * 0.95:
        flat = _spectral_flatness(freqs, db, cutoff, nyq)
        # 频点不足算不出来时保持 0.0：不能凭"算不出来"就判噪声填充
        extended_flat = 0.0 if flat is None else flat
    # SBR copy-up 相关性：HE-AAC 的高频是低频的平移复制（去趋势后仍高度相关）
    sbr_score = 0.0
    if not is_dsd and nyq >= 22050:
        sbr_score = _sbr_copyup_score(freqs, db, nyq)
    # 超声衰减：高采样率 PCM 的「假 hi-res」判据（往 40kHz 以上灌噪声）
    ultra_decay = 0.0
    ultra_slope = 0.0
    ultra_high = -200.0
    if not is_dsd and nyq >= 90_000:
        ultra_decay, ultra_slope, ultra_high = _ultrasonic_decay(freqs, db, peak, nyq)
    # 单音测试信号：基带能量集中在极少数频点，DSD 的帧间波动边界判据不适用
    test_signal = _tone_concentration(freqs, db) if is_dsd else 0.0
    test_signal = max(test_signal, frame_tone)
    # 位深注水：声明 24bit，但 s24 解码后低 8 位全部为零 —— 说明源是 16bit
    # 左移得到的。这是纯时域事实，比「峰值有没有越过 16bit 上限」可靠：
    # 很多真 24bit 录音的峰值本来就在 -12dBFS，碰不到 16bit 满刻度。
    depth_padding = bool(
        declared_depth >= 24 and pad_ratio >= 0.9 and 0 < eff_depth <= 17
    )
    result.peak_db = peak
    result.edge_energy_db = edge_db
    result.edge_body_db = edge_body
    result.bandwidth_ratio = bandwidth
    result.spectral_holes = holes
    result.sfb21_notch_db = notch
    result.bit_above_16 = above_16
    result.bit_depth_padding = depth_padding
    result.spectral_roughness = roughness
    result.extended_flatness = extended_flat
    result.sbr_copyup_score = sbr_score
    result.tone_concentration = test_signal

    verdict, score, reasons = _judge(
        info,
        cutoff,
        steepness,
        high_band,
        eff_depth or deepest_depth,
        slope=slope,
        ultra=ultra,
        base_cutoff=base_cutoff,
        base_steep=base_steep,
        base_silence=base_silence,
        steep_oct=steep_oct,
        wall_step=wall_step,
        edge_db=edge_db,
        edge_body=edge_body,
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
        test_signal=test_signal,
        edge_above_db=edge_above_db,
        edge_above_flat=edge_above_flat,
        upsample_src=upsample_src,
        upsample_step=upsample_step,
        extended_flat=extended_flat,
        sbr_score=sbr_score,
        ultra_decay=ultra_decay,
        ultra_high=ultra_high,
        ultra_slope=ultra_slope,
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
    is_dsd = (
        info.fmt in ("DSF", "DFF")
        or bool(info.dsd_rate)
        or (info.bit_depth == 1 and (info.sample_rate or 0) >= 352_800)
    )
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

#: 同批推定：调制器指纹（噪声整形斜率 dB/oct）允许的偏差。
#: 实测同一批转换内部标准差 ≤0.2（赵雷 18 首全在 26.79±0.09），
#: 不同转换器之间相差 3~20（全库 6.5~43.5），0.8 是非常宽松的界限。
BATCH_SLOPE_TOLERANCE = 0.8
#: 同批推定：信号边界（edge）的"众数集中度"阈值。
#: 真 PCM 转制的源码采样率一致，edge 高度集中在 22k/24k（实测王菲 15/16、
#: 林子祥 15/20 首 edge 都在 22k）；真 SACD 老录音每首歌高频内容不同，
#: edge 自然分散（实测梅艳芳 16~32k、李宗盛 20~36k 各档都有）。
#: 众数占比 ≥ 0.65 → PCM 转制；≤ 0.45 → 真 SACD；中间保持原判。
BATCH_EDGE_MAJORITY = 0.60
BATCH_EDGE_SPREAD = 0.45
#: 「假改真」允许的最高判假比例。老录音做成 SACD 时只有少数几首会被边界
#: 判据误伤，整批判真的仍是多数；一旦过半都是假，就说明这批本身就是转制
#: （实测 Taylor Swift 5/5 全判假，被「边界分散」逻辑整批放行）。
BATCH_REVERSE_MAX_FAKE = 0.5


def apply_batch_consensus(
    items: list[tuple[Path | str, AuthenticityResult]],
) -> list[tuple[Path, AuthenticityResult]]:
    """同目录一致性推定：一批文件出处相同，不会真假混杂。

    现实中「同一文件夹里一首假、其余真」几乎不会发生 —— 同一批资源的来源
    只有一次。但信号边界判据对「老模拟录音转 SACD」会系统性误判：老录音的
    高频本就稀疏，每首歌的边界随内容自然滚降（16~36kHz 分散）；而 PCM 转制
    的边界会**成批落在 PCM 采样率的 Nyquist 上**（22.05k / 24k / 32k…）。

    所以判据不是看「当前判定里假占多少」（那会继承误判），而是看**调制器
    指纹 + 边界是否落在 PCM 网格**这两个物理信号：

    * **斜率一致 + 落在 PCM 网格的边界 ≥65%** → 源码带宽统一，是 PCM 转制，
      把漏判的「真 DSD」改判假。注意统计的是**网格命中率**而不是某个精确
      边界值的占比 —— 同一批里 44.1k 源（22kHz）和 48k 源（24kHz）混装很
      常见，按精确值分组会把证据拆散。
    * **斜率一致 + 落在 PCM 网格的边界 ≤45%** → 每首歌高频不同，是真 SACD
      老录音的自然滚降，把「边界偏低被误判」的假改判真（只改无 PCM 断崖强
      证据的弱证据假）。

    只在 DSD 上生效：PCM 侧的「同目录」可能真的是不同来源拼的合辑。

    返回被改判的条目，供界面刷新。
    """
    from collections import Counter

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

        # 调制器指纹聚类：同一批转制的斜率一致，取中位数做参考。
        # 容差自适应：新录音斜率测量极稳（赵雷 ±0.09），老录音超声区能量
        # 低、斜率测量波动大（实测梅艳芳 ±1.5dB），固定 0.8 会把整批拆散。
        slopes = [r.noise_shaping_slope for _, r in dsd if r.noise_shaping_slope > 0]
        if not slopes:
            continue
        reference = float(np.median(slopes))
        mad = float(np.median([abs(s - reference) for s in slopes]))
        tolerance = max(BATCH_SLOPE_TOLERANCE, 2.5 * mad)
        core = [
            (p, r) for p, r in dsd
            if abs(r.noise_shaping_slope - reference) <= tolerance
        ]
        if len(core) < 3:
            continue

        # 边界集中度：统计落在 **PCM Nyquist 网格**上的比例，而不是某个
        # 精确边界值的占比。
        #
        # 为什么：同一批转制的源码采样率不一定统一 —— 44.1k 源落在 22kHz、
        # 48k 源落在 24kHz，都可能混在一批里。按精确值统计会把 22kHz 和
        # 24kHz 拆成两组（各 30~40%），落进下面的「分散 → 假改真」分支，
        # 把本该判假的整批放行（赵雷 18 首就是这样被误判成真的）。
        # 两者都落在 PCM 网格上，本质是同一个证据，必须合并计数。
        edge_counts: dict[int, int] = {}
        grid_hits = 0
        for _, r in core:
            if r.signal_edge_hz > 0:
                edge = int(r.signal_edge_hz)
                edge_counts[edge] = edge_counts.get(edge, 0) + 1
                # 落在 PCM 网格上，或连 CD 带宽都不到（<21.5kHz），
                # 都说明源码带宽被限制过。后者单独看是「有损转 DSD」，
                # 同样应计入整批统一的证据（李烁《花间梦》11 首里
                # 3 首边界 18~20kHz，若只数网格命中会低于 60% 门槛）。
                if _on_pcm_grid(r.signal_edge_hz) or (
                    r.signal_edge_hz < DSD_SUB_CD_EDGE_HZ
                ):
                    grid_hits += 1
        if not edge_counts:
            continue
        majority_ratio = grid_hits / len(core)
        majority_edge = max(edge_counts, key=edge_counts.get)

        if majority_ratio >= BATCH_EDGE_MAJORITY:
            # 边界高度集中 → 源码采样率统一 → PCM 转制，整批判假
            for path, result in core:
                if result.verdict != VERDICT_DSD:
                    continue
                result.verdict = VERDICT_FAKE_DSD
                result.score = 0.3
                result.reasons = [
                    f"同目录 {len(core)} 首调制器指纹一致（斜率 {reference:.1f}±{BATCH_SLOPE_TOLERANCE}），"
                    f"且 {majority_ratio:.0%} 的曲目信号边界落在 PCM 采样率 Nyquist 上"
                    f"（最集中的是 {majority_edge / 1000:.0f}kHz），符合 PCM 源码带宽统一"
                    "被调制器抬成 DSD 的特征",
                    "同一批资源出处只有一次：真 SACD 每首歌高频不同，边界不会成批落在 PCM 网格上",
                ] + result.reasons[:1]
                changed.append((path, result))
        elif majority_ratio <= BATCH_EDGE_SPREAD:
            # 边界分散 → 每首歌高频不同 → 真 SACD 老录音，弱证据的假改真。
            #
            # 但前提是「少数被误伤」：老录音做成 SACD 时，只有高频特别稀疏的
            # 那几首会被边界判据误判，整批里判真的仍是多数。若这一批里过半
            # 都判假，那不是误伤，而是这批本身就是转制 —— 实测 Taylor Swift
            # 那批 DSD128（96k 源）5/5 全判假，边界又漂在 46/48/50/52kHz
            # （精确值众数只有 20%），按旧的「分散 → 假改真」逻辑被整批放行。
            fake_ratio = sum(
                1 for _, r in core if r.verdict == VERDICT_FAKE_DSD
            ) / len(core)
            if fake_ratio > BATCH_REVERSE_MAX_FAKE:
                continue
            for path, result in core:
                if result.verdict != VERDICT_FAKE_DSD or result.pcm_cliff_source > 0:
                    continue
                result.verdict = VERDICT_DSD
                result.score = 0.8
                result.reasons = [
                    f"同目录 {len(core)} 首调制器指纹一致（斜率 {reference:.1f}），且信号边界随曲目分散"
                    f"（本首 {result.signal_edge_hz / 1000:.1f}kHz），是内容高频自然滚降而非统一转制",
                    "老模拟录音做成 SACD 时高频本就稀疏，信号边界偏低不是 PCM 转制的证据",
                ] + result.reasons[:1]
                changed.append((path, result))
        # 中间（45%~65%）保持原判，避免两头误伤
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
