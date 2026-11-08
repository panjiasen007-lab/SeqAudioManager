"""后台线程：把纯 Python 的 Processor / 扫描器接到 Qt 信号上，保证 UI 不阻塞。"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from ..core.model import Track
from ..core.probe import probe, scan_folder, expand_cue_tracks
from ..core.report import RunSummary
from ..core.config import Settings
from ..pipeline.processor import BatchOptions, Processor

log = logging.getLogger(__name__)


class ScanWorker(QThread):
    """目录扫描 + 单文件探测。上千条文件也不会卡死界面。"""

    discovered = Signal(int)          # 已扫描数量（流式反馈）
    finishedScan = Signal(object)     # list[Track]
    failed = Signal(str)

    def __init__(
        self,
        paths: list[Path],
        recursive: bool = True,
        auto_align: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.paths = paths
        self.recursive = recursive
        self.auto_align = auto_align

    def run(self) -> None:
        tracks: list[Track] = []
        try:
            for path in self.paths:
                if self.isInterruptionRequested():
                    break
                if path.is_dir():
                    found = scan_folder(
                        path,
                        recursive=self.recursive,
                        auto_align=self.auto_align,
                        cancel=self.isInterruptionRequested,
                    )
                    tracks.extend(found)
                    self.discovered.emit(len(tracks))
                elif path.is_file():
                    tracks.append(probe(path, auto_align=self.auto_align))
                    self.discovered.emit(len(tracks))
            # 多轨 CUE 整轨展开成分轨，让列表显示所有歌曲
            tracks = expand_cue_tracks(tracks)
            self.finishedScan.emit(tracks)
        except Exception as exc:  # noqa: BLE001
            log.exception("扫描失败")
            self.failed.emit(str(exc))

    def cancel(self) -> None:
        self.requestInterruption()


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

        from ..core.authenticity import (
            AuthenticityResult,
            analyze,
            apply_batch_consensus,
        )

        total = len(self.tracks)
        if total == 0:
            self.finishedAll.emit(0, 0, 0)
            return

        pairs: list[tuple] = []
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
                    pairs.append((track, result))
                    self.trackDone.emit(track, result)
                    self.progressChanged.emit(index, total)
                    if self.isInterruptionRequested():
                        for pending in futures:
                            pending.cancel()
                        break

            # ---- 同批一致性：同一文件夹出处一致，个别漏网的按调制器指纹补判 ----
            revised = apply_batch_consensus([(t.path, r) for t, r in pairs])
            if revised:
                by_path = {str(t.path): t for t, _ in pairs}
                for path, result in revised:
                    track = by_path.get(str(path))
                    if track is not None:
                        self.trackDone.emit(track, result)
                log.info(
                    "同批推定改判 %d 首：%s",
                    len(revised),
                    "、".join(p.name for p, _ in revised[:6]),
                )
        except Exception as exc:  # noqa: BLE001
            log.exception("批量鉴定失败")
            self.failed.emit(str(exc))

        true_count = suspect = unknown = 0
        for _track, result in pairs:
            if result.verdict == "无法判定":
                unknown += 1
            elif result.is_suspicious:
                suspect += 1
            else:
                true_count += 1
        self.finishedAll.emit(true_count, suspect, unknown)

    def cancel(self) -> None:
        self.requestInterruption()


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


class SplitCueWorker(QThread):
    """独立的「CUE 分轨导出」操作：把整轨 WAV/APE/FLAC + CUE 切成独立文件。"""

    progressChanged = Signal(int, int)
    summaryReady = Signal(object)         # SplitSummary
    logMessage = Signal(str, str)
    finished = Signal()
    failed = Signal(str)

    def __init__(self, settings: Settings, tracks: list[Track], output_dir: Path, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.tracks = list(tracks)
        self.output_dir = output_dir
        self._cancel_event = None

    def run(self) -> None:
        import threading

        from ..core.model import TrackStatus
        from ..net.http import DiskCache, HttpClient
        from ..pipeline.cue_split import SplitSummary, split_cue_sheet

        cancel = threading.Event()
        self._cancel_event = cancel

        client = HttpClient(
            proxy=self.settings.http_proxy,
            timeout=self.settings.request_timeout,
            concurrency=self.settings.concurrency,
            cache=DiskCache(ttl_days=self.settings.cache_ttl_days),
            offline=self.settings.offline_mode,
        )

        # 分轨条目共享同一整轨文件，按 path 去重，每个整轨文件只切分一次
        seen: set[str] = set()
        whole_tracks = []
        for t in self.tracks:
            if t.status is TrackStatus.UNSUPPORTED or not t.path.exists():
                continue
            key = str(t.path).lower()
            if key in seen or t.cue is None or len(t.cue.tracks) <= 1:
                continue
            seen.add(key)
            whole_tracks.append(t)

        summary = SplitSummary()
        try:
            for track in whole_tracks:
                if cancel.is_set():
                    break
                # 输出到整轨文件同目录的「<stem>_分轨」子目录
                out_dir = self.output_dir / f"{track.path.stem}_分轨"
                one = split_cue_sheet(
                    track, out_dir, client,
                    use_lrclib=self.settings.use_lrclib,
                    use_netmusic=self.settings.use_netmusic,
                    use_qqmusic=self.settings.use_qqmusic,
                    trim_pregap=self.settings.cue_split_trim_pregap,
                    output_format=self.settings.cue_split_format,
                    on_progress=lambda i, n: self.progressChanged.emit(i, n),
                    cancel=cancel,
                )
                summary.total += one.total
                summary.created += one.created
                summary.with_lyrics += one.with_lyrics
                summary.failed += one.failed
                summary.elapsed_sec += one.elapsed_sec
            self.summaryReady.emit(summary)
            self.logMessage.emit("INFO", summary.text())
        except Exception as exc:  # noqa: BLE001
            log.exception("分轨导出失败")
            self.failed.emit(str(exc))
        self.finished.emit()

    def cancel(self) -> None:
        if self._cancel_event is not None:
            self._cancel_event.set()
