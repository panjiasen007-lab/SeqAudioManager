"""批量重命名（需求 3.5）。

* 模板变量：{歌手} {歌名} {专辑} {专辑艺术家} {年份} {流派} {曲目} {碟号} {格式} {位深} {采样率}
* 支持格式说明符：``{曲目:02d}``、``{碟号:02d}``
* 支持子目录：``{专辑}/{碟号}-{曲目:02d} {歌名}``
* Windows 非法字符自动替换、保留设备名规避、尾部空格与点清除、路径长度保护
* 先生成计划供预览，确认后才落盘；同名冲突与「互换名」循环均安全处理
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .model import Track

#: 中文/英文变量别名 -> 内部字段
ALIASES = {
    "歌手": "artist",
    "艺术家": "artist",
    "artist": "artist",
    "歌名": "title",
    "标题": "title",
    "title": "title",
    "专辑": "album",
    "album": "album",
    "专辑艺术家": "albumartist",
    "albumartist": "albumartist",
    "年份": "year",
    "年代": "year",
    "year": "year",
    "date": "year",
    "流派": "genre",
    "genre": "genre",
    "曲目": "track",
    "音轨": "track",
    "track": "track",
    "碟号": "disc",
    "disc": "disc",
    "格式": "format",
    "format": "format",
    "位深": "bitdepth",
    "采样率": "samplerate",
    "时长": "duration",
}

DEFAULT_TEMPLATE = "{歌手} - {歌名}"

_INVALID_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_TOKEN = re.compile(r"\{([^{}:]+)(?::([^{}]+))?\}")

MAX_PATH = 250  # Windows 260 减去安全余量


@dataclass
class RenamePlanItem:
    track: Track
    old_path: Path
    new_path: Path
    ok: bool = True
    reason: str = ""

    @property
    def changed(self) -> bool:
        return self.ok and self.old_path != self.new_path


@dataclass
class RenamePlan:
    items: list[RenamePlanItem] = field(default_factory=list)
    template: str = DEFAULT_TEMPLATE
    conflicts: list[str] = field(default_factory=list)

    @property
    def pending(self) -> list[RenamePlanItem]:
        return [i for i in self.items if i.changed]

    @property
    def blocked(self) -> list[RenamePlanItem]:
        return [i for i in self.items if not i.ok]


# ---------------------------------------------------------------- 字段取值


def _field_value(track: Track, key: str) -> str:
    tags = track.working
    info = track.info
    mapping = {
        "artist": tags.artist or tags.albumartist,
        "title": tags.title or track.path.stem,
        "album": tags.album,
        "albumartist": tags.albumartist or tags.artist,
        "year": (tags.date or "")[:4],
        "genre": tags.genre,
        "track": (tags.track or "").split("/")[0].strip(),
        "disc": (tags.disc or "").split("/")[0].strip(),
        "format": info.fmt.lower() or track.path.suffix.lstrip(".").lower(),
        "bitdepth": str(info.bit_depth or ""),
        "samplerate": str(info.sample_rate or ""),
        "duration": info.duration_text,
    }
    return mapping.get(key, "")


def sanitize_component(text: str) -> str:
    """清理单个路径片段：非法字符、保留名、尾部空格与点。"""
    text = _INVALID_CHARS.sub("_", text or "")
    text = text.replace("\t", " ").strip()
    # 空字段会让模板留下悬空的分隔符（如「-02」），清除首尾分隔符
    text = text.strip(" -_|·,、.")
    text = re.sub(r"\s{2,}", " ", text).rstrip(" .")
    if not text:
        return "_"
    stem_upper = text.split(".")[0].upper()
    if stem_upper in _RESERVED_NAMES:
        text = f"_{text}"
    return text


def render_name(template: str, track: Track) -> str:
    """渲染模板为相对路径（可能含子目录）。"""
    def _replace(match: re.Match[str]) -> str:
        raw_key, spec = match.group(1).strip(), match.group(2)
        key = ALIASES.get(raw_key.lower(), ALIASES.get(raw_key, ""))
        if not key:
            return ""
        value = _field_value(track, key)
        if not value:
            return ""
        if spec:
            try:
                if "d" in spec:
                    number = int(str(value).split("/")[0] or 0)
                    return format(number, spec)
                return format(value, spec)
            except (ValueError, TypeError):
                pass
        # 字段值内的 / \ 不能生成子目录，统一替换为下划线；
        # 只有模板里书写的 / 才具备目录语义
        return _INVALID_CHARS.sub("_", value)

    rendered = _TOKEN.sub(_replace, template or DEFAULT_TEMPLATE)
    rendered = rendered.replace("\\", "/")
    parts = [sanitize_component(p) for p in rendered.split("/") if p.strip()]
    if not parts:
        parts = [sanitize_component(track.path.stem)]
    parts[-1] = parts[-1] + track.path.suffix.lower()
    return "/".join(parts)


def build_plan(tracks: list[Track], template: str = DEFAULT_TEMPLATE) -> RenamePlan:
    """生成重命名计划（纯计算，不落盘）。"""
    plan = RenamePlan(template=template)
    seen: dict[str, Path] = {}
    for track in tracks:
        old = track.path
        try:
            relative = render_name(template, track)
            new = old.parent / relative.replace("/", os.sep)
        except Exception as exc:  # noqa: BLE001
            plan.items.append(RenamePlanItem(track, old, old, False, f"模板渲染失败：{exc}"))
            continue

        item = RenamePlanItem(track=track, old_path=old, new_path=new)
        if len(str(new)) > MAX_PATH:
            item.ok, item.reason = False, f"目标路径过长（{len(str(new))} 字符）"
        elif not old.exists():
            item.ok, item.reason = False, "源文件不存在"
        elif new.exists() and new.resolve() != old.resolve():
            # 目标被其他文件占用
            item.ok, item.reason = False, "目标文件已存在"
        key = str(new).lower()
        if key in seen and seen[key] != old:
            item.ok, item.reason = False, "与其他曲目重名"
            plan.conflicts.append(str(new))
        else:
            seen[key] = old
        plan.items.append(item)
    return plan


def execute_plan(plan: RenamePlan, *, dry_run: bool = False) -> tuple[int, int]:
    """执行重命名，返回 (成功数, 失败数)。

    通过两阶段「先改临时名再改目标名」避免 A↔B 互换时互相覆盖。
    """
    pending = plan.pending
    if dry_run or not pending:
        return 0, len(plan.blocked)

    staged: list[tuple[RenamePlanItem, Path]] = []
    success = failed = 0
    for index, item in enumerate(pending):
        temp = item.old_path.with_name(f".seq_ren_{os.getpid()}_{index}{item.old_path.suffix}")
        try:
            item.old_path.rename(temp)
            staged.append((item, temp))
        except OSError as exc:
            item.ok, item.reason = False, f"暂存失败：{exc}"
            failed += 1

    for item, temp in staged:
        try:
            item.new_path.parent.mkdir(parents=True, exist_ok=True)
            temp.rename(item.new_path)
            item.track.path = item.new_path
            success += 1
        except OSError as exc:
            item.ok, item.reason = False, f"重命名失败：{exc}"
            try:
                temp.rename(item.old_path)
            except OSError:
                item.reason += "；且回滚失败，请手动处理"
            failed += 1

    failed += len(plan.blocked)
    return success, failed


def preview(plan: RenamePlan, limit: int = 500) -> list[tuple[str, str, str]]:
    """返回 ``(状态, 原文件名, 新文件名)`` 列表，供 UI 表格展示。"""
    rows: list[tuple[str, str, str]] = []
    for item in plan.items[:limit]:
        status = "待改名" if item.changed else ("跳过" if item.ok else "冲突")
        rows.append((status, item.old_path.name, item.new_path.name if item.ok else item.reason))
    return rows
