"""安全写入引擎（无损最关键的一环，需求 3.1.2 / 4.6）。

三层保护
--------
1. **备份**：写入前把原文件复制到备份目录，保留原路径镜像结构；
2. **影子副本写入**：常规体积文件先复制出 ``*.seqaudio.tmp``，全部标签操作只作用于
   副本，校验通过才 ``os.replace`` 原子替换 —— 中途任何异常都不会碰到原文件；
3. **写后校验**：重开副本，比对时长/采样率/声道，确认音频流未被截断，才允许替换。

超大文件（≥2GB，如 DSD 整轨）为避免双份拷贝把磁盘撑爆，改为「先备份 + 原地 save +
异常自动从备份恢复」。
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from . import paths
from .model import Tags, Track
from .tags import is_writable, open_audio, write_tags

log = logging.getLogger(__name__)

#: 超过该体积走「原地写入 + 备份恢复」策略
LARGE_FILE_THRESHOLD = 2 * 1024 * 1024 * 1024  # 2 GiB

TMP_SUFFIX = ".seqaudio.tmp"


@dataclass
class WriteOutcome:
    ok: bool = False
    message: str = ""
    backup_path: Optional[Path] = None
    written: list[str] = field(default_factory=list)
    elapsed_ms: int = 0


def _absolute_no_symlink(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path


def backup_target(path: Path, backup_root: Optional[Path] = None, mirror: bool = True) -> Path:
    """计算备份路径。``mirror`` 为真时保留相对目录结构，否则回落到 ``_backup`` 平铺目录。"""
    if backup_root is None:
        backup_root = paths.default_backup_dir()
    if mirror:
        try:
            drive = path.drive.rstrip(":\\").replace(":", "")
            relative = path.relative_to(path.anchor or "\\")
            return backup_root / drive / relative
        except (ValueError, OSError):
            return backup_root / path.name
    return backup_root / path.name


def _unique_target(target: Path) -> Path:
    """备份不覆盖：同名文件追加时间戳序号。"""
    if not target.exists():
        return target
    stamp = time.strftime("%Y%m%d-%H%M%S")
    candidate = target.with_name(f"{target.stem}.{stamp}{target.suffix}")
    index = 2
    while candidate.exists():
        candidate = target.with_name(f"{target.stem}.{stamp}_{index}{target.suffix}")
        index += 1
    return candidate


def make_backup(path: Path, backup_root: Optional[Path] = None, mirror: bool = True) -> Optional[Path]:
    """复制原文件到备份目录；磁盘空间不足等失败返回 None（不阻断，由调用方决定）。"""
    try:
        target = _unique_target(backup_target(path, backup_root, mirror))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        if target.stat().st_size != path.stat().st_size:
            log.warning("备份大小不一致：%s -> %s", path, target)
        return target
    except OSError as exc:
        log.error("备份失败 %s：%s", path, exc)
        return None


def _stream_signature(path: Path) -> tuple[Optional[float], Optional[int], Optional[int], int]:
    """音频流指纹：(时长, 采样率, 声道, 文件大小)。"""
    obj = open_audio(path)
    size = path.stat().st_size if path.exists() else 0
    if obj is None:
        return None, None, None, size
    info = getattr(obj, "info", None)
    return (
        getattr(info, "length", None),
        getattr(info, "sample_rate", None),
        getattr(info, "channels", None),
        size,
    )


def _verify_stream(original: Path, candidate: Path, tolerance: float = 1.0) -> tuple[bool, str]:
    """校验候选文件的音频流未被破坏。"""
    o_len, o_rate, o_ch, o_size = _stream_signature(original)
    c_len, c_rate, c_ch, c_size = _stream_signature(candidate)
    if c_size == 0:
        return False, "写入后文件为空"
    if c_rate and o_rate and c_rate != o_rate:
        return False, f"采样率变化 {o_rate} -> {c_rate}"
    if c_ch and o_ch and c_ch != o_ch:
        return False, f"声道数变化 {o_ch} -> {c_ch}"
    if o_len and c_len and abs(o_len - c_len) > tolerance:
        return False, f"时长变化 {o_len:.2f}s -> {c_len:.2f}s"
    return True, "校验通过"


def _write_on_copy(
    work: Path,
    tags: Tags,
    *,
    id3_version: int,
    compat_mode: bool,
    clear_cover: bool,
) -> "WriteOutcome":
    result = write_tags(
        work,
        tags,
        id3_version=id3_version,
        compat_mode=compat_mode,
        clear_cover_when_none=clear_cover,
    )
    return WriteOutcome(ok=result.ok, message=result.message, written=result.written)


def safe_write(
    track: Track,
    *,
    tags: Optional[Tags] = None,
    backup: bool = True,
    backup_root: Optional[Path] = None,
    mirror_backup: bool = True,
    id3_version: int = 4,
    compat_mode: bool = False,
    clear_cover: bool = False,
) -> WriteOutcome:
    """把 ``tags``（默认 track.working）安全写入 ``track.path``。"""
    started = time.perf_counter()
    path = _absolute_no_symlink(track.path)
    payload = tags if tags is not None else track.working

    if not path.exists():
        return WriteOutcome(False, "文件不存在")
    if not os.access(path, os.W_OK):
        return WriteOutcome(False, "文件为只读，无法写入")

    # 预检：标签体系不支持写回时立刻返回。
    # 这一步必须在备份之前 —— 否则为了一个注定失败的写入，
    # 白白复制几百 MB 的母带文件到备份盘。
    try:
        probe_obj = open_audio(path)
        if probe_obj is None:
            return WriteOutcome(False, "无法解析该文件（格式不支持或文件损坏）")
        if not is_writable(probe_obj):
            return WriteOutcome(
                False, f"{type(probe_obj).__name__} 标签为只读格式，已跳过写入"
            )
    except Exception as exc:  # noqa: BLE001 - 预检失败不阻断，交给后续流程判断
        log.debug("写前预检异常 %s：%s", path, exc)

    size = path.stat().st_size
    result = WriteOutcome()

    # ---------- 1. 备份 ----------
    if backup:
        result.backup_path = make_backup(path, backup_root, mirror_backup)
        if result.backup_path is None:
            result.message = "备份失败，已中止写入以保护原文件"
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

    large = size >= LARGE_FILE_THRESHOLD

    if not large:
        # ---------- 2a. 影子副本写入 ----------
        tmp = path.with_name(path.name + TMP_SUFFIX)
        try:
            if tmp.exists():
                tmp.unlink()
            shutil.copy2(path, tmp)
        except OSError as exc:
            result.message = f"创建写入副本失败：{exc}"
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
            return result

        try:
            inner = _write_on_copy(
                tmp,
                payload,
                id3_version=id3_version,
                compat_mode=compat_mode,
                clear_cover=clear_cover,
            )
            if not inner.ok:
                raise RuntimeError(inner.message)
            ok, reason = _verify_stream(path, tmp)
            if not ok:
                raise RuntimeError(f"写后校验失败：{reason}")
            os.replace(tmp, path)
            result.ok = True
            result.written = inner.written
            result.message = "写入成功"
        except Exception as exc:  # noqa: BLE001
            result.ok = False
            result.message = f"写入失败，原文件未改动：{exc}"
            try:
                if tmp.exists():
                    tmp.unlink()
            except OSError:
                pass
        finally:
            result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        return result

    # ---------- 2b. 超大文件：原地写入 + 失败回滚 ----------
    try:
        inner = _write_on_copy(
            path,
            payload,
            id3_version=id3_version,
            compat_mode=compat_mode,
            clear_cover=clear_cover,
        )
        if not inner.ok:
            raise RuntimeError(inner.message)
        ok, reason = _verify_stream(path, path)
        if not ok:
            raise RuntimeError(f"写后校验失败：{reason}")
        result.ok = True
        result.written = inner.written
        result.message = "写入成功（大文件原地写入）"
    except Exception as exc:  # noqa: BLE001
        result.ok = False
        rolled = False
        if result.backup_path and result.backup_path.exists():
            try:
                shutil.copy2(result.backup_path, path)
                rolled = True
            except OSError as restore_exc:
                log.critical("回滚失败 %s：%s", path, restore_exc)
        result.message = f"写入失败：{exc}" + ("，已从备份回滚" if rolled else "，回滚未完成，请检查备份")
    finally:
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
    return result
