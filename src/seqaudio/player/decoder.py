"""ffmpeg 子进程解码器：任意格式 → 定长原始 PCM 块。

为什么还是 ffmpeg 子进程
------------------------
项目已经随包内置 `imageio-ffmpeg` 提供的 ffmpeg 7.1（真假无损鉴定在用），
它覆盖 FLAC/ALAC/APE/WavPack/TTA/AIFF/CAF/AU/MP3/AAC/OGG/Opus/WMA/MPC/AC-3
以及 DSD（``dsd_lsbf`` / ``dsd_msbf``）。再引入一个专用解码库只会让打包体积
和 DLL 冲突面变大，而收益为零。所以唯一路径是「起子进程，把 PCM 从 stdout
按块读出来」。

两条使用模式
------------
1. **推模式（引擎用）**：构造时传入 ``ring``，``start()`` 后内部起一条线程
   把 PCM 块写进 ``PcmRingBuffer``。引擎只管开流与状态，不需要自己写循环。
2. **拉模式（测试/工具用）**：不传 ``ring``，直接反复调 ``read_block()``。

进程与线程纪律
--------------
* stdout 只由**一个**线程读（PortAudio 回调绝不碰管道）；
* stderr 另起一条线程抽干：``-v error`` 输出虽少，但只要管道写满（约 64 KB），
  ffmpeg 就会阻塞在写 stderr 上，表现为「解码卡住」，这条线程是防它的；
* 绝不把整首歌读进内存，块大小取自 ``constants``。
"""

from __future__ import annotations

import collections
import logging
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import numpy as np

from ..core.authenticity import find_ffmpeg
from .constants import (
    BLOCK_FRAMES_DEFAULT,
    DECODE_STALL_TIMEOUT_SEC,
    DECODER_JOIN_TIMEOUT_SEC,
    DECODER_STDERR_LINES,
    DEFAULT_CHANNELS,
    DEFAULT_SAMPLE_FMT,
    DSD_PCM_RATE_DEFAULT,
    FEED_STALL_SLEEP_SEC,
    FFMPEG_PROBE_TIMEOUT_SEC,
    PCM_FMT_TO_FFMPEG,
    PCM_FMT_TO_PIPE,
    SR_44K,
    numpy_dtype_name,
)
from .ringbuffer import PcmRingBuffer

log = logging.getLogger(__name__)

#: ``ffmpeg version 7.1-essentials_build-www.gyan.dev`` → ``7.1``
_VERSION_RE = re.compile(r"ffmpeg version\s+(\d+(?:\.\d+)*)", re.IGNORECASE)
#: ``Stream #0:0: Audio: flac, 96000 Hz, stereo, s32 (24 bit)`` → ``flac``
_AUDIO_RE = re.compile(r"Audio:\s*([A-Za-z0-9_]+)")

#: 打包版里不要弹出控制台窗口
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class DecodeError(RuntimeError):
    """解码不可用（ffmpeg 缺失、参数非法、进程启动失败）。"""


