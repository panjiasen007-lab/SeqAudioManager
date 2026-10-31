"""隔离区：把「判定为假无损」的文件安全移出曲库，并保留可还原记录。

为什么不是直接删除
------------------
频谱判定的本质是概率：320kbps 转码与真无损的截止频率只差 2kHz，母带处理的
老录音也可能被削掉高频。一旦真的删了 1.7GB 母带，用户没有任何挽回手段。

所以这里分三级，风险从低到高：

1. **移入回收站**（默认）—— 走 Windows Shell 的 SHFileOperationW 带 FOF_ALLOWUNDO，
   进入系统回收站，用户随时能右键还原；
2. **移入隔离区** —— 移到 ``data/quarantine/<批次>/``，写 manifest 记录原路径，
   软件内可一键还原，还能看到每个文件的判定理由；
3. **彻底删除** —— 仅在前两级都不满足需求时提供，UI 上必须额外勾选确认。

移动时会把同名 ``.lrc`` 一起带走，否则删了音频留下孤儿歌词文件，下次扫描
又是一堆无从归属的侧车文件。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from . import paths

log = logging.getLogger(__name__)

#: 与音频同名的附属文件后缀（删除/隔离时一起处理）
COMPANION_SUFFIXES = (".lrc", ".cue", ".log", ".txt")


@dataclass
class QuarantineEntry:
    """隔离区的一条记录。"""

    entry_id: str
    original: str                 # 原始绝对路径
    stored: str                   # 隔离区内的路径
    size: int                     # 音频本身字节数
    verdict: str = ""             # 判定结论（假无损 / 疑似升频 …）
    detail: str = ""              # 判定依据摘要
    batch: str = ""               # 批次号，便于整批还原
    moved_at: str = ""
    companions: list[str] = field(default_factory=list)   # 一并带走的附属文件
    restored_at: str = ""
    purged: bool = False

    @property
    def exists(self) -> bool:
        return Path(self.stored).exists()

    @property
    def size_text(self) -> str:
        return human_size(self.size)


def human_size(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(max(0, value))
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def quarantine_root() -> Path:
    root = paths.data_dir() / "quarantine"
    root.mkdir(parents=True, exist_ok=True)
    return root


# ---------------------------------------------------------------- 回收站


def send_to_recycle_bin(targets: Iterable[Path]) -> tuple[int, str]:
    """把文件送入 Windows 回收站（可还原）。

    非 Windows 或调用失败时返回 (0, 原因)，调用方应回退到隔离区方案 ——
    宁可多占点磁盘，也不能让用户的文件凭空消失。
    """
    files = [str(Path(t)) for t in targets if Path(t).exists()]
    if not files:
        return 0, "没有可删除的文件"
    if os.name != "nt":
        return 0, "当前系统无回收站，请改用隔离区"

    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    FO_DELETE = 3
    FOF_SILENT = 0x0004
    FOF_NOCONFIRMATION = 0x0010
    FOF_ALLOWUNDO = 0x0040          # 关键：有它才进回收站
    FOF_NOERRORUI = 0x0400
    FOF_NOCONFIRMMKDIR = 0x0200

    # 多字符串必须以双 \0 结尾
    buffer = "\0".join(files) + "\0\0"
    op = SHFILEOPSTRUCTW()
    op.hwnd = None
    op.wFunc = FO_DELETE
    op.pFrom = buffer
    op.pTo = None
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI | FOF_NOCONFIRMMKDIR

    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as exc:  # noqa: BLE001
        log.warning("调用回收站失败：%s", exc)
        return 0, f"回收站调用失败：{exc}"
    if result != 0:
        return 0, f"回收站返回错误码 {result}"
    if op.fAnyOperationsAborted:
        return 0, "操作被系统中断"
    return len(files), ""


# ---------------------------------------------------------------- 隔离区存储


class QuarantineStore:
    """隔离区的读写与 manifest 维护。

    manifest 用「整表原子替换」而不是追加：一次清理 200 个文件时，
    边写边崩会留下半条记录，还原时反而找不到文件。
    """

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else quarantine_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "manifest.json"

    # ---- 读写
    def entries(self, *, include_purged: bool = False) -> list[QuarantineEntry]:
        raw = self._load()
        result = []
        for item in raw:
            try:
                entry = QuarantineEntry(**item)
            except TypeError:
                continue
            if entry.purged and not include_purged:
                continue
            result.append(entry)
        return result

    def _load(self) -> list[dict]:
        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except (OSError, json.JSONDecodeError):
            return []

    def _flush(self, entries: list[dict]) -> None:
        tmp = self.manifest_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.manifest_path)

    def _append(self, new_entries: list[QuarantineEntry]) -> None:
        raw = self._load()
        raw.extend(asdict(e) for e in new_entries)
        self._flush(raw)

    def _update(self, entry: QuarantineEntry) -> None:
        raw = self._load()
        for index, item in enumerate(raw):
            if item.get("entry_id") == entry.entry_id:
                raw[index] = asdict(entry)
                break
        else:
            raw.append(asdict(entry))
        self._flush(raw)

    # ---- 写入
    def move_in(
        self,
        items: Iterable[tuple[Path, str, str]],
        *,
        batch: str = "",
    ) -> tuple[list[QuarantineEntry], list[tuple[Path, str]]]:
        """把文件移入隔离区。

        ``items`` 是 ``(路径, 判定, 依据)`` 三元组。返回 (成功记录, 失败列表)。
        """
        batch = batch or time.strftime("%Y%m%d-%H%M%S")
        batch_dir = self.root / batch
        batch_dir.mkdir(parents=True, exist_ok=True)

        created: list[QuarantineEntry] = []
        failed: list[tuple[Path, str]] = []

        for path, verdict, detail in items:
            path = Path(path)
            if not path.exists():
                failed.append((path, "文件不存在"))
                continue
            target = self._unique_target(batch_dir, path.name)
            try:
                size = path.stat().st_size
                shutil.move(str(path), str(target))
            except OSError as exc:
                failed.append((path, f"移动失败：{exc}"))
                continue

            companions: list[str] = []
            for suffix in COMPANION_SUFFIXES:
                side = path.with_suffix(suffix)
                if not side.exists():
                    continue
                try:
                    shutil.move(str(side), str(self._unique_target(batch_dir, side.name)))
                    companions.append(side.name)
                except OSError:
                    pass

            created.append(
                QuarantineEntry(
                    entry_id=f"{batch}-{len(created) + 1:03d}",
                    original=str(path),
                    stored=str(target),
                    size=size,
                    verdict=verdict,
                    detail=detail,
                    batch=batch,
                    moved_at=time.strftime("%Y-%m-%d %H:%M:%S"),
                    companions=companions,
                )
            )

        if created:
            self._append(created)
            log.info("隔离 %d 个文件 -> %s", len(created), batch_dir)
        return created, failed

    @staticmethod
    def _unique_target(directory: Path, name: str) -> Path:
        """同名文件加序号，避免两次清理互相覆盖。"""
        target = directory / name
        if not target.exists():
            return target
        stem, suffix = Path(name).stem, Path(name).suffix
        for index in range(1, 1000):
            candidate = directory / f"{stem} ({index}){suffix}"
            if not candidate.exists():
                return candidate
        return directory / f"{stem}-{int(time.time())}{suffix}"

    # ---- 还原 / 清除
    def restore(self, entry: QuarantineEntry) -> tuple[bool, str]:
        stored = Path(entry.stored)
        if not stored.exists():
            return False, "隔离区内的文件已丢失"
        original = Path(entry.original)
        target = original
        if target.exists():
            # 原位置已被新文件占用：退到同目录的「已恢复」文件名，绝不覆盖
            target = original.with_name(f"{original.stem} (已恢复){original.suffix}")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(stored), str(target))
        except OSError as exc:
            return False, f"还原失败：{exc}"

        # 附属文件跟着回去
        for name in entry.companions:
            side = self.root / entry.batch / name
            if side.exists():
                try:
                    shutil.move(str(side), str(target.with_suffix(Path(name).suffix)))
                except OSError:
                    pass

        entry.restored_at = time.strftime("%Y-%m-%d %H:%M:%S")
        entry.purged = True
        self._update(entry)
        return True, f"已还原到 {target}"

    def purge(self, entry: QuarantineEntry) -> tuple[bool, str]:
        """从隔离区彻底删除单个文件。"""
        stored = Path(entry.stored)
        if stored.exists():
            try:
                stored.unlink()
            except OSError as exc:
                return False, f"删除失败：{exc}"
        for name in entry.companions:
            side = self.root / entry.batch / name
            if side.exists():
                try:
                    side.unlink()
                except OSError:
                    pass
        entry.purged = True
        self._update(entry)
        return True, "已彻底删除"

    def purge_batch(self, batch: str) -> int:
        count = 0
        for entry in self.entries():
            if entry.batch == batch and self.purge(entry)[0]:
                count += 1
        # 批次目录空了就一并删掉
        batch_dir = self.root / batch
        if batch_dir.is_dir() and not any(batch_dir.iterdir()):
            try:
                batch_dir.rmdir()
            except OSError:
                pass
        return count

    def total_size(self) -> int:
        return sum(e.size for e in self.entries() if e.exists)

    def batches(self) -> dict[str, list[QuarantineEntry]]:
        grouped: dict[str, list[QuarantineEntry]] = {}
        for entry in self.entries():
            grouped.setdefault(entry.batch or "unknown", []).append(entry)
        return dict(sorted(grouped.items(), reverse=True))


def delete_permanently(targets: Iterable[Path]) -> tuple[int, list[tuple[Path, str]]]:
    """彻底删除（不进回收站）。调用方必须已做过二次确认。"""
    done = 0
    failed: list[tuple[Path, str]] = []
    for path in targets:
        path = Path(path)
        if not path.exists():
            continue
        try:
            path.unlink()
            done += 1
        except OSError as exc:
            failed.append((path, str(exc)))
            continue
        for suffix in COMPANION_SUFFIXES:
            side = path.with_suffix(suffix)
            if side.exists():
                try:
                    side.unlink()
                except OSError:
                    pass
    return done, failed
