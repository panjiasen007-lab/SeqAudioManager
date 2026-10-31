"""后台线程：把纯 Python 的 Processor / 扫描器接到 Qt 信号上，保证 UI 不阻塞。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from ..core.model import Track
from ..core.probe import probe, scan_folder
from ..core.report import RunSummary
from ..core.config import Settings
from ..pipeline.processor import BatchOptions, Processor

log = logging.getLogger(__name__)


class ScanWorker(QThread):
    """目录扫描 + 单文件探测。上千条文件也不会卡死界面。"""

    discovered = Signal(int)          # 已扫描数量（流式反馈）
    finishedScan = Signal(object)     # list[Track]
    failed = Signal(str)

    def __init__(self, paths: list[Path], recursive: bool = True, parent=None):
        super().__init__(parent)
        self.paths = paths
        self.recursive = recursive

    def run(self) -> None:
        tracks: list[Track] = []
        try:
            for path in self.paths:
                if self.isInterruptionRequested():
                    break
                if path.is_dir():
                    found = scan_folder(path, recursive=self.recursive)
                    tracks.extend(found)
                    self.discovered.emit(len(tracks))
                elif path.is_file():
                    tracks.append(probe(path))
                    self.discovered.emit(len(tracks))
            self.finishedScan.emit(tracks)
        except Exception as exc:  # noqa: BLE001
            log.exception("扫描失败")
            self.failed.emit(str(exc))


class ProcessorWorker(QThread):
    """批处理线程。所有跨线程交互都通过 Signal，避免直接碰 UI 对象。"""

    trackUpdated = Signal(object)
    progressChanged = Signal(int, int)
    writeProgress = Signal(int, int)      # 第二阶段（落盘）进度
    coverProgress = Signal(int, int)      # 第五阶段（文件夹封面）进度
    coverSummary = Signal(object)         # CoverSummary
    phaseChanged = Signal(str)            # 当前阶段名
    logMessage = Signal(str, str)
    finishedRun = Signal(object)
    startedRun = Signal(int)

    def __init__(
        self,
        settings: Settings,
        tracks: list[Track],
        options: Optional[BatchOptions] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.settings = settings
        self.tracks = tracks
        self.options = options or BatchOptions()
        self._processor: Optional[Processor] = None

    def run(self) -> None:
        self._processor = Processor(self.settings, on_event=self._on_event)
        try:
            summary = self._processor.run(self.tracks, self.options)
            self.finishedRun.emit(summary)
        except Exception as exc:  # noqa: BLE001
            log.exception("批处理异常")
            self.logMessage.emit("ERROR", f"批处理中断：{exc}")
            self.finishedRun.emit(RunSummary.of(self.tracks))

    def _on_event(self, kind: str, payload: object) -> None:
        if kind == "track":
            self.trackUpdated.emit(payload)
        elif kind == "progress":
            index, total = payload  # type: ignore[misc]
            self.progressChanged.emit(int(index), int(total))
        elif kind == "write_progress":
            index, total = payload  # type: ignore[misc]
            self.writeProgress.emit(int(index), int(total))
        elif kind == "cover_progress":
            index, total = payload  # type: ignore[misc]
            self.coverProgress.emit(int(index), int(total))
        elif kind == "cover_summary":
            self.coverSummary.emit(payload)
        elif kind == "phase":
            self.phaseChanged.emit(str(payload))
        elif kind == "start":
            self.startedRun.emit(int(payload))  # type: ignore[arg-type]
        elif kind == "log":
            level, message = payload  # type: ignore[misc]
            self.logMessage.emit(str(level), str(message))
        elif kind == "finish":
            summary = payload
            if isinstance(summary, RunSummary):
                self.logMessage.emit("INFO", summary.text())

    def cancel(self) -> None:
        if self._processor is not None:
            self._processor.cancel()


class MatchPreviewWorker(QThread):
    """单曲候选检索（手动搜索按钮 / 右键「重新匹配」）。"""

    finishedMatch = Signal(object, object)   # (track, MatchResult)
    failedMatch = Signal(object, str)

    def __init__(self, settings: Settings, track: Track, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.track = track

    def run(self) -> None:
        try:
            processor = Processor(self.settings)
            matcher = processor.matcher()
            result = matcher.match(self.track, need_lyrics=False)
            self.finishedMatch.emit(self.track, result)
        except Exception as exc:  # noqa: BLE001
            log.exception("匹配失败")
            self.failedMatch.emit(self.track, str(exc))


class AuthenticityWorker(QThread):
    """批量真假无损鉴定。

    每个文件要跑 4 次 ffmpeg 解码 + FFT，单线程跑一千首太慢。
    ffmpeg 是子进程，用线程池并发即可（numpy 的 FFT 也会释放 GIL）。
    """

    trackDone = Signal(object, object)     # (track, AuthenticityResult)
    progressChanged = Signal(int, int)
    finishedAll = Signal(int, int, int)    # (真无损, 可疑, 无法判定)
    failed = Signal(str)

    def __init__(self, tracks: list[Track], workers: int = 4, parent=None):
        super().__init__(parent)
        self.tracks = list(tracks)
        self.workers = max(1, workers)

    def run(self) -> None:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        from ..core.authenticity import AuthenticityResult, analyze

        total = len(self.tracks)
        if total == 0:
            self.finishedAll.emit(0, 0, 0)
            return

        true_count = suspect = unknown = 0
        try:
            with ThreadPoolExecutor(max_workers=self.workers) as pool:
                futures = {
                    pool.submit(analyze, t.path, t.info): t for t in self.tracks
                }
                for index, future in enumerate(as_completed(futures), start=1):
                    track = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:  # noqa: BLE001
                        log.exception("鉴定异常：%s", track.path)
                        result = AuthenticityResult(error=f"分析异常：{exc}")
                    self.trackDone.emit(track, result)
                    if result.verdict == "无法判定":
                        unknown += 1
                    elif result.is_suspicious:
                        suspect += 1
                    else:
                        true_count += 1
                    self.progressChanged.emit(index, total)
                    if self.isInterruptionRequested():
                        for pending in futures:
                            pending.cancel()
                        break
        except Exception as exc:  # noqa: BLE001
            log.exception("批量鉴定失败")
            self.failed.emit(str(exc))
        self.finishedAll.emit(true_count, suspect, unknown)


class CoverWorker(QThread):
    """独立的「生成文件夹封面」操作：为每个文件夹确保一张 cover。"""

    progressChanged = Signal(int, int)
    summaryReady = Signal(object)         # CoverSummary
    logMessage = Signal(str, str)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, settings: Settings, tracks: list[Track], parent=None):
        super().__init__(parent)
        self.settings = settings
        self.tracks = list(tracks)
        self._cancel_event = None

    def run(self) -> None:
        import threading

        from ..core.model import TrackStatus
        from ..net.http import DiskCache, HttpClient
        from ..pipeline.covers import ensure_folder_covers

        cancel = threading.Event()
        self._cancel_event = cancel

        client = HttpClient(
            proxy=self.settings.http_proxy,
            timeout=self.settings.request_timeout,
            concurrency=self.settings.concurrency,
            cache=DiskCache(ttl_days=self.settings.cache_ttl_days),
            offline=self.settings.offline_mode,
        )
        targets = [
            t for t in self.tracks
            if t.status is not TrackStatus.UNSUPPORTED and t.path.exists()
        ]
        try:
            summary = ensure_folder_covers(
                targets,
                client,
                use_itunes=self.settings.use_itunes,
                use_netmusic=self.settings.use_netmusic,
                use_deezer=True,
                on_progress=lambda i, n, folder: self.progressChanged.emit(i, n),
                cancel=cancel,
            )
            self.summaryReady.emit(summary)
            self.logMessage.emit("INFO", summary.text())
        except Exception as exc:  # noqa: BLE001
            log.exception("文件夹封面生成失败")
            self.failed.emit(str(exc))
        self.finished.emit()

    def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
