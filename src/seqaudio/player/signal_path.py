"""信号路径：把「这一路音频到底被改了什么」如实摊开成 9 级。

为什么要有这个东西
------------------
播放器的可信度承诺是「你可以自己看」。用户不需要信任一句营销话术，只需要
看到一条逐级的账：源文件是什么格式 → 用什么解码器 → 出来的样本格式 →
音量/EQ 有没有动 → 有没有重采样 → 交到哪个后端、哪个设备、设备上报什么格式。
每一级只有四种状态，任何一级「我们主动改了」或「设备不让我们如愿」都必须
显式标出来。

措辞铁律（需求文档 R2）
-----------------------
全部直通时只能说「**未做软件改动**」。绝不允许出现「bit-perfect 已验证」
「逐样本一致」这类**可被证伪的承诺** —— 我们既没有回环录音比对，也无法
控制操作系统的混音器。``FORBIDDEN_CLAIMS`` 里的词由 ``assert_wording()``
与单元测试双重看住。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from .constants import STAGE_I18N_KEYS, STAGE_KEYS, StageStatus
from .dsd import dsd_label_for, dop_label_for, format_rate_khz

log = logging.getLogger(__name__)

#: 全直通时的总述（措辞铁律 R2，只能这么写）
TEXT_NO_SOFTWARE_CHANGE = "未做软件改动"
#: 采样率回退时的说明模板
TEXT_FALLBACK_RATE = "已重采样 {src}→{dst}（设备不支持源采样率）"
#: L1：绕开系统混音器（两个独立读数互相印证：设备默认率 vs 本流率）
TEXT_L1 = "已绕开系统混音器（系统混音 {mix} Hz · 本流 {rate} Hz）"
#: L2：本进程未重采样/未改率/未加增益
TEXT_L2 = "本进程未重采样、未改采样率、未加软件增益（{container} 位容器 / 有效位深 {depth} 位）"
#: L3（**永久禁止**）：只有回环录音比对才能说的那几句
TEXT_L3_FORBIDDEN = "（该类表述已禁用：本进程无法证明设备侧的位一致性）"
#: output 级快照
TEXT_OUTPUT_STRICT = "WASAPI · 独占（严格）"
TEXT_OUTPUT_LOOSE = "WASAPI · 独占"
TEXT_OUTPUT_SHARED = "WASAPI · 共享（系统混音器）"
TEXT_OUTPUT_REFUSED = "WASAPI · 共享（该设备不支持独占）"
TEXT_OUTPUT_ASIO_MISSING = "ASIO · 不可用（当前 PortAudio 构建不含 ASIO）"
#: ASIO 真的开流成功时的 output 级文案（PRD P0-3③ 定稿措辞）。
#: ASIO 驱动天然独占，不再分「宽松/严格」两档。
TEXT_OUTPUT_ASIO = "ASIO · 驱动独占"
#: device 级的延迟项。**非** ASIO 后端保持 v1.8 的写法不变（既有断言依赖它）。
TEXT_LATENCY = "延迟 {ms:.0f} ms"
#: ASIO：``stream.latency`` 只是 PortAudio 的驱动缓冲周期，**不是**端到端延迟。
#: 把它叫「延迟」会让人以为整链路只有 40 ms，所以按后端分叉、改称「驱动缓冲」。
TEXT_ASIO_BUFFER_MEASURED = "驱动缓冲 {ms:.0f} ms（实测）"
#: 驱动没上报 ``stream.latency`` 时用的是估算值，必须写明是估算。
TEXT_ASIO_BUFFER_ESTIMATED = "驱动缓冲 {ms:.0f} ms（估算，驱动未上报）"
#: 整数通路被 DSP 挤掉时的说明（面板必须说清楚为什么「位完美」没生效）
TEXT_INT_PIPELINE_UNAVAILABLE = "整数通路不可用（DSP 已启用，改用浮点）"

# ---- DoP（v1.8.1 P1-1）----
#: DoP 直出：``DSD64 → DoP64 @176.4 kHz``。只陈述「怎么装的」，不承诺音质
TEXT_DOP_DECODE = "{dsd} → {dop} @{rate}"
#: 回退 PCM：``DSD256 → PCM 176.4 kHz（设备不支持 705.6 kHz 载体）``
TEXT_DOP_FALLBACK = "{dsd} → PCM {rate}（{note}）"
#: DSD 转 PCM（没有 DoP 意图时的老写法）
TEXT_DSD_TO_PCM = "{dsd} → PCM {rate}"
#: DoP 期间音量/EQ/淡变被旁路的**显式**说明（不能静默）
TEXT_DOP_GAIN_BYPASSED = "0 dB 旁路（DoP 期间软件音量不可用，音量由驱动控制）"
TEXT_DOP_EQ_BYPASSED = "旁路（DoP 期间强制关闭）"
TEXT_DOP_FADE_BYPASSED = "无（DoP 不支持淡变/交叉淡变）"
TEXT_DOP_RESAMPLE = "未重采样 · 声道直通（DoP 载体 = DSD 位率 ÷ 16）"
#: 绝不允许出现的说法
FORBIDDEN_CLAIMS = ("bit-perfect", "bit perfect", "位完美", "逐样本一致", "比特完美")

#: 后端键 -> 界面显示名
BACKEND_LABELS = {
    "wasapi": "WASAPI",
    "mme": "MME",
    "directsound": "DirectSound",
    "wdmks": "WDM-KS",
    # ASIO 没有「共享/独占」两档：驱动本身就是独占。这里给的是兜底显示名，
    # 真正开流成功时走下面的 ``TEXT_OUTPUT_ASIO`` 分支。
    "asio": TEXT_OUTPUT_ASIO,
}

#: 各级的中文标签（界面切语言时用 STAGE_I18N_KEYS 覆盖，此处只做日志/兜底）
STAGE_LABELS = {
    "source": "源文件",
    "decode": "解码器",
    "format": "样本格式",
    "gain": "音量",
    "eq": "均衡器",
    "fade": "淡变/无缝",
    "resample": "重采样/声道",
    "output": "输出后端",
    "device": "设备上报格式",
}


def assert_wording(text: str) -> str:
    """把禁用的措辞降级为合规说法（防某处手滑写死）。

    除了逐条黑名单，还做两条**启发式**拦截（设计 §7.3）：同一句里同时出现
    「位」与「完美」（各种拼法/空格变体），以及「24bit 直通」这类表述 ——
    它们都指向同一个无法证明的承诺。宁可错杀，不可放过：这种承诺一旦出现在
    界面上就是可被证伪的营销话术。
    """
    value = str(text or "")
    lowered = value.lower()
    for banned in FORBIDDEN_CLAIMS:
        if banned.lower() in lowered:
            log.warning("信号路径文案含禁用措辞 %r，已替换：%s", banned, value)
            return TEXT_NO_SOFTWARE_CHANGE
    compact = lowered.replace(" ", "")
    if ("位" in compact and "完美" in compact) or ("bit" in compact and "perfect" in compact):
        log.warning("信号路径文案触发「位完美」启发式拦截：%s", value)
        return TEXT_NO_SOFTWARE_CHANGE
    if "直通dac" in compact or "直通 dac" in lowered:
        log.warning("信号路径文案声称「直通 DAC」：%s", value)
        return TEXT_NO_SOFTWARE_CHANGE
    return value


def latency_text(backend_key: str, latency_ms: float, latency_source: str = "") -> str:
    """device 级「延迟」项的文案（按后端分叉，空串表示不写这项）。

    ``stream.latency`` 的语义随后端不同：WASAPI/MME/DirectSound 下它至少还沾点
    「这条路多长」的意思；ASIO 下它就是 **驱动自己的缓冲周期**（而且实测
    PortAudio 会把过大的 ``latency`` 请求值原样回显），跟端到端延迟不是一个量。
    所以 ASIO 改称「驱动缓冲」并写明实测/估算，其它后端保持 v1.8 的原文案 ——
    那些措辞已经被既有断言锁住，不该为了统一而漂移。

    Args:
        backend_key: 小写后端键（``"asio"`` / ``"wasapi"`` / …）。
        latency_ms: 输出层给出的毫秒数；``<= 0`` 视为「没有数据」。
        latency_source: ``"measured"`` / ``"estimated"``；空串按实测处理。

    Returns:
        可直接拼进 device 级的短句；无数据时返回空串。
    """
    try:
        value = float(latency_ms or 0.0)
    except (TypeError, ValueError):
        return ""
    if not value > 0:
        return ""
    text = (
        TEXT_ASIO_BUFFER_ESTIMATED
        if str(latency_source or "").strip().lower() == "estimated"
        else TEXT_ASIO_BUFFER_MEASURED
    )
    if str(backend_key or "").strip().lower() != "asio":
        return TEXT_LATENCY.format(ms=value)
    return text.format(ms=value)


@dataclass
class SignalStage:
    """一级信号。``detail`` 是给人看的短句，尽量只含事实与数字。"""

    key: str
    label: str = ""
    detail: str = ""
    status: StageStatus = StageStatus.PASSTHROUGH

    def __post_init__(self) -> None:
        if not self.label:
            self.label = STAGE_LABELS.get(self.key, self.key)
        if not isinstance(self.status, StageStatus):
            try:
                self.status = StageStatus(str(self.status))
            except ValueError:
                self.status = StageStatus.PASSTHROUGH
        self.detail = assert_wording(self.detail)

    @property
    def i18n_key(self) -> str:
        return STAGE_I18N_KEYS.get(self.key, f"sig_stage_{self.key}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "i18n_key": self.i18n_key,
            "detail": self.detail,
            "status": self.status.value,
            "status_i18n_key": self.status.i18n_key,
        }


@dataclass
class Counters:
    """信号路径上的量化计数器（面板底部一行小字）。"""

    underruns: int = 0
    dropped_frames: int = 0
    resample_events: int = 0
    decode_errors: int = 0
    gain_db: float = 0.0
    last_gap_ms: float = 0.0
    #: 「探测说行、开流被拒」的次数（设计 §4.2：连续 2 次触发缓存作废）
    probe_mismatch: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "underruns": int(self.underruns),
            "dropped_frames": int(self.dropped_frames),
            "resample_events": int(self.resample_events),
            "decode_errors": int(self.decode_errors),
            "gain_db": round(float(self.gain_db), 2),
            "last_gap_ms": round(float(self.last_gap_ms), 2),
            "probe_mismatch": int(self.probe_mismatch),
        }


class SignalPath:
    """9 级描述 + 计数器。

    生命周期：引擎每次「开流成功」都会 ``configure(...)`` 重建一次（保证描述
    与真实链路一致），运行期只会 ``mark(...)`` 局部修正（例如中途发生欠载）。
    """

    def __init__(
        self,
        stages: Optional[Sequence[SignalStage]] = None,
        counters: Optional[Counters] = None,
    ) -> None:
        self.stages: list[SignalStage] = list(stages or [])
        self.counters: Counters = counters or Counters()
        self._context: dict[str, Any] = {}
        if not self.stages:
            self.stages = [SignalStage(key=key) for key in STAGE_KEYS]

    # ------------------------------------------------------------ 构造

    @classmethod
    def blank(cls) -> "SignalPath":
        """全 PASSTHROUGH 的空路径（无曲目时的占位）。"""
        return cls([SignalStage(key=key, detail="—") for key in STAGE_KEYS])

    def configure(
        self,
        *,
        source: str = "",
        codec: str = "",
        decoder_version: str = "",
        sample_fmt: str = "",
        channels: int = 0,
        sample_rate: int = 0,
        is_dsd: bool = False,
        gain_db: float = 0.0,
        gain_active: bool = False,
        eq_enabled: bool = False,
        eq_active: bool = False,
        eq_bands: int = 0,
        ramp_ms: int = 0,
        crossfade_sec: float = 0.0,
        gapless: bool = False,
        rate_want: int = 0,
        rate_actual: int = 0,
        downmix_from: int = 0,
        backend: str = "",
        device: str = "",
        exclusive: bool = False,
        latency_ms: float = 0.0,
        latency_source: str = "",
        device_dtype: str = "",
        int_pipeline: bool = False,
        format_note: str = "",
        effective_bit_depth: int = 0,
        exclusive_actual: bool = False,
        mode_label: str = "",
        system_mix_rate: int = 0,
        mode_note: str = "",
        exclusive_refused: bool = False,
        exclusive_fallback: bool = False,
        dsd_rate: int = 0,
        dop: bool = False,
        dop_rate: int = 0,
        dop_note: str = "",
    ) -> "SignalPath":
        """按真实链路重建 9 级描述（引擎开流成功后调用一次）。

        DoP 相关参数（v1.8.1）：

        * ``dsd_rate``：源 DSD 位率（2822400 / 5644800 …），用来写「DSD64」；
          刻意**不**从载体率反推 —— 反推会把 DSD128 写成 DoP64。
        * ``dop`` + ``dop_rate``：真的在走 DoP 直出。
        * ``dop_note``：**回退原因**。非空即表示「本来要 DoP、设备不给」，
          decode 级会被标成 ``FALLBACK`` 并把原因写进同一行 —— 不许静默降级。
        * ``latency_source``：``"measured"`` / ``"estimated"``（由输出层的
          ``AudioOutput.latency_source`` 给）。只在 ASIO 下改变措辞：驱动没上报
          ``stream.latency`` 时必须写「估算」，否则就是把估数当实测卖。
        """
        want = int(rate_want or 0)
        actual = int(rate_actual or 0)
        fell_back = bool(want and actual and want != actual)
        dsd_value = int(dsd_rate or 0)
        dop_value = int(dop_rate or 0)
        if not dsd_value and dop_value:
            # 兜底：DoP 载体率 × 16 = DSD 位率（引擎正常会给 dsd_rate，
            # 这里保证「载体率有、位率缺」时仍能写出 DSD64/DoP64 而不是问号）
            dsd_value = dop_value * 16
        dop_on = bool(dop and dsd_value and dop_value)
        note = str(dop_note or "")

        stages: list[SignalStage] = []
        stages.append(SignalStage("source", detail=source or "—"))

        decode_detail = " · ".join(part for part in (decoder_version, codec) if part) or "—"
        decode_status = StageStatus.PASSTHROUGH
        if dop_on:
            # 「DSD64 → DoP64 @176.4 kHz」：只写通路，不写任何音质结论
            decode_detail = TEXT_DOP_DECODE.format(
                dsd=dsd_label_for(dsd_value),
                dop=dop_label_for(dsd_value),
                rate=format_rate_khz(dop_value),
            )
        elif is_dsd and dsd_value and note:
            # **真回退**才换措辞并标 FALLBACK：写清「本来该是哪一档、退到了多少、
            # 为什么」（PRD P1-1②/P1-2②）。没有回退时保持 v1.8 的老写法
            # 「… · DSD→176400 Hz」—— 一次只改该改的那一处，别的面板文案不漂移。
            decode_detail = TEXT_DSD_TO_PCM.format(
                dsd=dsd_label_for(dsd_value),
                rate=format_rate_khz(actual or sample_rate),
            )
            decode_detail = f"{decode_detail}（{note}）"
            decode_status = StageStatus.FALLBACK
        elif is_dsd and actual:
            decode_detail = f"{decode_detail} · DSD→{actual} Hz"
        stages.append(SignalStage("decode", detail=decode_detail, status=decode_status))

        format_detail = " · ".join(
            part
            for part in (
                sample_fmt,
                f"{channels}ch" if channels else "",
                f"{sample_rate} Hz" if sample_rate else "",
            )
            if part
        ) or "—"
        if format_note:
            format_detail = f"{format_detail} · {format_note}"
        stages.append(
            SignalStage(
                "format",
                detail=format_detail,
                status=StageStatus.PASSTHROUGH if int_pipeline else StageStatus.CHANGED,
            )
        )

        if dop_on:
            # DoP 期间任何软件增益都会破坏位流：必须显式说"被关掉了"，
            # 而不是给一句笼统的「0 dB 旁路」让用户以为他自己设的
            stages.append(
                SignalStage("gain", detail=TEXT_DOP_GAIN_BYPASSED, status=StageStatus.BYPASSED)
            )
        elif gain_active and abs(gain_db) > 1e-9:
            stages.append(
                SignalStage("gain", detail=f"{gain_db:+.1f} dB 浮点乘法", status=StageStatus.CHANGED)
            )
        else:
            stages.append(
                SignalStage("gain", detail="0 dB 旁路（无软件增益）", status=StageStatus.BYPASSED)
            )

        if dop_on:
            stages.append(
                SignalStage("eq", detail=TEXT_DOP_EQ_BYPASSED, status=StageStatus.BYPASSED)
            )
        elif eq_enabled and eq_active:
            stages.append(
                SignalStage("eq", detail=f"{eq_bands} 段 · 峰值 ±12 dB", status=StageStatus.CHANGED)
            )
        elif eq_enabled:
            stages.append(SignalStage("eq", detail="全平直（0 dB）", status=StageStatus.PASSTHROUGH))
        else:
            stages.append(SignalStage("eq", detail="旁路", status=StageStatus.BYPASSED))

        if dop_on:
            stages.append(
                SignalStage("fade", detail=TEXT_DOP_FADE_BYPASSED, status=StageStatus.BYPASSED)
            )
        else:
            fade_parts: list[str] = []
            if crossfade_sec > 0:
                fade_parts.append(f"交叉淡变 {crossfade_sec:.1f} s")
            if ramp_ms > 0:
                fade_parts.append(f"切歌斜坡 {int(ramp_ms)} ms")
            if gapless:
                fade_parts.append("同规格无缝")
            stages.append(
                SignalStage(
                    "fade",
                    detail=" · ".join(fade_parts) or "无",
                    status=StageStatus.CHANGED if crossfade_sec > 0 else (
                        StageStatus.PASSTHROUGH if fade_parts else StageStatus.BYPASSED
                    ),
                )
            )

        resample_parts: list[str] = []
        resample_status = StageStatus.PASSTHROUGH
        if fell_back and not dop_on:
            resample_parts.append(TEXT_FALLBACK_RATE.format(src=want, dst=actual))
            resample_status = StageStatus.FALLBACK
        if downmix_from and channels and downmix_from > channels:
            resample_parts.append(f"下混 {downmix_from}→{channels}")
            resample_status = StageStatus.CHANGED
        if dop_on and not resample_parts:
            resample_parts.append(TEXT_DOP_RESAMPLE)
        stages.append(
            SignalStage(
                "resample",
                detail="；".join(resample_parts) or "未重采样 · 声道直通",
                status=resample_status,
            )
        )

        backend_key = str(backend or "").lower()
        backend_text = BACKEND_LABELS.get(backend_key, str(backend or "") or "默认后端")
        # `exclusive` 是 v1.6 的旧参数，语义上等同「实际独占」；新代码用 exclusive_actual
        actual_exclusive = bool(exclusive_actual or exclusive)
        label = str(mode_label or ("shared" if not actual_exclusive else "loose"))
        if backend_key == "asio" and not actual_exclusive:
            output_status = StageStatus.ERROR
            output_detail = TEXT_OUTPUT_ASIO_MISSING
        elif backend_key == "asio":
            # ASIO 驱动天然独占：不要套用 WASAPI 的「宽松/严格」两档措辞
            output_detail = " · ".join(part for part in (TEXT_OUTPUT_ASIO, device) if part)
            output_status = (
                StageStatus.FALLBACK if exclusive_fallback else StageStatus.PASSTHROUGH
            )
        elif actual_exclusive:
            head = TEXT_OUTPUT_STRICT if label == "strict" else TEXT_OUTPUT_LOOSE
            output_detail = " · ".join(part for part in (head, device) if part)
            # 拿到了独占但降过级（换率/从严格退宽松）→ FALLBACK，面板要标红
            output_status = (
                StageStatus.FALLBACK if exclusive_fallback else StageStatus.PASSTHROUGH
            )
        elif exclusive_refused:
            output_detail = " · ".join(
                part for part in (TEXT_OUTPUT_REFUSED, device) if part
            )
            output_status = StageStatus.FALLBACK
        elif backend_key == "wasapi":
            # 请求了独占却落在共享：必须标 FALLBACK（面板不能显示"独占"）
            output_detail = " · ".join(
                part for part in (TEXT_OUTPUT_SHARED, device) if part
            )
            output_status = (
                StageStatus.FALLBACK if mode_note else StageStatus.PASSTHROUGH
            )
        else:
            output_detail = " · ".join(
                part for part in (backend_text, "共享", device) if part
            )
            output_status = StageStatus.PASSTHROUGH
        if mode_note:
            output_detail = f"{output_detail}（{mode_note}）" if output_detail else mode_note
        stages.append(
            SignalStage("output", detail=output_detail or "—", status=output_status)
        )

        mix_rate = int(system_mix_rate or 0)
        if actual and mix_rate and mix_rate != actual and actual_exclusive:
            rate_text = f"{actual} Hz（系统混音 {mix_rate} Hz，已绕开）"
        elif actual and want and actual != want:
            rate_text = f"{actual} Hz（源 {want} Hz）"
        elif actual:
            rate_text = f"{actual} Hz"
        else:
            rate_text = ""
        device_detail = " · ".join(
            part
            for part in (
                rate_text,
                f"{channels} ch" if channels else "",
                device_dtype,
                latency_text(backend_key, latency_ms, latency_source),
            )
            if part
        ) or "—"
        stages.append(
            SignalStage(
                "device",
                detail=device_detail,
                status=StageStatus.FALLBACK if fell_back else StageStatus.PASSTHROUGH,
            )
        )

        self.stages = stages
        self._context = {
            "exclusive_actual": actual_exclusive,
            "mode_label": label,
            "system_mix_rate": mix_rate,
            "rate_want": want,
            "rate_actual": actual,
            "int_pipeline": bool(int_pipeline),
            "sample_fmt": str(sample_fmt or ""),
            "effective_bit_depth": int(effective_bit_depth or 0),
            "mode_note": str(mode_note or ""),
            "exclusive_fallback": bool(exclusive_fallback),
            "dop": dop_on,
            "dsd_rate": dsd_value,
            "dop_rate": dop_value,
            "dop_note": note,
            "latency_source": str(latency_source or ""),
        }
        return self

    # ------------------------------------------------------------ 运行期修正

    def stage(self, key: str) -> Optional[SignalStage]:
        for item in self.stages:
            if item.key == key:
                return item
        return None

    def mark(self, key: str, status: StageStatus | str, detail: str = "") -> "SignalPath":
        """局部修正某级状态（不传 detail 时保留原描述）。"""
        target = self.stage(key)
        if target is None:
            log.warning("信号路径没有 %r 这一级，忽略标记", key)
            return self
        try:
            target.status = status if isinstance(status, StageStatus) else StageStatus(str(status))
        except ValueError:
            target.status = StageStatus.CHANGED
        if detail:
            target.detail = assert_wording(detail)
        return self

    def bump(self, counter: str, amount: int | float = 1) -> None:
        """计数器自增（引擎在欠载/解码错误/回退时调用）。"""
        if hasattr(self.counters, counter):
            setattr(self.counters, counter, getattr(self.counters, counter) + amount)
        else:  # pragma: no cover - 防御性
            log.debug("未知计数器 %r", counter)

    # ------------------------------------------------------------ 读数

    # ------------------------------------------------------------ 话术分级（v1.7 §7）

    def wording_level(self) -> str:
        """返回 ``"L2"`` / ``"L1"`` / ``"L0"``（**永远不会返回 L3**）。

        * **L2**：严格独占 + 整数通路 + DSP 全旁路 + 率一致。本进程内可枚举，
          但不含"设备侧未转换"。
        * **L1**：实际独占 + 率一致 + 设备默认率与本流率不同（说明真的绕开了混音器）。
        * **L0**：其余情况 —— 只能说「未做软件改动」。
        """
        ctx = self._context or {}
        exclusive = bool(ctx.get("exclusive_actual"))
        want = int(ctx.get("rate_want") or 0)
        actual = int(ctx.get("rate_actual") or 0)
        rate_match = bool(want and actual and want == actual)
        mix_rate = int(ctx.get("system_mix_rate") or 0)
        gain_changed = self.stage("gain") is not None and self.stage("gain").status is StageStatus.CHANGED
        eq_changed = self.stage("eq") is not None and self.stage("eq").status is StageStatus.CHANGED
        fade_changed = self.stage("fade") is not None and self.stage("fade").status is StageStatus.CHANGED

        if (
            exclusive
            and str(ctx.get("mode_label")) == "strict"
            and bool(ctx.get("int_pipeline"))
            and rate_match
            and not (gain_changed or eq_changed or fade_changed)
        ):
            return "L2"
        if exclusive and rate_match and mix_rate and mix_rate != actual:
            return "L1"
        return "L0"

    def wording_key(self) -> str:
        """话术的 i18n 键（界面按当前语言渲染；L3 永远不会有键）。"""
        return {
            "L2": "sig_l2_no_resample",
            "L1": "sig_l1_bypass_mixer",
        }.get(self.wording_level(), "sig_l0_untouched")

    def wording_text(self) -> str:
        """当前状态**允许说**的那句话（L0/L1/L2 之一）。"""
        level = self.wording_level()
        ctx = self._context or {}
        if level == "L2":
            # 容器由样本格式决定，**有效位深由源决定**：16bit 源进 32bit 容器时，
            # 说"有效位深 32"是错的（数据只有 16 位有信息）。
            fmt = str(ctx.get("sample_fmt", ""))
            wide = fmt.startswith(("s24", "s32"))
            container = "32" if wide else "16"
            source_depth = int(ctx.get("effective_bit_depth") or 0)
            depth = str(source_depth) if source_depth else ("24" if wide else "16")
            return TEXT_L2.format(container=container, depth=depth)
        if level == "L1":
            return TEXT_L1.format(
                mix=int(ctx.get("system_mix_rate") or 0),
                rate=int(ctx.get("rate_actual") or 0),
            )
        return TEXT_NO_SOFTWARE_CHANGE

    def is_bitperfect_claimable(self) -> bool:
        """是否没有任何一级被「改变」——决定要不要显示「未做软件改动」标签。

        名字沿用架构文档，但它**不构成任何位完美承诺**：它只表示本进程没有主动
        改样本（没重采样、没增益、没 EQ），系统混音器是否还在背后动手脚，
        不在本进程可知范围内。``BYPASSED``（明确旁路）也算「没动」。
        """
        return all(
            stage.status in (StageStatus.PASSTHROUGH, StageStatus.BYPASSED)
            for stage in self.stages
        )

    def summary(self) -> str:
        """人类可读总述：全直通 → 「未做软件改动」；否则列出被动过的级。"""
        if self.is_bitperfect_claimable():
            return TEXT_NO_SOFTWARE_CHANGE
        changed = [
            f"{stage.label}：{stage.detail}"
            for stage in self.stages
            if stage.status in (StageStatus.CHANGED, StageStatus.FALLBACK, StageStatus.ERROR)
        ]
        return " · ".join(changed) or TEXT_NO_SOFTWARE_CHANGE

    def to_text(self) -> str:
        """多行纯文本（日志与「复制信号路径」用）。"""
        lines = [f"{stage.label}：{stage.detail}（{stage.status.value}）" for stage in self.stages]
        lines.append(f"总述：{self.summary()}")
        return "\n".join(lines)

    def snapshot(self) -> list[dict[str, Any]]:
        """9 级快照（可 JSON 序列化，界面直接渲染）。"""
        return [stage.to_dict() for stage in self.stages]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": self.snapshot(),
            "counters": self.counters.to_dict(),
            "summary": self.summary(),
            "no_software_change": self.is_bitperfect_claimable(),
        }

    def __repr__(self) -> str:  # pragma: no cover - 日志/调试
        return f"<SignalPath {self.summary()}>"


__all__ = [
    "SignalStage",
    "SignalPath",
    "Counters",
    "STAGE_LABELS",
    "TEXT_NO_SOFTWARE_CHANGE",
    "TEXT_INT_PIPELINE_UNAVAILABLE",
    "TEXT_FALLBACK_RATE",
    "FORBIDDEN_CLAIMS",
    "TEXT_L1",
    "TEXT_L2",
    "TEXT_L2",
    "TEXT_L3_FORBIDDEN",
    "TEXT_OUTPUT_STRICT",
    "TEXT_OUTPUT_LOOSE",
    "TEXT_OUTPUT_SHARED",
    "TEXT_OUTPUT_REFUSED",
    "TEXT_OUTPUT_ASIO_MISSING",
    "TEXT_OUTPUT_ASIO",
    "TEXT_LATENCY",
    "TEXT_ASIO_BUFFER_MEASURED",
    "TEXT_ASIO_BUFFER_ESTIMATED",
    "TEXT_DOP_DECODE",
    "TEXT_DOP_FALLBACK",
    "TEXT_DSD_TO_PCM",
    "TEXT_DOP_GAIN_BYPASSED",
    "TEXT_DOP_EQ_BYPASSED",
    "TEXT_DOP_FADE_BYPASSED",
    "TEXT_DOP_RESAMPLE",
    "BACKEND_LABELS",
    "assert_wording",
    "latency_text",
]