@dataclass
class DecodeSpec:
    """一次解码任务的完整参数（PCM 格式的**唯一真源**，见架构文档 10.4-1）。

    位深、声道、采样率一旦定在这里，ffmpeg 参数、numpy dtype、
    ``OutputConfig.dtype`` 全部由它派生，别处不许再自行判断位深。
    """

    path: Path
    start_ms: int = 0
    duration_ms: int = 0
    sample_rate: int = SR_44K
    channels: int = DEFAULT_CHANNELS
    sample_fmt: str = DEFAULT_SAMPLE_FMT
    codec: str = ""
    is_dsd: bool = False
    block_frames: int = BLOCK_FRAMES_DEFAULT

    # ------------------------------------------------------------ 派生信息

    @property
    def numpy_dtype(self) -> np.dtype:
        """numpy 侧 dtype（由 ``sample_fmt`` 经 ``numpy_dtype_name`` 派生）。"""
        try:
            return np.dtype(numpy_dtype_name(self.sample_fmt))
        except TypeError:
            # 未知格式名不该让播放崩掉：退回 16 位整数，日志里另有记录
            log.warning("未知样本格式 %r，按 int16 处理", self.sample_fmt)
            return np.dtype("int16")

    @property
    def frame_bytes(self) -> int:
        """一帧的字节数（帧 = 各声道各一个样本）。"""
        return self.numpy_dtype.itemsize * int(self.channels)

    @property
    def block_bytes(self) -> int:
        """一个解码块的字节数。"""
        return self.frame_bytes * int(self.block_frames)

    @property
    def start_sec(self) -> float:
        return max(0.0, self.start_ms / 1000.0)

    @property
    def duration_sec(self) -> float:
        """``0`` 表示「到文件末尾」。"""
        return max(0.0, self.duration_ms / 1000.0)

    # ------------------------------------------------------------ 构造

    @classmethod
    def from_track(cls, track, settings) -> "DecodeSpec":
        """由 ``Track`` + ``Settings`` 推导解码参数。

        CUE 分轨的边界**只**取 ``CueTrack.start_ms / end_ms``（10.4-3），
        不重新解析 CUE 文件；DSD 的判定**只**看 ``AudioInfo.dsd_rate`` 或
        codec 前缀（10.4-2），不用采样率数值去猜。

        Args:
            track: ``core.model.Track``（只读，不修改任何字段）。
            settings: ``core.config.Settings``；缺字段时按默认值兜底，
                方便测试里用轻量替身。
        """
        info = getattr(track, "info", None)
        path = Path(track.path)

        start_ms = 0
        duration_ms = 0
        entry = getattr(track, "cue_track_entry", None)
        if entry is not None:
            start_ms = max(0, int(getattr(entry, "start_ms", 0) or 0))
            end_ms = int(getattr(entry, "end_ms", 0) or 0)
            if end_ms > start_ms:
                duration_ms = end_ms - start_ms

        codec = _codec_from_info(info, path)
        is_dsd = bool(getattr(info, "dsd_rate", None)) or codec.startswith("dsd_")

        channels = int(getattr(info, "channels", 0) or DEFAULT_CHANNELS)
        if channels <= 0:
            channels = DEFAULT_CHANNELS
        if getattr(settings, "playback_downmix_stereo", False) and channels > 2:
            channels = 2

        sample_rate = _pick_sample_rate(info, settings, is_dsd)
        sample_fmt = _pick_sample_fmt(info, settings, is_dsd)

        block_frames = int(getattr(settings, "playback_block_frames", BLOCK_FRAMES_DEFAULT))
        if block_frames <= 0:
            block_frames = BLOCK_FRAMES_DEFAULT

        return cls(
            path=path,
            start_ms=start_ms,
            duration_ms=duration_ms,
            sample_rate=sample_rate,
            channels=channels,
            sample_fmt=sample_fmt,
            codec=codec,
            is_dsd=is_dsd,
            block_frames=block_frames,
        )

    @classmethod
    def from_item(cls, item, settings, *, start_ms: Optional[int] = None) -> "DecodeSpec":
        """由 ``queue.QueueItem``（轻量投影）构造，**不重新 probe、不重解析 CUE**。

        引擎拿不到 ``Track``（队列只存投影），但解码需要的一切——

        路径、CUE 起止、采样率、声道、位深、是否 DSD —— 队列项里都有，
        所以这里直接按投影组装，起播路径上省掉一次文件探测。

        Args:
            item: ``QueueItem``（或任何具备同名属性的对象）。
            settings: ``Settings``（缺字段按默认值兜底，便于测试替身）。
            start_ms: 相对**曲目**起点的偏移（seek 用）；``None`` 表示从头。
        """
        base_start = max(0, int(getattr(item, "start_ms", 0) or 0))
        item_duration = max(0, int(getattr(item, "duration_ms", 0) or 0))
        offset = max(0, int(start_ms)) if start_ms is not None else 0

        if item_duration > 0:
            # CUE 子轨：起点顺延，时长按剩余部分截断
            start = base_start + min(offset, item_duration)
            duration = max(0, item_duration - offset)
        else:
            start = base_start + offset
            duration = 0

        codec = str(getattr(item, "codec", "") or "").strip().lower()
        path = Path(getattr(item, "path", "") or "")
        if not codec:
            codec = path.suffix.lstrip(".").lower()
        is_dsd = bool(getattr(item, "is_dsd", False)) or codec.startswith("dsd_")

        channels = int(getattr(item, "channels", 0) or 0) or DEFAULT_CHANNELS
        if getattr(settings, "playback_downmix_stereo", False) and channels > 2:
            channels = 2

        info_like = _InfoLike(
            fmt=codec,
            sample_rate=int(getattr(item, "sample_rate", 0) or 0),
            bit_depth=int(getattr(item, "bit_depth", 0) or 0),
            channels=channels,
            dsd_rate="DSD" if is_dsd else None,
        )
        sample_rate = _pick_sample_rate(info_like, settings, is_dsd)
        sample_fmt = _pick_sample_fmt(info_like, settings, is_dsd)
        block_frames = int(getattr(settings, "playback_block_frames", BLOCK_FRAMES_DEFAULT))
        if block_frames <= 0:
            block_frames = BLOCK_FRAMES_DEFAULT

        return cls(
            path=path,
            start_ms=start,
            duration_ms=duration,
            sample_rate=sample_rate,
            channels=channels,
            sample_fmt=sample_fmt,
            codec=codec,
            is_dsd=is_dsd,
            block_frames=block_frames,
        )

    # ------------------------------------------------------------ 展示 / 命令

    def to_display(self) -> str:
        """一行式描述，供信号路径面板与日志使用。"""
        label = (self.codec or self.path.suffix.lstrip(".")).upper() or "AUDIO"
        depth = {
            "s16": "16bit",
            "s24": "24bit",
            "s32": "32bit",
            "f32": "32bit float",
        }.get(self.sample_fmt, self.sample_fmt)
        parts = [label, f"{depth}/{self.sample_rate} Hz", f"{self.channels}ch"]
        if self.duration_ms > 0:
            total = int(round(self.duration_ms / 1000.0))
            parts.append(f"{total // 60:02d}:{total % 60:02d}")
        if self.is_dsd and self.start_ms == 0:
            parts.append("DSD→PCM")
        return " · ".join(parts)

    def build_args(self, ffmpeg: str) -> list[str]:
        """拼 ffmpeg 命令行。

        要点：
        * ``-map 0:a:0`` 显式取首条音轨，含视频的 MKV/MP4 自然只出音频；
        * ``-ss`` 放在 ``-i`` **之后**：输出侧 seek，ffmpeg 会先解码再丢弃，
          时间轴精确（CUE 定位验收 ≤50 ms），代价是深位置 seek 稍慢；
        * ``-err_detect ignore_err``：坏帧跳过续播（FR-DEC-008 的本期子集）；
        * ``-nostdin``：防止 ffmpeg 抢终端输入导致进程挂住。
        """
        pipe_fmt = PCM_FMT_TO_PIPE.get(self.sample_fmt, "s16le")
        acodec = PCM_FMT_TO_FFMPEG.get(self.sample_fmt, "pcm_s16le")

        args: list[str] = [
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-nostdin",
            "-err_detect", "ignore_err",
            "-i", str(self.path),
        ]
        if self.start_ms > 0:
            args += ["-ss", f"{self.start_sec:.3f}"]
        if self.duration_ms > 0:
            args += ["-t", f"{self.duration_sec:.3f}"]
        args += [
            "-map", "0:a:0",
            "-vn",
            "-ar", str(int(self.sample_rate)),
            "-ac", str(int(self.channels)),
            "-acodec", acodec,
            "-f", pipe_fmt,
            "-",
        ]
        return args


