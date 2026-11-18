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

log = logging.getLogger(__name__)

#: 全直通时的总述（措辞铁律 R2，只能这么写）
TEXT_NO_SOFTWARE_CHANGE = "未做软件改动"
#: 采样率回退时的说明模板
TEXT_FALLBACK_RATE = "已重采样 {src}→{dst}（设备不支持源采样率）"
#: 整数通路被 DSP 挤掉时的说明（面板必须说清楚为什么「位完美」没生效）
TEXT_INT_PIPELINE_UNAVAILABLE = "整数通路不可用（DSP 已启用，改用浮点）"
#: 绝不允许出现的说法
FORBIDDEN_CLAIMS = ("bit-perfect", "bit perfect", "位完美", "逐样本一致", "比特完美")

#: 后端键 -> 界面显示名
BACKEND_LABELS = {
    "wasapi": "WASAPI",
    "mme": "MME",
    "directsound": "DirectSound",
    "wdmks": "WDM-KS",
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
    """把禁用的措辞降级为合规说法（防某处手滑写死）。"""
    value = str(text or "")
    lowered = value.lower()
    for banned in FORBIDDEN_CLAIMS:
        if banned.lower() in lowered:
            log.warning("信号路径文案含禁用措辞 %r，已替换：%s", banned, value)
            return TEXT_NO_SOFTWARE_CHANGE
    return value


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "underruns": int(self.underruns),
            "dropped_frames": int(self.dropped_frames),
            "resample_events": int(self.resample_events),
            "decode_errors": int(self.decode_errors),
            "gain_db": round(float(self.gain_db), 2),
            "last_gap_ms": round(float(self.last_gap_ms), 2),
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
        device_dtype: str = "",
        int_pipeline: bool = False,
        format_note: str = "",
    ) -> "SignalPath":
        """按真实链路重建 9 级描述（引擎开流成功后调用一次）。"""
        want = int(rate_want or 0)
        actual = int(rate_actual or 0)
        fell_back = bool(want and actual and want != actual)

        stages: list[SignalStage] = []
        stages.append(SignalStage("source", detail=source or "—"))

        decode_detail = " · ".join(part for part in (decoder_version, codec) if part) or "—"
        if is_dsd and actual:
            decode_detail = f"{decode_detail} · DSD→{actual} Hz"
        stages.append(SignalStage("decode", detail=decode_detail))

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

        if gain_active and abs(gain_db) > 1e-9:
            stages.append(
                SignalStage("gain", detail=f"{gain_db:+.1f} dB 浮点乘法", status=StageStatus.CHANGED)
            )
        else:
            stages.append(
                SignalStage("gain", detail="0 dB 旁路（无软件增益）", status=StageStatus.BYPASSED)
            )

        if eq_enabled and eq_active:
            stages.append(
                SignalStage("eq", detail=f"{eq_bands} 段 · 峰值 ±12 dB", status=StageStatus.CHANGED)
            )
        elif eq_enabled:
            stages.append(SignalStage("eq", detail="全平直（0 dB）", status=StageStatus.PASSTHROUGH))
        else:
            stages.append(SignalStage("eq", detail="旁路", status=StageStatus.BYPASSED))

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
        if fell_back:
            resample_parts.append(TEXT_FALLBACK_RATE.format(src=want, dst=actual))
            resample_status = StageStatus.FALLBACK
        if downmix_from and channels and downmix_from > channels:
            resample_parts.append(f"下混 {downmix_from}→{channels}")
            resample_status = StageStatus.CHANGED
        stages.append(
            SignalStage(
                "resample",
                detail="；".join(resample_parts) or "未重采样 · 声道直通",
                status=resample_status,
            )
        )

        backend_text = BACKEND_LABELS.get(str(backend or "").lower(), str(backend or "") or "默认后端")
        output_detail = " · ".join(
            part for part in (backend_text, "独占" if exclusive else "共享", device) if part
        )
        stages.append(SignalStage("output", detail=output_detail or "—"))

        device_detail = " · ".join(
            part
            for part in (
                f"{actual} Hz" if actual else "",
                f"{channels} ch" if channels else "",
                device_dtype,
                f"延迟 {latency_ms:.0f} ms" if latency_ms else "",
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
    "BACKEND_LABELS",
    "assert_wording",
]
