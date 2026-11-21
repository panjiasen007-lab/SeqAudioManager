"""播放引擎：状态机 + 队列 + 解码/输出装配 + 位置时钟。

设计约束（架构文档第五节）
--------------------------
引擎**自己不起线程**：全部方法都在调用方线程（GUI）执行。跨线程的只有两个
既成事实 ——

* ``FfmpegDecoder`` 内部的解码线程（T02 实现，它只写环形缓冲）；
* ``AudioOutput`` 的 PortAudio 回调（只从环形缓冲读，并调用 ``DspChain``）。

这两者都不碰引擎字段，只通过「环形缓冲的单调计数」和「置一个 bool 标志」上报。
因此引擎里没有锁：状态迁移、队列、位置全部只由 GUI 线程写。

位置为什么用「环形缓冲已读帧数」
--------------------------------
``AudioOutput.frames_out`` 在暂停时照样递增（暂停是「写静音」不关流），拿它算
位置会让进度条在暂停时空跑。``ring.frames_read`` 只在回调真正取走样本时增加，
暂停时冻结、seek 后归零，正好是我们要的语义。

自动续播为什么靠 ``poll()``
---------------------------
解码线程发现 EOF 时只置 ``_eof_pending``（不改状态机，遵守回调纪律第 7 条）；
真正的「取下一首」发生在 GUI 线程的 ``poll()`` 里 —— 由 ``PlayerBridge`` 每
100 ms 调一次。这样状态迁移、队列修改、开流全部留在单一线程，不会出现两个
线程同时改队列。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from ..core.config import Settings
from . import devices as devices_mod
from .constants import (
    BACKEND_WASAPI,
    BLOCK_FRAMES_DEFAULT,
    EVENT_ERROR,
    EVENT_GAP,
    EVENT_POSITION,
    EVENT_QUEUE,
    EVENT_SIGNAL,
    EVENT_STATE,
    EVENT_TRACK,
    PREFILL_BLOCKS,
    RING_SECONDS,
    OutputMode,
    PlayMode,
    PlayerState,
    StageStatus,
    normalize_output_mode,
)
from .decoder import DecodeError, DecodeSpec, FfmpegDecoder, int_sample_fmt_for
from .dsp import DspChain, int_pipeline_allowed
from .devices import (
    DeviceBusyError,
    DeviceError,
    DeviceInfo,
    DeviceLostError,
    NoOutputDeviceError,
    RateUnsupportedError,
)
from .output import AudioOutput
from .queue import PlayQueue, QueueItem
from .ringbuffer import PcmRingBuffer
from .signal_path import TEXT_INT_PIPELINE_UNAVAILABLE, SignalPath

log = logging.getLogger(__name__)

#: 事件名（constants 里没有 eof/volume 的常量，这里补齐并统一走 ``on_event``）
EVENT_EOF = "eof"
EVENT_VOLUME = "volume"

#: 允许的状态迁移（非法迁移只记日志、不改状态，绝不抛异常给界面）
ALLOWED_TRANSITIONS: dict[PlayerState, frozenset[PlayerState]] = {
    PlayerState.IDLE: frozenset({PlayerState.LOADING, PlayerState.ERROR}),
    PlayerState.LOADING: frozenset(
        {PlayerState.PLAYING, PlayerState.PAUSED, PlayerState.IDLE, PlayerState.ERROR}
    ),
    PlayerState.PLAYING: frozenset(
        {PlayerState.PAUSED, PlayerState.LOADING, PlayerState.IDLE, PlayerState.ERROR}
    ),
    PlayerState.PAUSED: frozenset(
        {PlayerState.PLAYING, PlayerState.LOADING, PlayerState.IDLE, PlayerState.ERROR}
    ),
    PlayerState.ERROR: frozenset({PlayerState.IDLE, PlayerState.LOADING, PlayerState.PLAYING}),
}


class _Attempt:
    """一次起播尝试的结果（内部用，不进 snapshot）。

    ``retryable`` 为真表示"这一级开流失败、可以降级重试"，由 ``_start_item`` 的
    外层循环处理；为假表示已经 ``_fail`` 过，不要再试（设计 §4.3 硬规则 1/2）。
    """

    __slots__ = ("ok", "retryable", "level", "message")

    def __init__(self, ok: bool, retryable: bool = False, level: int = 0, message: str = "") -> None:
        self.ok = ok
        self.retryable = retryable
        self.level = level
        self.message = message

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<_Attempt ok={self.ok} retryable={self.retryable} level={self.level}>"


class PlayerError(RuntimeError):
    """引擎层错误（解码不可用、设备不可用、无曲目可播…）。"""


@dataclass
class PlayerSnapshot:
    """``snapshot()`` 的对象形式（设计文档 3.1 的 ``PlayerSnapshot``）。

    ``snapshot()`` 本身按任务书返回 ``dict``（界面与 JSON 直接用）；
    需要强类型时用 ``snapshot_object()``。
    """

    state: PlayerState = PlayerState.IDLE
    item: Optional[QueueItem] = None
    position_ms: int = 0
    duration_ms: int = 0
    rate: int = 0
    channels: int = 0
    volume_db: float = 0.0
    mode: PlayMode = PlayMode.SEQUENTIAL
    signal: Optional[SignalPath] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "track": self.item.to_dict() if self.item else None,
            "position_ms": int(self.position_ms),
            "duration_ms": int(self.duration_ms),
            "rate": int(self.rate),
            "channels": int(self.channels),
            "volume_db": float(self.volume_db),
            "mode": self.mode.value,
            "signal_path": self.signal.snapshot() if self.signal else [],
        }


class PlayerEngine:
    """播放引擎门面。

    Args:
        settings: ``core.config.Settings``；``None`` 时用一份默认配置（测试友好）。
        on_event: ``callable(kind: str, payload)``，与逐事件回调同时生效。
        output_factory: 注入用，签名 ``(ring, device_info, hook) -> AudioOutputLike``；
            测试里可以塞一个假输出，完全不碰声卡。
        decoder_factory: 注入用，签名 ``(spec, ring, on_error, on_eof) -> DecoderLike``。
        queue: 预置队列；``None`` 时建空队列。
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        on_event: Optional[Callable[[str, Any], None]] = None,
        *,
        output_factory: Optional[Callable[..., Any]] = None,
        decoder_factory: Optional[Callable[..., Any]] = None,
        queue: Optional[PlayQueue] = None,
    ) -> None:
        self.settings = settings or Settings()
        self._on_event = on_event

        # ---- 逐事件回调（同步；实现方不得在回调里做重活）----
        self.on_state_changed: Optional[Callable[[PlayerState], None]] = None
        self.on_track_changed: Optional[Callable[[Optional[dict]], None]] = None
        self.on_position: Optional[Callable[[int], None]] = None
        self.on_error: Optional[Callable[[str], None]] = None
        self.on_eof: Optional[Callable[[], None]] = None
        self.on_volume: Optional[Callable[[float], None]] = None
        self.on_signal_path: Optional[Callable[[dict], None]] = None
        self.on_queue_changed: Optional[Callable[[int], None]] = None
        self.on_gap: Optional[Callable[[float], None]] = None

        self._queue = queue or PlayQueue(mode=self._settings_mode())
        self._state = PlayerState.IDLE
        self._item: Optional[QueueItem] = None
        self._ring: Optional[PcmRingBuffer] = None
        self._decoder: Any = None
        self._output: Any = None
        self._spec: Optional[DecodeSpec] = None
        self._rate = 0
        self._channels = 0
        self._ring_base_ms = 0

        self._dsp = DspChain(
            gain_db=float(getattr(self.settings, "playback_volume_db", 0.0) or 0.0),
            eq_gains=list(getattr(self.settings, "playback_eq_gains", []) or []),
            eq_enabled=bool(getattr(self.settings, "playback_eq_enabled", False)),
            ramp_ms=int(getattr(self.settings, "playback_ramp_ms", 5) or 5),
            enabled=True,
        )
        if str(getattr(self.settings, "playback_volume_mode", "bypass")) != "software":
            self._dsp.set_gain_db(0.0)
        if bool(getattr(self.settings, "playback_dsp_bypass", True)):
            # 全局直通：只旁路 EQ/调音，**不动软件音量**（音量由 volume_mode
            # 单独裁决，见 set_bypass 的说明）。切歌斜坡保留，防爆音。
            self._dsp.set_eq_enabled(False)

        self._signal = SignalPath.blank()
        self._volume_db = float(getattr(self.settings, "playback_volume_db", 0.0) or 0.0)
        self._busy = False
        self._error = ""
        self._fallback = False
        self._want_rate = 0
        self._eof_pending = False
        self._last_position_ms = -1
        self._last_gap_ms = 0.0
        self._int_downgraded = False   # 用户要整数通路、但被 DSP 挤回浮点
        self._plan: Any = None         # 本次起播的输出决定（devices.OutputPlan）
        self._strict_locked = False    # 严格独占：DSP/音量被锁在旁路
        self._force_shared = False     # 独占开流失败过 → 本会话内直接用共享
        self._released_on_pause = False
        # _output_mode() 是查询函数，但 snapshot() 每次 UI 刷新都会调它（每秒多次）。
        # 只记「已提示过的 (后端, 模式)」，变化时才打一行，避免日志被同一句话刷屏。
        self._mode_downgrade_notified: tuple[str, str] | None = None
        self._pending_choice = ""      # fallback="ask" 时要弹的非模态提示
        self._exclusive_refused_note = ""  # 独占开流失败的原因（降级留痕）
        #: (device_index, want_rate, mode) -> 连续开流失败次数
        self._open_failures: dict[tuple, int] = {}
        self._probe_mismatch_total = 0           # 会话累计（stop 后仍保留）
        #: (device_index, want_rate, mode) -> 开流失败过的层级。
        #: **键必须带率与模式**：dev22 上「192k 独占 ✓ / 176.4k 独占 ✗」说明同一
        #: 设备同一 level 的成败与请求率强相关；只按设备记会让「44.1k 失败」污染
        #: 「48k/192k 起播」——轻则白白跳过可用能力（静默降质），重则让 48k 落到
        #: level 3 被标 FALLBACK 并写「已重采样」，而实际一个样本都没重采样（面板说谎）。
        self._device_skip_levels: dict[tuple, set[int]] = {}
        #: 同上三元键 -> 「这组参数探测说行、开流被拒」——说明文字要用
        self._device_refused: dict[tuple, bool] = {}
        self._underrun_seen = 0        # 当前这条流的欠载数（回调自增）
        self._underrun_base = 0        # 已拆掉的流累计欠载数
        self._closed = False

        self._output_factory = output_factory
        self._decoder_factory = decoder_factory
        block_frames = int(
            getattr(self.settings, "playback_block_frames", BLOCK_FRAMES_DEFAULT)
            or BLOCK_FRAMES_DEFAULT
        )
        self._block_frames = block_frames

    # ------------------------------------------------------------ 只读属性

    @property
    def state(self) -> PlayerState:
        return self._state

    @property
    def queue(self) -> PlayQueue:
        return self._queue

    @property
    def current_item(self) -> Optional[QueueItem]:
        return self._item

    @property
    def busy(self) -> bool:
        return self._busy

    @property
    def error(self) -> str:
        return self._error

    @property
    def volume_db(self) -> float:
        return self._volume_db

    @property
    def signal_path(self) -> SignalPath:
        return self._signal

    def state_value(self) -> str:
        """状态字符串（界面/JSON 用）。"""
        return self._state.value

    # ------------------------------------------------------------ 事件

    def _emit(self, kind: str, payload: Any = None) -> None:
        """派发事件；任何回调抛异常都只记日志，绝不影响播放。"""
        target = {
            EVENT_STATE: self.on_state_changed,
            EVENT_TRACK: self.on_track_changed,
            EVENT_POSITION: self.on_position,
            EVENT_ERROR: self.on_error,
            EVENT_EOF: self.on_eof,
            EVENT_VOLUME: self.on_volume,
            EVENT_SIGNAL: self.on_signal_path,
            EVENT_QUEUE: self.on_queue_changed,
            EVENT_GAP: self.on_gap,
        }.get(kind)
        if target is not None:
            try:
                target(payload)
            except BaseException:  # noqa: BLE001 - 回调实现方的问题不能拖垮播放
                log.exception("播放事件回调 %s 抛异常", kind)
        if self._on_event is not None:
            try:
                self._on_event(kind, payload)
            except BaseException:  # noqa: BLE001
                log.exception("on_event 回调抛异常（kind=%s）", kind)

    def _set_state(self, new_state: PlayerState) -> bool:
        """状态迁移（非法迁移只记日志）。返回是否真的变了。"""
        if new_state is self._state:
            return False
        allowed = ALLOWED_TRANSITIONS.get(self._state, frozenset())
        if new_state not in allowed:
            log.debug("忽略非法状态迁移：%s -> %s", self._state.value, new_state.value)
            return False
        previous = self._state
        self._state = new_state
        log.debug("播放状态：%s -> %s", previous.value, new_state.value)
        self._emit(EVENT_STATE, new_state)
        return True

    # ------------------------------------------------------------ 公开命令

    def play(self, track: Any = None) -> bool:
        """起播 / 继续 / 从暂停恢复。**幂等**：重复调用不报错。

        Args:
            track: ``Track`` 或 ``QueueItem``；给定就切到它（不在队列里则换成单曲队列），
                不给则播当前队列项。

        Returns:
            是否进入了播放（批处理占用、无曲目、设备不可用时返回 ``False``）。
        """
        if self._closed:
            return False
        if self._busy:
            log.info("批处理进行中，拒绝起播")
            self._emit(EVENT_ERROR, "批处理进行中，暂不能开始播放")
            return False

        if track is not None:
            item = track if isinstance(track, QueueItem) else QueueItem.from_track(track)
            position = self._queue.index_of(item.key)
            if position >= 0:
                self._queue.index = position
            else:
                self._queue.load([item])
            self._emit(EVENT_QUEUE, len(self._queue))
            return self._start_item(item, 0)

        if self._state is PlayerState.PLAYING:
            return True  # 幂等：已经在播，什么都不做
        if self._state is PlayerState.PAUSED:
            return self.resume()
        item = self._item or self._queue.current()
        if item is None:
            log.debug("队列为空，无法起播")
            return False
        return self._start_item(item, 0)

    def resume(self) -> bool:
        """从暂停恢复；非暂停状态调用无副作用。"""
        if (
            self._state is PlayerState.PAUSED
            and self._released_on_pause
            and self._item is not None
        ):
            # 暂停时释放过设备：重开流并从冻结的位置续播
            position = self.position_ms()
            self._released_on_pause = False
            started = self._start_item(self._item, position)
            if not started and self._state is PlayerState.ERROR:
                # 暂停期间设备可能被浏览器/会议软件抢走：必须进 ERROR（失败路径
                # 已做）、不静默改共享、不跳下一首，并把位置原样留下——用户关掉
                # 占用程序后点「播放」还能从这个位置续播。
                self._ring_base_ms = position
                reason = str(self._error or "")
                if reason.startswith(("打开输出设备失败", "独占各级开流均失败")):
                    self._fail(
                        f"恢复播放失败：设备已被其它程序占用，位置已保留（{position} ms）。"
                        "关闭占用该设备的程序后点「播放」可从原位置继续。"
                    )
            return started
        if self._state is not PlayerState.PAUSED or self._decoder is None or self._output is None:
            return self._state is PlayerState.PLAYING
        try:
            self._decoder.resume_feeding()
            self._output.resume()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"恢复播放失败：{exc}")
            return False
        self._dsp.arm_fade_in()
        self._set_state(PlayerState.PLAYING)
        return True

    def pause(self) -> bool:
        """暂停：斜坡淡出 → 停喂数据 → 回调写静音（不关流）。幂等。

        独占模式下若 `playback_release_on_pause=True`（Q3 定稿默认），则**关流释放
        设备**：否则用户暂停后浏览器/会议一直没声，而他根本不知道是自己占了设备。
        代价是恢复时要重开流（约 +100 ms）。
        """
        if self._state is PlayerState.PAUSED:
            return True
        if self._state is not PlayerState.PLAYING or self._output is None:
            return False
        self._fade_out_and_wait()
        position = self.position_ms()
        release = self._should_release_on_pause()
        try:
            if release:
                self._release_for_pause(position)
            else:
                if self._output is not None:
                    self._output.pause()
                if self._decoder is not None:
                    self._decoder.pause_feeding()
        except Exception as exc:  # noqa: BLE001
            self._fail(f"暂停失败：{exc}")
            return False
        self._set_state(PlayerState.PAUSED)
        return True

    def _should_release_on_pause(self) -> bool:
        """是否要在暂停时释放设备：只对「真独占」且用户没关掉该选项时才做。"""
        try:
            exclusive = bool(
                self._output is not None and getattr(self._output, "exclusive_actual", False)
            )
        except Exception:  # noqa: BLE001
            exclusive = False
        release_setting = bool(getattr(self.settings, "playback_release_on_pause", True))
        return exclusive and release_setting

    def _release_for_pause(self, position_ms: int) -> None:
        """关流释放设备，但把位置记在 `_ring_base_ms` 上（暂停时位置必须冻结）。"""
        log.info("暂停即释放独占设备（position=%d ms）", position_ms)
        self._teardown_playback()
        self._ring_base_ms = int(position_ms)
        self._rate = self._rate or 0
        self._released_on_pause = True

    def stop(self) -> bool:
        """停止：淡出 → 关流 → IDLE，位置归零。重复调用安全。"""
        if self._closed and self._state is PlayerState.IDLE:
            return True
        if self._state is PlayerState.IDLE and self._ring is None and self._decoder is None:
            return True
        self._fade_out_and_wait()
        self._teardown_playback()
        self._item = None
        self._ring_base_ms = 0
        self._rate = 0
        self._channels = 0
        self._eof_pending = False
        self._released_on_pause = False
        self._signal = SignalPath.blank()
        self._set_state(PlayerState.IDLE)
        self._emit(EVENT_TRACK, None)
        self._emit(EVENT_POSITION, 0)
        self._emit(EVENT_SIGNAL, self._signal.to_dict())
        return True

    def seek(self, ms: int) -> bool:
        """定位到曲目内的 ``ms``（重新起解码器，环清空后重填）。"""
        item = self._item
        if item is None or self._state in (PlayerState.IDLE, PlayerState.ERROR):
            return False
        duration = self.duration_ms()
        target = max(0, int(ms))
        if duration > 0:
            # 留出一小段尾巴：拖到最右端（或用户给 10^9）时仍能解出数据，
            # 否则会落在真实 EOF 之后，引擎只能判"没有音频"，体验上像卡死。
            tail_ms = max(120, int(self._block_frames * 1000 / max(1, self._rate)) * 3)
            target = min(target, max(0, duration - tail_ms))
        was_playing = self._state is PlayerState.PLAYING
        self._fade_out_and_wait()
        self._teardown_playback()
        started = self._start_item(item, target)
        if started and not was_playing:
            self.pause()
        return started

    def next(self) -> bool:
        """下一首（用户动作：单曲循环也会真的换歌）。"""
        item = self._queue.next(auto_advance=False)
        if item is None:
            log.debug("已经是队尾")
            return False
        return self._switch_to(item)

    def previous(self) -> bool:
        """上一首。"""
        item = self._queue.previous()
        if item is None:
            return False
        return self._switch_to(item)

    def set_mode(self, mode: PlayMode | str) -> PlayMode:
        """切换播放模式（顺序/随机/单曲/列表循环）。"""
        result = self._queue.set_mode(mode)
        self._emit(EVENT_QUEUE, len(self._queue))
        return result

    def set_volume(self, db: float) -> float:
        """设置软件音量（dB）。本期只有 ``volume_mode="software"`` 才真的改样本。"""
        value = max(-60.0, min(12.0, float(db)))
        if self._strict_locked:
            log.info("严格独占下软件音量被锁定为 0 dB（否则「未加软件增益」不成立）")
            value = 0.0
        self._volume_db = value
        if str(getattr(self.settings, "playback_volume_mode", "bypass")) == "software":
            self._dsp.set_gain_db(value)
        elif abs(value) > 1e-9:
            log.debug("音量模式为旁路，%.1f dB 只记录不应用（输出层与 DSP 均 0 dB）", value)
        self._refresh_signal()
        self._emit(EVENT_VOLUME, value)
        return value

    def set_bypass(self, on: bool) -> None:
        """全局 DSP 直通开关（``on=True`` 表示直通）。

        两点容易搞混，这里定死：

        * 直通只关「EQ / 调音模块」，**不关软件音量**。音量是不是参与，只由
          ``playback_volume_mode`` 决定 —— 用户在播放条上把滑块拖离 0 dB，
          界面就把模式切成 ``software``，那是一个明确意图，不该被一次
          「保存设置」（会重放本方法）悄悄清零，否则表现就是「调好的音量
          一保存就变回满音量」。
        * 直通也不关切歌斜坡。斜坡防爆音，是播放卫生措施，不是调音。
        """
        if on:
            self._dsp.set_eq_enabled(False)
            if str(getattr(self.settings, "playback_volume_mode", "bypass")) == "software":
                self._dsp.set_gain_db(self._volume_db)
            else:
                self._dsp.set_gain_db(0.0)
        else:
            self._apply_settings_to_dsp()
        self._refresh_signal()

    def set_eq_enabled(self, enabled: bool) -> None:
        if self._strict_locked and enabled:
            log.info("严格独占下 EQ 被强制旁路（否则「未加软件增益」不成立）")
            enabled = False
        self._dsp.set_eq_enabled(bool(enabled))
        self._refresh_signal()

    def set_eq_gains(self, gains: Sequence[float]) -> None:
        """更新各段增益（GUI 线程；系数与频响在这里算好）。"""
        self._dsp.set_eq_gains(gains)
        self._refresh_signal()

    def set_output(
        self,
        device: Optional[str] = None,
        backend: Optional[str] = None,
        exclusive: Optional[bool] = None,
    ) -> None:
        """改输出设备/后端/独占（下一次起播生效；正在播则重连）。"""
        if device is not None:
            self.settings.playback_device = str(device)
        if backend is not None:
            self.settings.playback_output_backend = str(backend)
        if exclusive is not None:
            self.settings.playback_exclusive = bool(exclusive)
        self._device_skip_levels.clear()
        self._device_refused.clear()
        self._open_failures.clear()
        if self._item is not None and self._state in (PlayerState.PLAYING, PlayerState.PAUSED):
            self.reconnect()

    def reconnect(self) -> bool:
        """重新打开设备并从当前位置续播（设备拔出后用户点「重新连接」）。"""
        self._force_shared = False        # 换设备/重连时给独占一次新机会
        self._exclusive_refused_note = ""
        info = self._resolve_device()
        index = info.index if info is not None else -1
        for bucket in [item for item in self._device_skip_levels if item[0] == index]:
            self._device_skip_levels.pop(bucket, None)
        for bucket in [item for item in self._device_refused if item[0] == index]:
            self._device_refused.pop(bucket, None)
        item = self._item
        if item is None:
            return False
        position = self.position_ms()
        self._fade_out_and_wait()
        self._teardown_playback()
        self._error = ""
        return self._start_item(item, position)

    # ---- 架构文档 3.1 列出的方法名（与上面的任务书命名并存）----

    def open_track(self, track: Any, autoplay: bool = True, start_ms: int = 0) -> bool:
        """装载一首（``autoplay=False`` 时只更新当前项并通知界面）。"""
        item = track if isinstance(track, QueueItem) else QueueItem.from_track(track)
        if autoplay:
            if start_ms <= 0:
                return self.play(item)
            position = self._queue.index_of(item.key)
            if position >= 0:
                self._queue.index = position
            else:
                self._queue.load([item])
            return self._start_item(item, int(start_ms))
        self._item = item
        self._emit(EVENT_TRACK, item.to_dict())
        return True

    def prev(self) -> bool:
        """``previous()`` 的别名。"""
        return self.previous()

    def set_volume_db(self, db: float) -> float:
        """``set_volume()`` 的别名。"""
        return self.set_volume(db)

    def set_eq(self, gains: Sequence[float]) -> None:
        """``set_eq_gains()`` 的别名。"""
        self.set_eq_gains(gains)

    def get_state(self) -> PlayerState:
        """``state`` 属性的方法形式（部分调用方喜欢显式调用）。"""
        return self._state

    def set_busy(self, busy: bool) -> None:
        """批处理互斥：``True`` 时拒绝新的起播请求（已播曲目继续）。"""
        self._busy = bool(busy)
        log.debug("播放器 busy=%s", self._busy)

    def set_queue(self, queue: PlayQueue) -> None:
        """替换整个队列（界面「用当前列表替换队列」）。"""
        self._queue = queue
        if self._queue.current() is None:
            self._item = None
        self._emit(EVENT_QUEUE, len(self._queue))

    # ------------------------------------------------------------ 位置与快照

    def duration_ms(self) -> int:
        item = self._item
        if item is None:
            return 0
        if item.duration_ms > 0:
            return int(item.duration_ms)
        return int(round(max(0.0, item.duration) * 1000.0))

    def position_ms(self) -> int:
        """当前曲目内的播放位置（毫秒）。暂停时冻结，停止后为 0。"""
        if self._item is None:
            return 0
        base = int(self._ring_base_ms)
        ring = self._ring
        if ring is None or self._rate <= 0:
            return max(0, base)
        frames = int(ring.frames_read)
        position = base + int(frames * 1000.0 / float(self._rate))
        duration = self.duration_ms()
        if duration > 0:
            position = min(position, duration)
        return max(0, position)

    def snapshot_object(self) -> PlayerSnapshot:
        return PlayerSnapshot(
            state=self._state,
            item=self._item,
            position_ms=self.position_ms(),
            duration_ms=self.duration_ms(),
            rate=self._rate,
            channels=self._channels,
            volume_db=self._volume_db,
            mode=self._queue.mode,
            signal=self._signal,
        )

    def snapshot(self) -> dict[str, Any]:
        """只读快照（全部原始类型，``json.dumps`` 不会报错）。"""
        output_snapshot: dict[str, Any] = {}
        if self._output is not None:
            try:
                output_snapshot = dict(self._output.snapshot())
            except Exception:  # noqa: BLE001 - 快照失败不该影响界面
                output_snapshot = {}
        self._sync_counters(output_snapshot)
        return {
            "state": self._state.value,
            "track": self._item.to_dict() if self._item else None,
            "position_ms": self.position_ms(),
            "duration_ms": self.duration_ms(),
            "rate": int(self._rate),
            "channels": int(self._channels),
            "volume_db": float(self._volume_db),
            "mode": self._queue.mode.value,
            "queue_index": int(self._queue.index),
            "queue_length": len(self._queue),
            "queue": self._queue.snapshot(),
            "queue_total_sec": round(self._queue.total_sec(), 3),
            "signal_path": self._signal.snapshot(),
            "signal_summary": self._signal.summary(),
            "counters": self._signal.counters.to_dict(),
            "output": output_snapshot,
            "device": output_snapshot.get("device", ""),
            "backend": output_snapshot.get(
                "backend", str(getattr(self.settings, "playback_output_backend", ""))
            ),
            "exclusive": bool(output_snapshot.get("exclusive", False)),
            # 注意键名：`mode` 是**播放模式**（顺序/随机…），输出模式单独叫
            # `output_mode` —— v1.6 的 UI/会话已经按 `mode` 读播放模式，不能抢名字。
            "output_mode": str(output_snapshot.get("mode", "") or self._output_mode().value),
            "output_mode_requested": str(
                getattr(self._plan, "requested_mode", self._output_mode()).value
            ),
            "mode_note": str(getattr(self._plan, "note", "") or ""),
            "system_mix_rate": int(output_snapshot.get("system_mix_rate", 0) or 0),
            "wording_level": self._signal.wording_level(),
            "wording_text": self._signal.wording_text(),
            "exclusive_choice_pending": self._pending_choice,
            "fallback": bool(self._fallback),
            "busy": bool(self._busy),
            "error": self._error,
            "eq_gains": self._dsp.eq.gains,
            "dsp": self._dsp.snapshot(),
        }

    def poll(self) -> dict[str, Any]:
        """定时轮询（``PlayerBridge`` 每 100 ms 调一次）：续播 + 位置 + 计数。"""
        self._drain_pending_eof()
        position = self.position_ms()
        if position != self._last_position_ms:
            self._last_position_ms = position
            self._emit(EVENT_POSITION, position)
        if self._output is not None:
            try:
                snapshot = self._output.snapshot()
            except Exception:  # noqa: BLE001
                snapshot = {}
            self._sync_counters(snapshot)
            lost = snapshot and not snapshot.get("is_open", True)
            if lost and self._state is PlayerState.PLAYING:
                self._fail("输出设备已断开（请重新连接）")
        return self.snapshot()

    def shutdown(self) -> None:
        """退出时调用：先停流再清引用（避免 PortAudio 卸载噪音）。"""
        try:
            self.stop()
        except Exception:  # noqa: BLE001
            log.exception("关闭播放引擎时出错")
        self._closed = True
        self._on_event = None
        self.on_state_changed = None
        self.on_track_changed = None
        self.on_position = None
        self.on_error = None
        self.on_eof = None
        self.on_volume = None
        self.on_signal_path = None
        self.on_queue_changed = None
        self.on_gap = None

    # ------------------------------------------------------------ 起播内部

    def _settings_mode(self) -> PlayMode:
        raw = str(getattr(self.settings, "playback_mode", PlayMode.SEQUENTIAL.value))
        try:
            return PlayMode(raw)
        except ValueError:
            return PlayMode.SEQUENTIAL

    def _apply_settings_to_dsp(self) -> None:
        settings = self.settings
        # 音量先按 volume_mode 定位（与 bypass 无关，理由见 set_bypass）
        if str(getattr(settings, "playback_volume_mode", "bypass")) == "software":
            self._dsp.set_gain_db(self._volume_db)
        else:
            self._dsp.set_gain_db(0.0)
        if bool(getattr(settings, "playback_dsp_bypass", True)):
            self._dsp.set_eq_enabled(False)
            return
        self._dsp.set_eq_enabled(bool(getattr(settings, "playback_eq_enabled", False)))
        gains = getattr(settings, "playback_eq_gains", None)
        if gains:
            self._dsp.set_eq_gains(gains)

    # ------------------------------------------------------------ 输出模式（v1.7）

    def _output_mode(self) -> OutputMode:
        """由设置解析出**本次**输出模式（含严格模式的可行性校验）。

        ``playback_exclusive`` 是 v1.6 的布尔旧值，只做镜像：真正的语义在
        ``playback_output_mode``。若后端不是 WASAPI，独占没有意义 → 返回 SHARED
        （并记日志，不抛错，T01 ⑤）。
        """
        mode = normalize_output_mode(
            getattr(self.settings, "playback_output_mode", "shared")
        )
        if mode is OutputMode.SHARED and bool(
            getattr(self.settings, "playback_exclusive", False)
        ):
            mode = OutputMode.EXCLUSIVE
        if not mode.is_exclusive:
            self._mode_downgrade_notified = None
            return OutputMode.SHARED
        device = self._resolve_device()
        backend = (
            device.backend_key
            if device is not None
            else str(getattr(self.settings, "playback_output_backend", "") or "")
        )
        if backend and backend != BACKEND_WASAPI:
            # 同一 (后端, 请求模式) 只提示一次：这是查询函数，snapshot() 每秒调多次，
            # 每次都 log 会把运行日志刷成同一句话（用户截图反馈）。
            notice_key = (str(backend), mode.value)
            if self._mode_downgrade_notified != notice_key:
                self._mode_downgrade_notified = notice_key
                log.info("当前后端 %r 不支持独占，按共享模式播放", backend)
            return OutputMode.SHARED
        self._mode_downgrade_notified = None
        return mode

    def set_output_mode(self, mode: OutputMode | str) -> OutputMode:
        """切换输出模式（设置面板保存后调用；正在播则重连）。"""
        value = normalize_output_mode(mode)
        self.settings.playback_output_mode = value.value
        self.settings.playback_exclusive = value.is_exclusive
        self._device_skip_levels.clear()
        self._device_refused.clear()
        self._open_failures.clear()
        if self._item is not None and self._state in (PlayerState.PLAYING, PlayerState.PAUSED):
            self.reconnect()
        return value

    def output_mode(self) -> OutputMode:
        """当前（下次起播会用的）输出模式。"""
        return self._output_mode()

    def _apply_strict_constraints(self, spec: DecodeSpec) -> DecodeSpec:
        """严格独占的连带约束（Q6）：强制整数通路 + DSP 全旁路。

        为什么是强绑定：``explicit_sample_format=True`` 只接受整数格式（实测
        float32 直接 -9997）；而"未加软件增益"要成立，音量和 EQ 必须真的不动样本。
        这不是产品口味问题，是话术成立的前提。
        """
        if not self._plan or not self._plan.mode.explicit_format:
            self._strict_locked = False
            return spec
        self._strict_locked = True
        if spec.sample_fmt not in ("s16", "s24", "s32"):
            # Q11 拍板：严格模式**统一进 int32 容器**，不为 int16 源单独开分支
            # （与 QA 严格列只用 int32 的口径一致；16bit 样本左移 8 位无损）。
            int_fmt = "s32"
            log.info(
                "严格独占要求整数通路：样本格式 %s → %s（DSP/音量强制旁路）",
                spec.sample_fmt, int_fmt,
            )
            spec = replace(spec, sample_fmt=int_fmt)
            self._int_downgraded = False   # 已经强制成整数了，别再标"不可用"
        self._dsp.set_gain_db(0.0)
        self._dsp.set_eq_enabled(False)
        return spec

    def _build_spec(
        self,
        item: QueueItem,
        offset_ms: int,
        *,
        skip_levels: frozenset[int] = frozenset(),
    ) -> DecodeSpec:
        """按队列项 + 偏移构造解码参数（含采样率/声道/样本格式的最终裁决）。"""
        spec = DecodeSpec.from_item(item, self.settings, start_ms=offset_ms)
        settings = self.settings
        # 整数通路与软件处理互斥：判据统一在 dsp.int_pipeline_allowed()，
        # 解码器已经据此选过样本格式；这里只是把「用户要整数、实际给了浮点」
        # 这件事记下来，好在信号路径 format 级如实说明原因（面板不能含糊）。
        requested_int = bool(
            getattr(settings, "playback_int_pipeline", False)
            or getattr(settings, "playback_bit_perfect", False)
        )
        self._int_downgraded = bool(
            requested_int
            and not spec.is_dsd
            and spec.sample_fmt not in ("s16", "s24", "s32")
        )
        if self._int_downgraded:
            log.info(
                "整数通路不可用（DSP 已启用：旁路=%s 音量模式=%s EQ=%s），改用浮点",
                getattr(settings, "playback_dsp_bypass", True),
                getattr(settings, "playback_volume_mode", "bypass"),
                getattr(settings, "playback_eq_enabled", False),
            )
        # 双保险：万一有人塞了一个自带 spec 的工厂，这里仍旧强制浮点
        if spec.sample_fmt in ("s16", "s24", "s32") and not int_pipeline_allowed(
            settings, self._volume_db
        ):
            spec = replace(spec, sample_fmt="f32")
            self._int_downgraded = True
        device = self._resolve_device()
        want = int(spec.sample_rate)
        if not bool(getattr(settings, "playback_follow_source_rate", True)) and not spec.is_dsd:
            device_rate = device.rate if device is not None else 0
            if device_rate > 0:
                want = device_rate
        self._want_rate = int(want)
        mode = self._output_mode()
        try:
            # 一次定案：采样率 + 实际模式 + 回退层级（设计 §4.2「探测一次」）
            plan = devices_mod.plan_output(
                device, want, channels=spec.channels, mode=mode, skip_levels=skip_levels
            )
        except RateUnsupportedError as exc:
            raise PlayerError(str(exc)) from exc
        if self._exclusive_refused_note:
            # 上一轮是"探测说行、开流被拒"降级过来的：把这句写进 plan.note，
            # 面板才能解释"为什么最后是 48000 Hz/共享"，而不是让用户以为设备不支持。
            detail = (
                f"探测通过但开流被拒（已记录），实际改用 {plan.rate} Hz"
                f"（{plan.mode.value}）"
            )
            merged = detail
            plan = replace(plan, note=merged)
            self._exclusive_refused_note = ""
        self._plan = plan
        self._fallback = bool(plan.fallback)
        policy = str(getattr(self.settings, "playback_exclusive_fallback", "auto") or "auto")
        if plan.requested_mode.is_exclusive and (
            plan.mode_fallback or plan.rate_fallback
        ):
            if policy == "never":
                # 用户明确要求「只报错」：不静默降级
                raise PlayerError(
                    "独占无法按原参数打开，且设置为「只报错」：" + (plan.note or "设备不支持")
                )
            log.warning("独占降级：%s", plan.note or "设备不支持原参数")
            self._pending_choice = plan.note if policy == "ask" else ""
        else:
            self._pending_choice = ""
        if plan.rate_fallback:
            self._signal.bump("resample_events", 1)
        if not plan.mode.is_exclusive and mode.is_exclusive:
            log.info("独占不可用，本次按共享模式播放：%s", plan.note)
        spec = replace(spec, sample_rate=int(plan.rate))
        # 关键：解码器按**设备真实率**出 PCM，重采样交给 ffmpeg（避免变调）
        return self._apply_strict_constraints(spec)

    def _dsp_will_process(self) -> bool:
        """这次起播 DSP 是否真的会改样本（= 整数通路是否可行，取反）。"""
        return not int_pipeline_allowed(self.settings, self._volume_db)

    def _plan_key(self, item: QueueItem) -> tuple[int, int, str]:
        """(设备, 请求率, 请求模式) —— 引擎跨起播记忆的键。

        为什么必须带率与模式：dev22 上 192k 独占 ✓ 而 176.4k 独占 ✗，
        说明「某一级能不能开」和请求率强相关；只按设备记会把一次失败的错误
        扩散到其它采样率（静默降级 + 面板谎报重采样）。
        """
        device = self._resolve_device()
        index = device.index if device is not None else -1
        mode = self._output_mode()
        want = int(getattr(item, "sample_rate", 0) or 0)
        if bool(getattr(item, "is_dsd", False)):
            want = int(getattr(self.settings, "playback_dsd_pcm_rate", 0) or 0)
        elif not bool(getattr(self.settings, "playback_follow_source_rate", True)):
            want = int(device.rate) if device is not None else want
        return (index, want, mode.value)

    def _note_open_failure(self, message: str, key: Optional[tuple] = None) -> None:
        """记录一次「开流失败」（= 探测与实际不一致），到阈值就作废该设备缓存。

        设计 §4.2：连续失败 2 次 → 失效重探。不接这条的话，假阳性设备会**每次
        起播**都先按错的 plan 试一遍，用户感受是"每次切歌都卡一下"。
        """
        self._signal.bump("probe_mismatch", 1)
        self._probe_mismatch_total += 1
        device = self._device_info_for_counters()
        index = device.index if device is not None else -1
        bucket = key if key is not None else (index, 0, "")
        count = self._open_failures.get(bucket, 0) + 1
        self._open_failures[bucket] = count
        log.info("输出开流失败计数：%s = %d（%s）", bucket, count, message)
        if count >= 2 and device is not None:
            try:
                devices_mod.probe_cache().invalidate(device)
                log.warning("设备 %s 连续 %d 次开流失败，已作废能力缓存并将在下次重探", index, count)
            except Exception:  # noqa: BLE001
                pass
            self._open_failures[bucket] = 0

    def _note_open_success(self, key: Optional[tuple] = None) -> None:
        if key is not None:
            self._open_failures[key] = 0
        else:
            self._open_failures.clear()

    def _device_info_for_counters(self) -> Optional[DeviceInfo]:
        """当前设备（用于按设备记失败次数）；拿不到就返回 None。"""
        try:
            info = getattr(self._output, "device_info", None)
            if info is not None:
                return info
        except Exception:  # noqa: BLE001
            pass
        return self._resolve_device()

    def _resolve_device(self) -> Optional[DeviceInfo]:
        try:
            return devices_mod.resolve_device(
                str(getattr(self.settings, "playback_device", "") or ""),
                str(getattr(self.settings, "playback_output_backend", "") or ""),
            )
        except NoOutputDeviceError:
            return None

    def _make_decoder(self, spec: DecodeSpec, ring: PcmRingBuffer) -> Any:
        if self._decoder_factory is not None:
            return self._decoder_factory(
                spec, ring, on_error=self._on_decode_error, on_eof=self._on_decode_eof
            )
        return FfmpegDecoder(
            spec, ring=ring, on_error=self._on_decode_error, on_eof=self._on_decode_eof
        )

    def _make_output(self, ring: PcmRingBuffer, device: Optional[DeviceInfo]) -> Any:
        hook = self._hook if str(ring.dtype) == "float32" else None
        if self._output_factory is not None:
            return self._output_factory(ring, device, hook)
        settings = self.settings
        mode = self._plan.mode if self._plan is not None else self._output_mode()
        return AudioOutput(
            ring,
            device=device.index if device is not None else "",
            backend=str(getattr(settings, "playback_output_backend", "") or ""),
            exclusive=mode.is_exclusive,
            mode=mode,
            blocksize=self._block_frames,
            hook=hook,
            # 「缓冲深度」真正接到 PortAudio：这是 latency 建议值（秒），
            # PortAudio 会取本设备最接近的可用深度。以前这个设置只存不用，
            # 用户调了毫无效果。40 ms 是产品定稿的下限（再浅必然欠载）。
            latency=max(40, int(getattr(settings, "playback_buffer_ms", 80) or 80)) / 1000.0,
        )

    def _hook(self, outdata: Any, filled: int) -> None:
        """PortAudio 回调里的 DSP 入口（就地处理前 ``filled`` 帧）。"""
        try:
            if filled > 0:
                self._dsp.process(outdata[:filled])
        except BaseException:  # noqa: BLE001 - 回调里绝不能让异常外泄
            log.exception("DSP 处理异常（已忽略该块）")

    def _start_item(self, item: QueueItem, offset_ms: int = 0) -> bool:
        """起播/续播一首，带**最多 4 次**的独占降级重试（设计 §4.3）。

        为什么必须重试而不是"探测决定一切"：``check_output_settings`` 有假阳性
        （dev18 + 44100 + 独占：探测 ✓、开流 ``-9996``）。探测只用来排序候选，
        **判据是开流成功**。每级只降一次，失败过的 level 放进 ``skip_levels``，
        绝不在同一组参数上原地重试。
        """
        key = self._plan_key(item)
        # 同一 (设备, 率, 模式) 上失败过的层级直接跳过（假阳性设备因此不再每次卡 ~200 ms）。
        # 注意：**别的采样率/模式不受影响**——那是"静默降质 + 面板说谎"的来源。
        skip_levels: set[int] = set(self._device_skip_levels.get(key, set()))
        if skip_levels:
            log.info("参数 %s 上次开流失败过层级 %s，本次直接跳过", key, sorted(skip_levels))
        if self._device_refused.get(key):
            self._exclusive_refused_note = "探测通过但开流被拒（已记录），已自动降级"
        last_message = ""
        for attempt in range(4):
            result = self._start_item_once(item, offset_ms, frozenset(skip_levels))
            if result.ok:
                self._note_open_success(key)
                # 刻意**不**清 skip 集：这台设备"探测说行、开流被拒"是稳定特性，
                # 学会一次就该一直用对的那级（否则每次切歌都要白试 ~200 ms）。
                # 换设备/重连时清空（见 set_output / reconnect）。
                return True
            if not result.retryable:
                return False                     # _start_item_once 里已经 _fail 过
            skip_levels.add(int(result.level))
            self._device_skip_levels.setdefault(key, set()).add(int(result.level))
            self._device_refused[key] = True
            last_message = result.message
            self._note_open_failure(result.message, key)
            log.warning(
                "第 %d 级输出开流失败（%s），降级重试（第 %d/4 次）",
                result.level, result.message, attempt + 2,
            )
            self._exclusive_refused_note = (
                "探测通过但开流被拒（已记录），已自动降级"
            )
        self._fail(f"独占各级开流均失败：{last_message}")
        return False

    def _start_item_once(
        self, item: QueueItem, offset_ms: int = 0, skip_levels: frozenset[int] = frozenset()
    ) -> "_Attempt":
        """按给定 skip 集尝试**一级**：开流 → 预填 → PLAYING。

        返回 ``_Attempt``：``ok`` / ``retryable``（可降级）/ ``level``（失败层级）。
        """
        backend = str(getattr(self.settings, "playback_output_backend", "") or "")
        if backend == "asio" and not devices_mod.backend_present("asio"):
            # 用户手工把后端写成 asio（或从别处同步来的配置）：不崩，给准确原因
            self._fail(
                "当前 PortAudio 构建不含 ASIO：本版未启用 ASIO（需替换 DLL，见 v1.7 说明）"
            )
            return _Attempt(False)
        if not Path(item.path).exists():
            self._fail(f"文件不存在：{item.path}")
            return _Attempt(False)
        previous = self._item
        changed_track = previous is None or previous.key != item.key
        self._item = item
        if changed_track:
            self._emit(EVENT_TRACK, item.to_dict())

        self._set_state(PlayerState.LOADING)
        # **防重叠播放（P0）**：直连起播路径（play(track)/open_track）此前不拆旧
        # 会话就 ``self._output = output`` 覆盖 —— 旧输出流没关、旧解码器还在喂
        # 旧环，两首歌同时出声（用户实测"播另一首，原来的还在播"）。
        # 正常切歌走 _switch_to 已拆过，这里再拆一次是幂等空操作。
        self._fade_out_and_wait()
        self._teardown_playback()
        try:
            spec = self._build_spec(item, offset_ms, skip_levels=skip_levels)
        except (PlayerError, DecodeError) as exc:
            self._fail(str(exc))
            return _Attempt(False)

        device = self._resolve_device()
        ring = PcmRingBuffer.from_seconds(
            RING_SECONDS, spec.sample_rate, spec.channels, str(spec.numpy_dtype)
        )

        # 先开流（不启动回调）再起解码器：把「解码器在写、却没人消费」的窗口
        # 压到一次开流的耗时内，避免慢设备上触到解码线程 500 ms 的停滞阈值。
        # （T03 实测：先起解码器再开流，DSD 那首会丢 480 帧。）
        output = None
        actual_rate = int(spec.sample_rate)
        try:
            output = self._make_output(ring, device)
            if hasattr(output, "prepare"):
                # rate_decided=True：采样率已由 plan_output 一次定案，输出层不得再探
                # （两次探测之间设备可能变化，那正是 v1.6 里 44.1k→48k 抖动的来源）
                mode_arg = self._plan.mode if self._plan is not None else self._output_mode()
                try:
                    actual_rate = int(
                        output.prepare(
                            spec.sample_rate,
                            spec.channels,
                            mode=mode_arg,
                            rate_decided=True,
                        )
                    )
                except TypeError as exc:
                    # 注入的测试替身可能还是 v1.6 的两参数签名：退回去调，
                    # 但只对「不认识这个关键字」放行，别把真错误吞了
                    if "unexpected keyword argument" not in str(exc):
                        raise
                    actual_rate = int(output.prepare(spec.sample_rate, spec.channels))
            else:  # 注入的极简假输出：退回一步到位
                actual_rate = int(output.start(spec.sample_rate, spec.channels))
        except (DeviceBusyError, DeviceLostError, RateUnsupportedError, DeviceError) as exc:
            # 探测说"独占可用"、真开流却失败（dev18 44.1k 独占：探测 ✓、开流 -9996）。
            # 这类假阳性由外层降级循环处理：不在同一级原地重试，只回报"这级失败"。
            self._discard_output(output)
            self._teardown_playback()
            plan = self._plan
            if plan is not None and plan.mode.is_exclusive:
                return _Attempt(False, retryable=True, level=int(plan.level), message=str(exc))
            self._fail(f"打开输出设备失败：{exc}")
            return _Attempt(False)
        except Exception as exc:  # noqa: BLE001 - 未知异常也要转 ERROR 而不是崩
            self._fail(f"打开输出设备失败：{exc}")
            return _Attempt(False)

        actual_mode = (
            output.actual_mode() if hasattr(output, "actual_mode") else None
        )
        if (
            actual_mode is not None
            and self._plan is not None
            and actual_mode is not self._plan.mode
        ):
            # 计划说独占、PortAudio 实际给了共享：必须是"降级"而不是"独占成功"
            log.warning(
                "输出模式实际为 %s（计划 %s），按实际值上报信号路径",
                actual_mode.value, self._plan.mode.value,
            )
            self._plan = replace(self._plan, mode=actual_mode, level=max(4, self._plan.level),
                                 fallback=True,
                                 note=(self._plan.note or "设备未接受独占，已按共享打开"))
            if str(getattr(self.settings, "playback_exclusive_fallback", "auto")) == "never":
                self._discard_output(output)
                self._fail("独占无法生效（设备未接受独占参数），且设置为「只报错」")
                return _Attempt(False)

        if actual_rate != int(spec.sample_rate):
            # 理论上 pick_rate 已裁决过；真出现说明设备在两次查询之间变了，
            # 按实际率重建（宁可重来一次，也不能变调）
            log.warning(
                "设备实际率 %d 与预期 %d 不一致，按实际率重建", actual_rate, spec.sample_rate
            )
            self._discard_output(output)
            spec = replace(spec, sample_rate=int(actual_rate))
            return self._start_item_once(item, offset_ms, skip_levels)

        self._rate = int(spec.sample_rate)
        self._channels = int(spec.channels)
        self._ring = ring
        self._ring_base_ms = int(offset_ms)
        self._spec = spec
        self._eof_pending = False

        try:
            decoder = self._make_decoder(spec, ring)
            decoder.start()
        except (DecodeError, OSError) as exc:
            self._discard_output(output)
            self._fail(f"无法解码：{exc}")
            return _Attempt(False)
        self._decoder = decoder

        prefilled = self._wait_prefill(ring, decoder)
        if not prefilled and not decoder.is_alive() and ring.available == 0:
            # 解码器**干净**退出却没吐数据：最常见的原因是落点已经在真实 EOF
            # 之后（元数据把时长估长了、或用户 seek 到最右端）。把它当成
            # 「这首已经播完」，别用"可能是不支持的格式"误导用户。
            returncode = getattr(decoder, "returncode", None)
            self._discard_output(output)
            self._teardown_playback()
            self._item = None if not self._queue.current() else self._item
            if returncode in (0, None):
                log.info("解码器无数据（落点在音频末尾），按曲末处理：%s", item.display)
                self._error = ""
                self._set_state(PlayerState.IDLE)
                self._emit(EVENT_EOF, None)
                self._emit(EVENT_TRACK, None)
                self._emit(EVENT_POSITION, 0)
                return _Attempt(False)
            self._fail("解码器没有产出任何音频（解码失败）")
            return _Attempt(False)

        # DSP 必须在 begin() **之前**准备好：回调一启动就会调用它
        self._dsp.prepare(self._rate, self._channels, self._block_frames)
        self._apply_settings_to_dsp()
        self._dsp.reset_state()
        self._dsp.arm_fade_in()

        try:
            if hasattr(output, "begin"):
                output.begin()
        except (DeviceBusyError, DeviceLostError, RateUnsupportedError, DeviceError) as exc:
            self._discard_output(output)
            plan = self._plan
            if plan is not None and plan.mode.is_exclusive:
                return _Attempt(False, retryable=True, level=int(plan.level), message=str(exc))
            self._fail(f"启动输出流失败：{exc}")
            return _Attempt(False)
        except Exception as exc:  # noqa: BLE001
            self._discard_output(output)
            self._fail(f"启动输出流失败：{exc}")
            return _Attempt(False)

        self._output = output
        self._configure_signal(spec, device, actual_rate)
        self._set_state(PlayerState.PLAYING)
        self._emit(EVENT_SIGNAL, self._signal.to_dict())
        self._emit(EVENT_POSITION, self.position_ms())
        log.info(
            "开始播放：%s（%d Hz %d ch，偏移 %d ms%s）",
            item.display, self._rate, self._channels, offset_ms,
            "，已回退采样率" if self._fallback else "",
        )
        return _Attempt(True)

    def _discard_output(self, output: Any) -> None:
        """丢掉一个还没接管（或接管失败）的输出对象。"""
        if output is None:
            return
        try:
            output.stop()
        except Exception:  # noqa: BLE001
            pass

    def _wait_prefill(self, ring: PcmRingBuffer, decoder: Any) -> bool:
        """等环里攒够 ``PREFILL_BLOCKS`` 块（或解码已结束）。返回是否攒够。"""
        need = max(1, PREFILL_BLOCKS) * self._block_frames
        deadline = time.monotonic() + 5.0
        while ring.available < need and time.monotonic() < deadline:
            if getattr(decoder, "stopped", False):
                break
            if not decoder.is_alive() and ring.available == 0:
                break
            time.sleep(0.002)
        return ring.available >= need

    def _switch_to(self, item: QueueItem) -> bool:
        """切歌：淡出 → 拆旧 → 起新（同规格也重建，简单可靠优先）。

        实测间隙（``last_gap_ms``）如实记录并抛 ``gap`` 事件：本期不承诺
        「≤5 ms 无缝」，跨规格本来就必须重开流，同规格的无缝续写留到后续版本。
        """
        started_at = time.monotonic()
        self._fade_out_and_wait()
        self._teardown_playback()
        position = self._queue.index_of(item.key)
        if position >= 0:
            self._queue.index = position
        self._emit(EVENT_QUEUE, len(self._queue))
        started = self._start_item(item, 0)
        gap_ms = (time.monotonic() - started_at) * 1000.0
        self._last_gap_ms = gap_ms
        self._signal.counters.last_gap_ms = round(gap_ms, 1)
        if started:
            log.info("切歌实测间隙 %.0f ms → %s", gap_ms, item.display)
        self._emit(EVENT_GAP, round(gap_ms, 1))
        return started

    def _fade_out_and_wait(self) -> None:
        """让回调把斜坡**真的走完**再关流。

        只 ``sleep(ramp_ms)`` 是不够的：回调是 23 ms 一块（44.1k/1024），
        5 ms 的斜坡可能只应用了半块就关流，实测接缝仍有 0.07 的样本跳变。
        所以这里既等斜坡时长，也等 ``ramp.gain`` 归零（回调是唯一写它的地方，
        轮询它就是最准确的「斜坡已生效」信号），并留出一个块周期的余量。
        """
        if self._output is None:
            return
        ramp_ms = max(1, int(getattr(self.settings, "playback_ramp_ms", 5) or 5))
        block_ms = (
            1000.0 * self._block_frames / float(self._rate) if self._rate > 0 else 30.0
        )
        try:
            self._dsp.arm_fade_out()
            deadline = time.monotonic() + (ramp_ms + block_ms * 2.0) / 1000.0
            while time.monotonic() < deadline and self._dsp.ramp.gain > 0.0:
                time.sleep(0.002)
        except Exception:  # noqa: BLE001
            log.debug("斜坡淡出失败（忽略）", exc_info=True)

    def _teardown_playback(self) -> None:
        """拆掉当前解码器与输出流（顺序：先停流，再停解码器）。

        关流用 ``abort``：调用方（切歌/停止/seek/重连）都已经先做过 5 ms 淡出，
        缓冲里剩下的是静音；再等 MME 把 266 ms 静音播完只会让切歌听上去「卡一下」。
        """
        output, self._output = self._output, None
        if output is not None:
            # 关流前把欠载数结转到累计值：否则「切歌后 counters 归零」会骗人，
            # 三首连播的验收也就看不出中途有没有欠载。
            try:
                snapshot = output.snapshot()
                self._underrun_base += int(snapshot.get("underruns", 0) or 0)
            except Exception:  # noqa: BLE001
                pass
        decoder, self._decoder = self._decoder, None
        if output is not None:
            try:
                output.pause()
            except Exception:  # noqa: BLE001
                pass
            try:
                output.stop(abort=True)
            except TypeError:  # 注入的假输出可能没有 abort 参数
                try:
                    output.stop()
                except Exception as exc:  # noqa: BLE001
                    log.debug("关闭输出流失败（忽略）：%s", exc)
            except Exception as exc:  # noqa: BLE001
                log.debug("关闭输出流失败（忽略）：%s", exc)
        if decoder is not None:
            try:
                decoder.stop()
            except Exception as exc:  # noqa: BLE001
                log.debug("停止解码器失败（忽略）：%s", exc)
        self._ring = None
        self._eof_pending = False

    # ------------------------------------------------------------ 解码/输出回调

    def _on_decode_error(self, message: str) -> None:
        """解码线程上报错误（**解码线程**上下文）：只记标志，不迁移状态。"""
        self._error = str(message)
        try:
            self._signal.bump("decode_errors", 1)
        except Exception:  # noqa: BLE001
            pass
        log.warning("解码错误：%s", message)

    def _on_decode_eof(self) -> None:
        """解码线程上报 EOF（**解码线程**上下文）：置标志，由 poll() 处理。"""
        self._eof_pending = True

    # ------------------------------------------------------------ 续播

    def _drain_pending_eof(self) -> None:
        """在 GUI 线程处理「一首播完」：等缓冲排空 → 取下一首 → 自动续播。"""
        if self._state is not PlayerState.PLAYING or self._item is None:
            return
        decoder = self._decoder
        if decoder is None:
            return
        ended = self._eof_pending or bool(getattr(decoder, "at_eof", False))
        if not ended:
            return
        ring = self._ring
        if ring is not None and ring.available > 0:
            return  # 缓冲里还有音频，让它播完，不要提前切歌
        self._eof_pending = False
        self._emit(EVENT_EOF, None)
        item = self._queue.next(auto_advance=True)
        if item is None:
            log.info("队列播完，回到 IDLE")
            self._teardown_playback()
            self._item = None
            self._ring_base_ms = 0
            self._signal = SignalPath.blank()
            self._set_state(PlayerState.IDLE)
            self._emit(EVENT_TRACK, None)
            self._emit(EVENT_POSITION, 0)
            self._emit(EVENT_SIGNAL, self._signal.to_dict())
            return
        self._switch_to(item)

    # ------------------------------------------------------------ 信号路径与计数

    def _configure_signal(
        self, spec: DecodeSpec, device: Optional[DeviceInfo], actual_rate: int
    ) -> None:
        """按真实链路重建 9 级描述（每次开流成功后调用）。"""
        settings = self.settings
        dsp = self._dsp.snapshot()
        output_snapshot: dict[str, Any] = {}
        if self._output is not None:
            try:
                output_snapshot = dict(self._output.snapshot())
            except Exception:  # noqa: BLE001
                output_snapshot = {}
        # v1.7 关键修复：`exclusive` 从此只传**实际值**。
        # 之前这里传的是 settings.playback_exclusive（请求值），于是勾了独占却降级
        # 共享时，面板照样显示"独占" —— 信号路径说谎比不说更糟。
        actual_mode = str(output_snapshot.get("mode", "") or "shared")
        actual_exclusive = bool(output_snapshot.get("exclusive", False))
        mode_label = {
            "shared": "shared",
            "exclusive": "loose",
            "exclusive_strict": "strict",
        }.get(actual_mode, "shared")
        plan = self._plan
        self._signal.configure(
            source=spec.to_display(),
            codec=spec.codec,
            decoder_version="ffmpeg",
            sample_fmt=spec.sample_fmt,
            channels=self._channels,
            sample_rate=self._rate,
            is_dsd=spec.is_dsd,
            gain_db=float(dsp.get("gain_db", 0.0)),
            gain_active=bool(dsp.get("gain_active", False)),
            eq_enabled=bool(dsp.get("eq_enabled", False)),
            eq_active=bool(dsp.get("eq_active", False)),
            eq_bands=len(dsp.get("eq_gains", []) or []),
            ramp_ms=int(getattr(settings, "playback_ramp_ms", 5) or 5),
            crossfade_sec=float(getattr(settings, "playback_crossfade_sec", 0.0) or 0.0),
            gapless=bool(getattr(settings, "playback_gapless", True)),
            rate_want=int(self._want_rate or spec.sample_rate),
            rate_actual=int(actual_rate),
            downmix_from=0,
            backend=str(getattr(settings, "playback_output_backend", "") or ""),
            device=output_snapshot.get("device", "") or (device.name if device else ""),
            exclusive=actual_exclusive,
            exclusive_actual=actual_exclusive,
            mode_label=mode_label,
            system_mix_rate=int(
                output_snapshot.get("system_mix_rate", 0)
                or (device.rate if device is not None else 0)
            ),
            mode_note=str(
                getattr(plan, "note", "")
                or output_snapshot.get("mode_note", "")
                or ""
            ),
            exclusive_fallback=bool(getattr(plan, "fallback", False)),
            exclusive_refused=bool(
                self._exclusive_refused_note
                or (
                    getattr(plan, "note", "")
                    and "不支持独占" in str(getattr(plan, "note", ""))
                )
            ),
            latency_ms=float(output_snapshot.get("latency_ms", 0.0) or 0.0),
            device_dtype=str(output_snapshot.get("dtype", "") or ""),
            int_pipeline=spec.sample_fmt in ("s16", "s24", "s32"),
            format_note=(
                TEXT_INT_PIPELINE_UNAVAILABLE if self._int_downgraded else ""
            ),
            effective_bit_depth=int(getattr(self._item, "bit_depth", 0) or 0),
        )
        # 回退时 counters 里补一笔（引擎在 _build_spec 里已 bump，这里不重复加）
        self._sync_counters(output_snapshot)

    def _refresh_signal(self) -> None:
        """音量/EQ 变化后局部刷新信号路径（不重开流）。"""
        dsp = self._dsp.snapshot()
        if bool(dsp.get("eq_enabled")):
            bands = len(dsp.get("eq_gains", []) or [])
            eq_active = bool(dsp.get("eq_active"))
            self._signal.mark(
                "eq",
                StageStatus.CHANGED if eq_active else StageStatus.PASSTHROUGH,
                f"{bands} 段 · 峰值 ±12 dB" if eq_active else "全平直（0 dB）",
            )
        else:
            self._signal.mark("eq", StageStatus.BYPASSED, "旁路")
        if bool(dsp.get("gain_active")) and abs(float(dsp.get("gain_db", 0.0))) > 1e-9:
            self._signal.mark("gain", StageStatus.CHANGED, f"{float(dsp['gain_db']):+.1f} dB 浮点乘法")
        else:
            self._signal.mark("gain", StageStatus.BYPASSED, "0 dB 旁路（无软件增益）")
        self._signal.counters.gain_db = float(dsp.get("gain_db", 0.0))
        self._emit(EVENT_SIGNAL, self._signal.to_dict())

    def _sync_counters(self, output_snapshot: dict[str, Any]) -> None:
        """把输出/解码侧的计数搬进信号路径（快照与轮询都会调）。"""
        if output_snapshot:
            current = int(output_snapshot.get("underruns", 0) or 0)
            total = self._underrun_base + current
            delta = current - self._underrun_seen
            self._underrun_seen = current
            self._signal.counters.underruns = total
            if delta >= 20:
                log.warning(
                    "连续欠载 %d 次，缓冲可能偏小（当前 %s ms）",
                    delta, getattr(self.settings, "playback_buffer_ms", "?"),
                )
        decoder = self._decoder
        if decoder is not None:
            try:
                self._signal.counters.dropped_frames = int(
                    getattr(decoder, "dropped_frames", 0) or 0
                )
            except Exception:  # noqa: BLE001
                pass
        self._signal.counters.gain_db = float(self._dsp.gain.db)
        self._signal.counters.probe_mismatch = self._probe_mismatch_total

    # ------------------------------------------------------------ 错误

    def _fail(self, message: str) -> None:
        """进入 ERROR：拆掉流、记录原因、通知界面。"""
        self._error = str(message)
        log.error("播放失败：%s", message)
        self._teardown_playback()
        self._signal.mark("output", StageStatus.ERROR, self._error)
        self._set_state(PlayerState.ERROR)
        self._emit(EVENT_ERROR, self._error)
        self._emit(EVENT_SIGNAL, self._signal.to_dict())


def snapshot_json(engine: PlayerEngine) -> str:
    """调试用：把引擎快照序列化成 JSON 文本（验证可序列化的便利函数）。"""
    return json.dumps(engine.snapshot(), ensure_ascii=False)


__all__ = [
    "PlayerEngine",
    "PlayerSnapshot",
    "PlayerError",
    "ALLOWED_TRANSITIONS",
    "EVENT_EOF",
    "EVENT_VOLUME",
    "snapshot_json",
]