class FfmpegDecoder:
    """一条解码会话 = 一个 ffmpeg 子进程 + 一条读取线程。

    Args:
        spec: 解码参数。
        ring: 推模式的目标环；``None`` 则走拉模式（调用方自己 ``read_block()``）。
        ffmpeg_path: 显式指定 ffmpeg，``None`` 则按 ``find_ffmpeg()`` 查找。
        on_error: 错误回调（收到的是可读中文串），可能在解码线程里被调用，
            实现方**不得**在里面操作 Qt 控件。
        on_eof: 解码结束回调（正常结束与出错都会调，用 ``returncode`` 区分）。
    """

    def __init__(
        self,
        spec: DecodeSpec,
        ring: Optional[PcmRingBuffer] = None,
        ffmpeg_path: Optional[str] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_eof: Optional[Callable[[], None]] = None,
    ) -> None:
        self.spec = spec
        self.ring = ring
        self._ffmpeg_path = ffmpeg_path
        self._on_error = on_error
        self._on_eof = on_eof

        self._proc: Optional[subprocess.Popen] = None
        self._thread: Optional[threading.Thread] = None
        self._stderr_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._eof = threading.Event()
        # 喂数据闸门：暂停时关闭，让解码线程「等」而不是把数据丢掉
        self._gate = threading.Event()
        self._gate.set()

        self._frames_pushed = 0
        self._dropped_frames = 0
        self._stall_reported = False
        self._started = False
        self._returncode: Optional[int] = None
        # 只留最后若干行 stderr：报错摘要够用，不会无界增长
        self._stderr_lines: collections.deque[str] = collections.deque(
            maxlen=DECODER_STDERR_LINES
        )

    # ------------------------------------------------------------ 生命周期

    def start(self) -> None:
        """启动 ffmpeg（推模式下同时启动写入线程）。"""
        if self._started:
            raise DecodeError("解码器已经启动过，请新建实例")
        exe = self._ffmpeg_path or find_ffmpeg()
        if not exe:
            raise DecodeError("未找到 ffmpeg，无法解码音频")
        if not Path(self.spec.path).exists():
            raise DecodeError(f"文件不存在：{self.spec.path}")

        args = self.spec.build_args(exe)
        log.debug("启动解码：%s", " ".join(args))
        try:
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
                creationflags=_NO_WINDOW,
            )
        except OSError as exc:
            raise DecodeError(f"启动 ffmpeg 失败：{exc}") from exc

        self._started = True
        self._stop.clear()
        self._eof.clear()
        self._gate.set()

        self._stderr_thread = threading.Thread(
            target=self._drain_stderr, name="seqaudio-decode-stderr", daemon=True
        )
        self._stderr_thread.start()

        if self.ring is not None:
            self._thread = threading.Thread(
                target=self._pump, name="seqaudio-decode-pump", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        """终止 ffmpeg 并回收线程；重复调用安全。"""
        self._stop.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except OSError as exc:  # pragma: no cover - 极端竞态
                log.debug("终止 ffmpeg 失败：%s", exc)
        if proc is not None:
            try:
                proc.wait(timeout=DECODER_JOIN_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:  # pragma: no cover - 罕见卡死
                log.warning("ffmpeg 未在 %.0f s 内退出，强制结束", DECODER_JOIN_TIMEOUT_SEC)
                try:
                    proc.kill()
                    proc.wait(timeout=DECODER_JOIN_TIMEOUT_SEC)
                except (OSError, subprocess.TimeoutExpired):
                    pass
            self._returncode = proc.returncode
            for stream in (proc.stdout, proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        for thread in (self._thread, self._stderr_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=DECODER_JOIN_TIMEOUT_SEC)
        self._thread = None
        self._stderr_thread = None

    def wait(self, timeout: Optional[float] = None) -> bool:
        """等待读取线程结束；返回是否已结束。"""
        thread = self._thread
        if thread is None:
            return True
        thread.join(timeout=timeout)
        return not thread.is_alive()

    def is_alive(self) -> bool:
        """是否仍在解码（线程活着或进程未退出）。"""
        thread = self._thread
        if thread is not None and thread.is_alive():
            return True
        proc = self._proc
        return proc is not None and proc.poll() is None

    @property
    def returncode(self) -> Optional[int]:
        """ffmpeg 退出码（未结束时为 ``None``）。

        **不要只看退出码判断成败**：被 ``stop()`` 主动终止时 ffmpeg 通常返回
        ``1``（进程收到 terminate），那不是错误。判定顺序应是
        ``stopped`` → 主动停；否则 ``at_eof`` + ``returncode == 0`` → 正常结束；
        其余才是解码失败（可用 ``stderr_tail`` 取原因）。
        """
        if self._proc is not None and self._proc.poll() is not None:
            self._returncode = self._proc.returncode
        return self._returncode

    @property
    def stopped(self) -> bool:
        """是否被调用方 ``stop()`` 主动停止（区别于解码失败）。"""
        return self._stop.is_set()

    # ------------------------------------------------------------ 读数

    @property
    def position_sec(self) -> float:
        """当前已产出的音频位置（秒，含 start_sec 偏移）。"""
        return self.spec.start_sec + self._frames_pushed / float(self.spec.sample_rate)

    @property
    def frames_pushed(self) -> int:
        """累计产出帧数。"""
        return self._frames_pushed

    @property
    def dropped_frames(self) -> int:
        """因环满而丢弃的帧数（解码停滞的量化指标）。"""
        return self._dropped_frames

    @property
    def at_eof(self) -> bool:
        return self._eof.is_set()

    @property
    def stderr_tail(self) -> str:
        """最后若干行 stderr，用于错误摘要。"""
        return "\n".join(self._stderr_lines)

    # ------------------------------------------------------------ 暂停喂数据

    def pause_feeding(self) -> None:
        """暂停向环形缓冲喂数据（暂停播放时用）。

        暂停期间**不能让解码线程继续写**：环一旦满，解码线程只能丢数据，
        用户按下「继续播放」就听到中途断掉的一段。所以这里让线程等闸门，
        ffmpeg 子进程会因管道写满而自然阻塞 —— 正好把不确定性交还给操作系统。
        """
        self._gate.clear()

    def resume_feeding(self) -> None:
        """恢复向环形缓冲喂数据。"""
        self._gate.set()

    # ------------------------------------------------------------ 读块

    def read_block(self) -> Optional[np.ndarray]:
        """读取下一个 PCM 块；EOF 返回 ``None``。

        返回的数组是只读视图（``np.frombuffer``），形状 ``(帧, 声道)``。
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return None
        raw = self._read_exact(self.spec.block_bytes)
        if not raw:
            return None
        frame_bytes = self.spec.frame_bytes
        if frame_bytes <= 0:  # pragma: no cover - 构造期已校验
            return None
        usable = len(raw) - (len(raw) % frame_bytes)
        if usable <= 0:
            return None
        if usable != len(raw):
            log.debug("丢弃 %d 字节不足一帧的尾部数据", len(raw) - usable)
            raw = raw[:usable]
        frames = usable // frame_bytes
        return np.frombuffer(raw, dtype=self.spec.numpy_dtype).reshape(
            frames, int(self.spec.channels)
        )

    def _read_exact(self, nbytes: int) -> bytes:
        """从 stdout 读满 ``nbytes``；不足即 EOF（管道读可能短返回）。"""
        proc = self._proc
        if proc is None or proc.stdout is None:
            return b""
        chunks: list[bytes] = []
        remaining = nbytes
        while remaining > 0:
            try:
                chunk = proc.stdout.read(remaining)
            except (OSError, ValueError):  # pragma: no cover - 管道已被关闭
                break
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if not chunks:
            return b""
        return chunks[0] if len(chunks) == 1 else b"".join(chunks)

    # ------------------------------------------------------------ 线程体

    def _pump(self) -> None:
        """推模式线程体：读块 → 写环，环满则短暂自旋。"""
        try:
            while not self._stop.is_set():
                block = self.read_block()
                if block is None:
                    break
                self._push_block(block)
        except BaseException as exc:  # noqa: BLE001 - 线程里必须兜住一切
            log.exception("解码线程异常退出")
            self._report_error(f"解码线程异常：{exc}")
        finally:
            self._eof.set()
            rc = self.returncode
            if rc not in (None, 0) and not self._stop.is_set():
                tail = self.stderr_tail.strip()
                self._report_error(
                    f"ffmpeg 退出码 {rc}" + (f"：{tail}" if tail else "")
                )
            log.debug(
                "解码结束：%s（%d 帧，退出码 %s）",
                Path(self.spec.path).name, self._frames_pushed, rc,
            )
            if self._on_eof is not None:
                try:
                    self._on_eof()
                except BaseException:  # noqa: BLE001 - 回调实现方的问题不能拖垮线程
                    log.exception("on_eof 回调抛异常")

    def _push_block(self, block: np.ndarray) -> None:
        """把一块 PCM 完整写进环；空间不足时自旋等待，超时则丢弃剩余部分。"""
        ring = self.ring
        if ring is None:  # pragma: no cover - 只会在拉模式误调
            return
        offset = 0
        total = int(block.shape[0])
        blocked_since: Optional[float] = None
        while offset < total:
            if self._stop.is_set():
                return
            if not self._gate.is_set():
                # 暂停中：等引擎恢复。这里耗住的是解码线程，回调线程不受影响
                self._gate.wait(FEED_STALL_SLEEP_SEC * 10)
                blocked_since = None
                continue
            written = ring.write(block[offset:])
            if written > 0:
                offset += written
                # 逐块累计而不是最后一次性加 total：暂停/停止可能在块中间发生，
                # 位置时钟必须只按「真正进了环」的帧数走（10.4-5 单一位置真源）。
                self._frames_pushed += written
                blocked_since = None
                continue
            now = time.monotonic()
            if blocked_since is None:
                blocked_since = now
            elif now - blocked_since > DECODE_STALL_TIMEOUT_SEC:
                dropped = total - offset
                self._dropped_frames += dropped
                if not self._stall_reported:
                    self._stall_reported = True
                    log.warning(
                        "环形缓冲 %.0f ms 内没有腾出空间，丢弃 %d 帧（解码停滞）",
                        DECODE_STALL_TIMEOUT_SEC * 1000, dropped,
                    )
                return
            # 交给消费者一点时间腾空间；这不是忙等锁，只是让步
            time.sleep(FEED_STALL_SLEEP_SEC)

    def _drain_stderr(self) -> None:
        """抽干 stderr，避免 ffmpeg 因管道写满而阻塞。"""
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            for raw in iter(proc.stderr.readline, b""):
                line = raw.decode("utf-8", "replace").strip()
                if not line:
                    continue
                self._stderr_lines.append(line)
                log.debug("ffmpeg: %s", line)
        except (OSError, ValueError):  # pragma: no cover - 进程被强杀
            pass

    def _report_error(self, message: str) -> None:
        log.error("%s", message)
        if self._on_error is None:
            return
        try:
            self._on_error(message)
        except BaseException:  # noqa: BLE001
            log.exception("on_error 回调抛异常")


# ---------------------------------------------------------------- 模块级工具


def probe_codec(path: Path | str) -> tuple[str, str]:
    """探测「ffmpeg 版本」与「音频编码名」，如 ``("ffmpeg 7.1", "flac")``。

    只读文件头，不产出 PCM。任何失败都返回可读但不完整的结果，绝不抛异常：
    信号路径面板拿不到 codec 时，退化成显示扩展名即可。

    Returns:
        ``(版本串, 编码名)``；找不到 ffmpeg 时是 ``("", "")``。
    """
    exe = find_ffmpeg()
    if not exe:
        return "", ""
    version = "ffmpeg"
    codec = ""
    try:
        proc = subprocess.run(
            [exe, "-hide_banner", "-i", str(path)],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=FFMPEG_PROBE_TIMEOUT_SEC,
            creationflags=_NO_WINDOW,
        )
        text = (proc.stderr or b"").decode("utf-8", "replace")
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("探测编码失败（%s）：%s", path, exc)
        return version, codec

    match = _VERSION_RE.search(text)
    if match:
        version = f"ffmpeg {match.group(1)}"
    audio = _AUDIO_RE.search(text)
    if audio:
        codec = audio.group(1).lower()
    return version, codec


class _InfoLike:
    """把「位深/采样率/声道/DSD」几个字段包成 ``AudioInfo`` 的样子。

    ``_pick_sample_fmt`` / ``_pick_sample_rate`` 只读这几个属性，用不着造一个
    完整的 ``AudioInfo``（那会牵进 core.model 的导入与一堆无关字段）。
    """

    __slots__ = ("fmt", "sample_rate", "bit_depth", "channels", "dsd_rate")

    def __init__(
        self,
        fmt: str = "",
        sample_rate: int = 0,
        bit_depth: int = 0,
        channels: int = 0,
        dsd_rate: Optional[str] = None,
    ) -> None:
        self.fmt = fmt
        self.sample_rate = sample_rate
        self.bit_depth = bit_depth
        self.channels = channels
        self.dsd_rate = dsd_rate


def _codec_from_info(info, path: Path) -> str:
    """从 ``AudioInfo.fmt`` 推 codec 名（小写）；没有再退回扩展名。

    刻意**不再起一个子进程**去 ``probe_codec``：起播路径上每省一次进程启动，
    首块就绪就能早几十毫秒。真正的编码名由 ``probe_codec`` 在需要展示时补。
    """
    fmt = str(getattr(info, "fmt", "") or "").strip().lower()
    if fmt:
        return fmt
    return path.suffix.lstrip(".").lower()


def _pick_sample_rate(info, settings, is_dsd: bool) -> int:
    """解码侧的采样率：DSD 走目标 PCM 率，其余一律取源采样率。

    ``playback_follow_source_rate`` 刻意**不**在这里生效：是否跟随是引擎的
    决定（它掌握设备能力），设备不支持源率时引擎会把 ``spec.sample_rate``
    改成回退率，让 ffmpeg 用 ``-ar`` 一并完成重采样。
    """
    if is_dsd:
        rate = int(
            getattr(settings, "playback_dsd_pcm_rate", DSD_PCM_RATE_DEFAULT)
            or DSD_PCM_RATE_DEFAULT
        )
        return rate if rate > 0 else DSD_PCM_RATE_DEFAULT
    source = int(getattr(info, "sample_rate", 0) or 0)
    return source if source > 0 else SR_44K


def _pick_sample_fmt(info, settings, is_dsd: bool) -> str:
    """样本格式：整数通路只在「音量旁路 + 无 EQ + 无淡变 + 非 DSD」时成立。

    判据统一走 ``dsp.int_pipeline_allowed()`` —— 这里是**唯一**决定样本格式的
    地方，别处（引擎/界面）只许读结果。这条交叉校验是 QA 实测出来的缺口：
    少了它，``int_pipeline`` 与软件音量/EQ 同时打开会让浮点 DSP 拿到 int16
    缓冲，numpy 2.x 抛 casting 异常、被回调吞掉 → 整场静音。
    """
    from .dsp import int_pipeline_allowed

    if is_dsd or not int_pipeline_allowed(settings):
        return DEFAULT_SAMPLE_FMT
    depth = int(getattr(info, "bit_depth", 0) or 0)
    if depth == 16:
        return "s16"
    if depth in (17, 18, 19, 20, 21, 22, 23, 24):
        return "s24"
    if depth in (25, 26, 27, 28, 29, 30, 31, 32):
        return "s32"
    return DEFAULT_SAMPLE_FMT


__all__ = ["DecodeSpec", "FfmpegDecoder", "DecodeError", "probe_codec"]
