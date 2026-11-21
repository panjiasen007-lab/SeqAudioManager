"""播放引擎与 Qt 的唯一通道：命令转发 + 信号发射 + 100 ms 位置轮询。

为什么只允许这一个 UI 文件 import ``seqaudio.player``
------------------------------------------------------
架构文档 2.3 的依赖方向是 ``ui/player_*.py → player/*`` 单向。如果每个面板
各自 ``import`` 引擎，就会出现「谁都能改引擎状态」的局面，而引擎的
「单一状态写入者」原则（10.4 第 4 条）立刻失效。所以这里把引擎包成
``PlayerBridge``，其他界面文件只认本类的信号与方法。

线程纪律（架构文档 5.2）
------------------------
引擎自己不起线程，但**解码线程/PortAudio 回调**随时可能触发回调。若在非 GUI
线程里直接 ``emit`` 到界面控件，Qt 会在随机线程上改控件 → 随机崩溃。因此
``_on_engine_event`` 一律先判断线程身份：GUI 线程直接派发，其他线程通过
``QMetaObject.invokeMethod(..., Qt.QueuedConnection)``（失败退回队列连接信号）
投回 GUI 线程，再 emit。

会话为什么要经过这里
--------------------
``player/session.py`` 属于内核，``main_window`` 不允许 import。所以
``save_session()`` / ``restore_session()`` 放在本类里，主窗口只拿到一个普通
字典作为回执 —— 依赖方向仍然单向。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QThread, Qt, QTimer, Signal, Slot

from ..core.config import Settings
from ..player import PlayerEngine, PlayMode, PlayerState
from ..player.constants import (
    EVENT_ERROR,
    EVENT_GAP,
    EVENT_POSITION,
    EVENT_QUEUE,
    EVENT_SIGNAL,
    EVENT_STATE,
    EVENT_TRACK,
    POSITION_POLL_MS,
)
# eof / volume 两个事件名定义在 engine.py（constants 里没有），跟着源头走
from ..player.engine import EVENT_EOF, EVENT_VOLUME
from ..player.queue import PlayQueue, QueueItem
from ..player.session import PlaybackSession

log = logging.getLogger(__name__)


class PlayerBridge(QObject):
    """引擎门面：把 ``on_event(kind, payload)`` 转成 Qt 信号，跨线程安全。

    Signals:
        stateChanged(str): 播放状态（``IDLE`` / ``LOADING`` / ``PLAYING`` /
            ``PAUSED`` / ``ERROR``，与原枚举值同源，展示文案由 i18n 负责）。
        trackChanged(object): 当前曲目字典（``QueueItem.to_dict()``）或 ``None``。
        positionChanged(int): 曲目内位置（毫秒）。
        durationChanged(int): 曲目总长（毫秒），变化时才发。
        errorRaised(str): 播放相关错误文案（已可直接写日志）。
        eofReached(): 当前曲目自然结束（自动续播由 ``poll()`` 内部完成）。
        volumeChanged(float): 软件音量（dB）。
        signalPathChanged(object): 信号路径 9 级快照（``list[dict]``）。
        queueChanged(int): 队列长度变化。
        gapDetected(float): 切歌间隙（毫秒）。
        busyChanged(bool): 批处理互斥状态（``True`` 时拒绝起播）。
    """

    stateChanged = Signal(str)
    trackChanged = Signal(object)
    positionChanged = Signal(int)
    durationChanged = Signal(int)
    errorRaised = Signal(str)
    eofReached = Signal()
    volumeChanged = Signal(float)
    signalPathChanged = Signal(object)
    queueChanged = Signal(int)
    gapDetected = Signal(float)
    busyChanged = Signal(bool)

    #: 跨线程投递的兜底通道（``invokeMethod`` 在某些 Python 对象上不可用）
    _relay = Signal(str, object)

    def __init__(self, settings: Settings, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.settings = settings or Settings()
        self._engine = PlayerEngine(self.settings, on_event=self._on_engine_event)
        self._alive = True
        self._shut_down = False
        self._last_duration = -1
        self._last_busy = False
        self._last_snapshot: dict[str, Any] = {}
        #: 会话恢复但未播放时的待续位置（毫秒）；首次 play 时用掉
        self._resume_ms = 0
        #: 队列长度镜像：面板刷新时不必每次都问引擎要快照
        self._last_queue_len = 0

        self._relay.connect(self._deliver, Qt.QueuedConnection)

        self._timer = QTimer(self)
        interval = int(POSITION_POLL_MS or 100)
        self._timer.setInterval(interval if interval > 0 else 100)
        self._timer.setTimerType(Qt.CoarseTimer)
        self._timer.timeout.connect(self._poll)

        self._engine.on_error = None  # 统一走 on_event，避免双份回调
        self._last_busy = bool(getattr(self._engine, "busy", False))

    # ------------------------------------------------------------ 只读

    @property
    def engine(self) -> PlayerEngine:
        """引擎本体（主窗口只允许调用它的命令方法，不改其内部状态字段）。"""
        return self._engine

    @property
    def timer(self) -> QTimer:
        return self._timer

    def state(self) -> str:
        return str(getattr(self._engine.state, "value", self._engine.state))

    def last_snapshot(self) -> dict[str, Any]:
        """最近一次 ``poll()`` 的完整快照（信号面板/队列面板复用，避免重复计算）。"""
        return self._last_snapshot

    def queue_items(self) -> list[dict[str, Any]]:
        """当前队列的可序列化快照（``QueueItem.to_dict()`` 列表）。"""
        try:
            return list(self._engine.queue.snapshot())
        except Exception:  # noqa: BLE001 - 读队列失败不该弹出异常
            log.exception("读取队列快照失败")
            return []

    def queue_index(self) -> int:
        try:
            return int(self._engine.queue.index)
        except Exception:  # noqa: BLE001
            return -1

    def pending_resume_ms(self) -> int:
        return int(self._resume_ms)

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """开始轮询（窗口显示时调用）。"""
        if not self._shut_down and not self._timer.isActive():
            self._timer.start()

    def stop(self) -> None:
        """暂停轮询（窗口隐藏时调用；不影响正在播的音频）。"""
        if self._timer.isActive():
            self._timer.stop()

    def shutdown(self) -> None:
        """退出：停 timer → 关流 → 断信号。重复调用安全。"""
        if self._shut_down:
            return
        self._shut_down = True
        self._alive = False
        try:
            self._timer.stop()
            self._timer.timeout.disconnect(self._poll)
        except (RuntimeError, TypeError):
            pass
        try:
            self._engine.shutdown()
        except Exception:  # noqa: BLE001 - 退出路径绝不能再抛
            log.exception("关闭播放引擎失败")
        # 断信号：主窗口可能已经销毁，逐个断并吞掉 "Failed to disconnect(None)"
        # 的 RuntimeWarning（PySide6 在无接收者时会警告而不是抛异常）
        import warnings

        for name in (
            "stateChanged",
            "trackChanged",
            "positionChanged",
            "durationChanged",
            "errorRaised",
            "eofReached",
            "volumeChanged",
            "signalPathChanged",
            "queueChanged",
            "gapDetected",
            "busyChanged",
        ):
            signal = getattr(self, name, None)
            if signal is None:
                continue
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    signal.disconnect()
                except (RuntimeError, TypeError):
                    pass

    # ------------------------------------------------------------ 事件入口

    def _on_engine_event(self, kind: str, payload: Any) -> None:
        """引擎回调入口。**可能运行在解码线程**，必须投回 GUI 线程再 emit。"""
        if not self._alive:
            return
        if QThread.currentThread() is self.thread():
            self._deliver(kind, payload)
            return
        self._post_to_gui(kind, payload)

    def _post_to_gui(self, kind: str, payload: Any) -> None:
        """把事件从工作线程投回 GUI 线程（先 invokeMethod，失败退回队列信号）。"""
        try:
            QMetaObject.invokeMethod(
                self,
                "_deliver_queued",
                Qt.QueuedConnection,
                Q_ARG(str, str(kind)),
                Q_ARG(object, payload),
            )
            return
        except (TypeError, RuntimeError, ValueError) as exc:
            log.debug("invokeMethod 投递失败（%s），改用队列连接信号", exc)
        try:
            self._relay.emit(str(kind), payload)
        except RuntimeError:  # pragma: no cover - 对象已析构
            log.debug("桥接对象已销毁，丢弃事件 %s", kind)

    @Slot(str, object)
    def _deliver_queued(self, kind: str, payload: Any) -> None:
        """``invokeMethod`` 的落点（GUI 线程）。"""
        self._deliver(kind, payload)

    def _deliver(self, kind: str, payload: Any) -> None:
        """把引擎事件翻译成 Qt 信号（只在 GUI 线程执行）。"""
        if not self._alive:
            return
        try:
            if kind == EVENT_STATE:
                self.stateChanged.emit(self._state_text(payload))
            elif kind == EVENT_TRACK:
                data = payload if isinstance(payload, dict) else None
                self.trackChanged.emit(data)
                duration = int((data or {}).get("duration_ms") or 0)
                if duration <= 0 and data:
                    duration = int(round(float(data.get("duration") or 0.0) * 1000.0))
                if duration != self._last_duration:
                    self._last_duration = duration
                    self.durationChanged.emit(duration)
            elif kind == EVENT_POSITION:
                self.positionChanged.emit(int(payload or 0))
            elif kind == EVENT_SIGNAL:
                self.signalPathChanged.emit(payload)
            elif kind == EVENT_ERROR:
                self.errorRaised.emit(str(payload or ""))
            elif kind == EVENT_EOF:
                self.eofReached.emit()
            elif kind == EVENT_VOLUME:
                self.volumeChanged.emit(float(payload or 0.0))
            elif kind == EVENT_QUEUE:
                length = int(payload or 0)
                self._last_queue_len = length
                self.queueChanged.emit(length)
            elif kind == EVENT_GAP:
                self.gapDetected.emit(float(payload or 0.0))
            else:  # pragma: no cover - 引擎新增事件时只记日志
                log.debug("未知播放事件：%s", kind)
        except RuntimeError:  # pragma: no cover - 控件已销毁
            log.debug("信号发射失败（对象可能已销毁）：%s", kind)

    @staticmethod
    def _state_text(payload: Any) -> str:
        """状态载荷归一成字符串（枚举取 ``.value``，字符串原样用）。"""
        if isinstance(payload, PlayerState):
            return payload.value
        value = getattr(payload, "value", payload)
        return str(value)

    # ------------------------------------------------------------ 轮询

    def _poll(self) -> None:
        """每 100 ms 调一次 ``engine.poll()``：续播、位置、计数器、忙碌态。"""
        if self._shut_down:
            return
        try:
            snapshot = self._engine.poll()
        except Exception as exc:  # noqa: BLE001 - 轮询异常不能拖垮界面
            log.exception("播放轮询失败")
            self.errorRaised.emit(f"播放轮询失败：{exc}")
            return
        if not isinstance(snapshot, dict):
            snapshot = {}
        self._last_snapshot = snapshot
        duration = int(snapshot.get("duration_ms") or 0)
        if duration != self._last_duration:
            self._last_duration = duration
            self.durationChanged.emit(duration)
        busy = bool(snapshot.get("busy", False))
        if busy != self._last_busy:
            self._last_busy = busy
            self.busyChanged.emit(busy)

    def poll_now(self) -> dict[str, Any]:
        """手动触发一次轮询（测试与「立刻刷新」用）。"""
        self._poll()
        return self._last_snapshot

    # ------------------------------------------------------------ 命令转发

    def _guard(self, action: str) -> Optional[str]:
        """起播类命令的公共前置检查，返回拒绝原因（``None`` 表示放行）。"""
        if self._shut_down:
            return "播放器已关闭"
        if not bool(getattr(self.settings, "playback_enabled", True)):
            return "播放功能已关闭"
        if bool(getattr(self._engine, "busy", False)):
            return "批处理进行中，暂不能开始播放"
        log.debug("播放命令放行：%s", action)
        return None

    def _safe(self, action: str, callback: Callable[[], Any], default: Any = None) -> Any:
        """执行引擎命令；异常转成 ``errorRaised`` 信号，绝不抛给界面。"""
        if self._shut_down:
            return default
        try:
            return callback()
        except Exception as exc:  # noqa: BLE001 - 播放失败不该带着界面一起崩
            log.exception("播放命令失败：%s", action)
            self.errorRaised.emit(f"{action}失败：{exc}")
            return default

    # ---- 起播 / 传输控制 ----

    def play_track(self, track: Any, start_ms: int = 0) -> bool:
        """起播指定曲目（``Track`` 或 ``QueueItem``）。CUE 子轨用 ``start_ms``。"""
        reason = self._guard("起播")
        if reason is not None:
            self.errorRaised.emit(reason)
            return False
        offset = int(start_ms or 0)
        if offset > 0:
            return bool(
                self._safe(
                    "起播",
                    lambda: self._engine.open_track(track, start_ms=offset),
                    False,
                )
            )
        return bool(self._safe("起播", lambda: self._engine.play(track), False))

    def play_item(self, track: Any, start_ms: int = 0) -> bool:
        """``play_track`` 的别名（架构文档 2.1 第 14 行的命名）。"""
        return self.play_track(track, start_ms)

    def play_current(self) -> bool:
        """播放当前队列项 / 从暂停恢复。会话恢复的位置在这里被用掉。"""
        reason = self._guard("播放")
        if reason is not None:
            self.errorRaised.emit(reason)
            return False
        state = self._engine.state
        if state is PlayerState.IDLE and self._resume_ms > 0:
            item = self._engine.current_item or self._engine.queue.current()
            offset = int(self._resume_ms)
            self._resume_ms = 0
            if item is not None:
                log.info("从上次位置继续：%s（%d ms）", item.display, offset)
                return bool(
                    self._safe(
                        "续播",
                        lambda: self._engine.open_track(item, start_ms=offset),
                        False,
                    )
                )
        return bool(self._safe("播放", lambda: self._engine.play(), False))

    def toggle(self) -> bool:
        """播放/暂停切换（按钮与空格键共用，幂等）。"""
        state = self._engine.state
        if state is PlayerState.PLAYING:
            return self.pause()
        return self.play_current()

    def pause(self) -> bool:
        return bool(self._safe("暂停", lambda: self._engine.pause(), False))

    def stop(self) -> bool:
        return bool(self._safe("停止", lambda: self._engine.stop(), False))

    def next(self) -> bool:
        reason = self._guard("下一首")
        if reason is not None:
            self.errorRaised.emit(reason)
            return False
        return bool(self._safe("下一首", lambda: self._engine.next(), False))

    def prev(self) -> bool:
        reason = self._guard("上一首")
        if reason is not None:
            self.errorRaised.emit(reason)
            return False
        return bool(self._safe("上一首", lambda: self._engine.previous(), False))

    def seek(self, ms: int) -> bool:
        """定位（拖进度条松手时调用）。"""
        target = max(0, int(ms or 0))
        return bool(self._safe("定位", lambda: self._engine.seek(target), False))

    def set_volume(self, db: float) -> float:
        """设置软件音量（dB）；旁路模式下只记录不应用（引擎内部判断）。"""
        value = float(db or 0.0)
        result = self._safe("设置音量", lambda: self._engine.set_volume(value), value)
        return float(result if result is not None else value)

    def set_volume_db(self, db: float) -> float:
        """``set_volume`` 的别名（架构文档 2.1 第 14 行的命名）。"""
        return self.set_volume(db)

    def set_mode(self, mode: Any) -> str:
        """切换播放模式（``PlayMode`` 或字符串键），返回生效后的字符串键。"""
        result = self._safe("切换播放模式", lambda: self._engine.set_mode(mode), None)
        value = getattr(result, "value", result)
        if value is None:
            value = getattr(self._engine.queue.mode, "value", str(self._engine.queue.mode))
        self.queueChanged.emit(len(self._engine.queue))
        return str(value)

    def set_eq_gains(self, gains: list[float]) -> None:
        self._safe("设置均衡器", lambda: self._engine.set_eq_gains(list(gains)))

    def set_eq_enabled(self, enabled: bool) -> None:
        self._safe("切换均衡器", lambda: self._engine.set_eq_enabled(bool(enabled)))

    def set_bypass(self, on: bool) -> None:
        self._safe("切换直通", lambda: self._engine.set_bypass(bool(on)))

    def set_output(
        self,
        device: Optional[str] = None,
        backend: Optional[str] = None,
        exclusive: Optional[bool] = None,
    ) -> None:
        """改输出设备/后端/独占（下次起播生效；正在播则自动重连）。"""
        self._safe(
            "切换输出设备",
            lambda: self._engine.set_output(device=device, backend=backend, exclusive=exclusive),
        )

    def set_output_mode(self, mode: str) -> str:
        """切换输出模式（v1.7：shared / exclusive / exclusive_strict）。

        引擎会同时把 ``settings.playback_output_mode`` 与布尔镜像写回去；
        正在播则自动重连（设备/率可能一起变）。
        """
        value = self._safe(
            "切换输出模式", lambda: self._engine.set_output_mode(str(mode)), str(mode)
        )
        return str(getattr(value, "value", value))

    def reconnect(self) -> bool:
        return bool(self._safe("重新连接设备", lambda: self._engine.reconnect(), False))

    def set_busy(self, busy: bool) -> None:
        """批处理互斥：``True`` 时拒绝新的起播请求（已播曲目继续播）。"""
        if self._shut_down:
            return
        self._safe("设置忙碌状态", lambda: self._engine.set_busy(bool(busy)))
        if bool(busy) != self._last_busy:
            self._last_busy = bool(busy)
            self.busyChanged.emit(bool(busy))

    def set_enabled(self, enabled: bool) -> None:
        """播放功能总开关（架构文档 2.1 第 14 行的命名）。

        关闭时等价于「一直忙」：起播被拒，但不会打断正在播的曲目。
        """
        self.settings.playback_enabled = bool(enabled)
        self.set_busy(not bool(enabled))

    # ---- 队列 ----

    def enqueue(self, tracks: list[Any], replace: bool = False) -> int:
        """把曲目加进队列（``replace=True`` 时整队替换），返回新队列长度。

        入参可以是 ``Track`` / ``QueueItem`` / ``QueueItem.to_dict()`` 的字典 ——
        后者让界面（不允许 import player）也能把队列项搬来搬去。
        """
        def _run() -> int:
            queue = self._engine.queue
            converted = [self._as_queue_item(entry) for entry in tracks]
            if replace:
                queue.load([item for item in converted if item is not None])
            else:
                for item in converted:
                    if item is not None:
                        queue.append(item)
            self._engine.set_queue(queue)
            return len(queue)

        return int(self._safe("加入队列", _run, 0) or 0)

    def play_next(self, track: Any) -> int:
        """「下一首播放」：插到当前项之后，返回新下标（失败 ``-1``）。"""
        def _run() -> int:
            item = self._as_queue_item(track)
            if item is None:
                return -1
            queue = self._engine.queue
            position = queue.insert_after_current(item)
            self._engine.set_queue(queue)
            return int(position)

        result = self._safe("插播", _run, -1)
        return int(result) if result is not None else -1

    @staticmethod
    def _as_queue_item(entry: Any) -> Optional[QueueItem]:
        """把 ``Track`` / ``QueueItem`` / 字典统一成 ``QueueItem``。"""
        if entry is None:
            return None
        if isinstance(entry, QueueItem):
            return entry
        if isinstance(entry, dict):
            return QueueItem.from_dict(entry)
        try:
            return QueueItem.from_track(entry)
        except Exception:  # noqa: BLE001 - 无法投影的项直接丢弃
            log.exception("无法把 %r 转成队列项", type(entry))
            return None

    def play_index(self, index: int) -> bool:
        """跳到队列第 ``index`` 项并起播。"""
        def _run() -> bool:
            queue = self._engine.queue
            item = queue.jump_to(int(index))
            if item is None:
                return False
            return bool(self._engine.play(item))

        return bool(self._safe("跳播", _run, False))

    def remove_index(self, index: int) -> bool:
        """从队列移除一项（当前项被移除且仍在播时由引擎自行处理）。"""
        def _run() -> bool:
            queue = self._engine.queue
            removed = queue.remove_at(int(index))
            self._engine.set_queue(queue)
            return removed is not None

        return bool(self._safe("移除队列项", _run, False))

    def clear_queue(self) -> None:
        """清空队列（先停播，避免停在已不存在的项上）。"""
        def _run() -> None:
            self._safe("停止", lambda: self._engine.stop())
            queue = self._engine.queue
            queue.clear()
            self._engine.set_queue(queue)

        self._safe("清空队列", _run)

    def move_queue_item(self, source: int, target: int) -> bool:
        """拖拽排序用的队列移动（队列面板本轮未启用拖拽，接口先留）。"""
        def _run() -> bool:
            queue = self._engine.queue
            ok = queue.move(int(source), int(target))
            if ok:
                self._engine.set_queue(queue)
            return bool(ok)

        return bool(self._safe("队列排序", _run, False))

    # ---- 设备与快照 ----

    def list_output_devices(self) -> list[dict[str, Any]]:
        """可用输出设备（``ui/settings_panel`` 不 import player，只能经由这里）。"""
        def _run() -> list[dict[str, Any]]:
            from ..player.devices import list_output_devices

            return [device.to_dict() for device in list_output_devices()]

        return list(self._safe("枚举输出设备", _run, []) or [])

    def snapshot(self) -> dict[str, Any]:
        return dict(self._safe("读取播放快照", self._engine.snapshot, {}) or {})

    def signal_snapshot(self) -> list[dict[str, Any]]:
        return list(self._safe("读取信号路径", lambda: self._engine.signal_path.snapshot(), []) or [])

    # ------------------------------------------------------------ 会话

    def save_session(self) -> bool:
        """把「队列 + 位置 + 模式 + 设备 + 音量 + EQ」写进会话文件。"""
        def _run() -> bool:
            session = PlaybackSession.capture(self._engine)
            position = int(self._engine.position_ms())
            if position > 0:
                session.position_ms = position
            return bool(session.save())

        return bool(self._safe("保存播放会话", _run, False))

    def clear_session(self) -> None:
        """删掉会话文件（用户关闭「恢复会话」时用）。"""
        def _run() -> None:
            from ..core.paths import session_path

            try:
                session_path().unlink(missing_ok=True)
            except OSError as exc:  # pragma: no cover - 权限/占用
                log.warning("删除播放会话失败：%s", exc)

        self._safe("清除播放会话", _run)

    def restore_session(self, restore_position: bool = True) -> dict[str, Any]:
        """启动时恢复会话：只恢复队列与位置，**不自动播放**。

        Returns:
            ``{"restored": bool, "count": int, "index": int, "position_ms": int,
            "mode": str, "device": str, "backend": str, "volume_db": float}``
        """
        report: dict[str, Any] = {"restored": False, "count": 0, "index": -1, "position_ms": 0}
        if self._shut_down:
            return report
        session = self._safe("读取播放会话", PlaybackSession.load, None)
        if session is None or session.is_empty():
            return report
        try:
            items: list[QueueItem] = []
            for entry in session.tracks:
                path_text = str((entry or {}).get("path", "") or "").strip()
                if not path_text:
                    continue
                cue = (entry or {}).get("cue_track")
                from pathlib import Path as _Path

                items.append(
                    QueueItem(
                        path=_Path(path_text),
                        cue_track=None if cue is None else int(cue),
                        title=_Path(path_text).stem,
                    )
                )
            if not items:
                return report
            mode = session.mode or PlayMode.SEQUENTIAL.value
            queue = PlayQueue(items, mode=mode)
            index = max(0, min(int(session.index), len(items) - 1))
            queue.index = index
            self._engine.set_queue(queue)
            current = queue.current()
            if current is not None:
                self._engine.open_track(current, autoplay=False)
            if session.volume_db:
                self.settings.playback_volume_db = float(session.volume_db)
                self._engine.set_volume(float(session.volume_db))
            if session.eq_gains:
                self.settings.playback_eq_gains = [float(v) for v in session.eq_gains]
                self._engine.set_eq_gains(self.settings.playback_eq_gains)
            position = int(session.position_ms) if restore_position else 0
            self._resume_ms = max(0, position)
            report.update(
                restored=True,
                count=len(items),
                index=index,
                position_ms=self._resume_ms,
                mode=str(getattr(queue.mode, "value", mode)),
                device=session.output_device,
                backend=session.output_backend,
                paths=[str(item.path) for item in items],
                volume_db=float(session.volume_db),
            )
            log.info(
                "恢复播放会话：%d 项，第 %d 项，位置 %d ms（不自动播放）",
                len(items),
                index + 1,
                self._resume_ms,
            )
        except Exception as exc:  # noqa: BLE001 - 会话坏了也不该拦住启动
            log.exception("恢复播放会话失败")
            report["error"] = str(exc)
        return report


__all__ = ["PlayerBridge"]
