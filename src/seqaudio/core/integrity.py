"""音频完整性校验（需求 3.6.2）。

两档能力
--------
1. **结构校验（默认，快）**：纯 Python 遍历容器结构 —— FLAC 的 metadata block 链、
   STREAMINFO 合理性、音频数据区非空；APE/WAV 的头部一致性。不依赖外部工具。
2. **深度校验（可选）**：检测到系统装有 ``flac.exe`` 时调用 ``flac -t`` 做完整 MD5 校验。
   未安装则明确返回「未执行」，绝不假装通过 —— 需求只要求检测上报，不要求修复。
"""

from __future__ import annotations

import shutil
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CheckResult:
    ok: bool = True
    level: str = "结构"
    message: str = ""

    @property
    def text(self) -> str:
        return f"[{self.level}] {self.message or ('正常' if self.ok else '异常')}"


def _read_flac_blocks(path: Path) -> tuple[bool, str, dict]:
    """遍历 FLAC metadata block 链，返回 (完整, 说明, streaminfo 摘要)。"""
    summary: dict = {}
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
            if magic != b"fLaC":
                return False, "缺少 fLaC 标识", summary
            last = False
            count = 0
            while not last:
                header = handle.read(4)
                if len(header) < 4:
                    return False, "metadata block 链被截断", summary
                block_type = header[0] & 0x7F
                last = bool(header[0] & 0x80)
                length = int.from_bytes(header[1:4], "big")
                if length < 0 or length > 16 * 1024 * 1024:
                    return False, f"异常 block 长度 {length}", summary
                payload = handle.read(length)
                if len(payload) < length:
                    return False, f"block 数据被截断（type={block_type}）", summary
                if block_type == 0 and len(payload) >= 34:
                    min_block, max_block = struct.unpack(">HH", payload[0:4])
                    min_frame, max_frame = struct.unpack(">I", payload[4:8])[0], struct.unpack(">I", payload[8:12])[0]
                    sample_rate = (payload[12] << 12) | (payload[13] << 4) | (payload[14] >> 4)
                    channels = ((payload[14] >> 1) & 0x07) + 1
                    bps = (((payload[14] & 0x01) << 4) | (payload[15] >> 4)) + 1
                    total_samples = ((payload[15] & 0x0F) << 32) | int.from_bytes(payload[16:20], "big")
                    md5 = payload[18:34].hex()
                    summary = {
                        "sample_rate": sample_rate,
                        "channels": channels,
                        "bit_depth": bps,
                        "total_samples": total_samples,
                        "md5": md5,
                        "min_block": min_block,
                        "max_block": max_block,
                    }
                    if sample_rate == 0:
                        return False, "STREAMINFO 采样率为 0", summary
                    if total_samples == 0:
                        return False, "STREAMINFO 总采样数为 0", summary
                    if bps == 0 or bps > 32:
                        return False, f"STREAMINFO 位深异常 {bps}", summary
                count += 1
                if count > 64:
                    return False, "metadata block 数量异常", summary
            position = handle.tell()
            handle.seek(0, 2)
            end = handle.tell()
            if end - position <= 0:
                return False, "音频帧数据区为空", summary
    except OSError as exc:
        return False, f"读取失败：{exc}", summary
    return True, "FLAC 结构完整", summary


def _read_dsdiff_chunks(path: Path) -> tuple[bool, str, dict]:
    """遍历 DSDIFF 的 chunk 链。

    DFF 写标签是在容器末尾追加 ID3 chunk，并同步 FRM8 的总长度字段 ——
    所以「FRM8 声明的总长 == 文件实际大小」是判断文件有没有被写坏的
    最直接证据。同时确认 DSD/DST 音频块仍然存在。
    """
    summary: dict = {}
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
            if len(head) < 16:
                return False, "文件头不完整", summary
            cid, size, form = struct.unpack(">4sQ4s", head)
            if cid != b"FRM8":
                return False, "缺少 FRM8 标识", summary
            if form not in (b"DSD ", b"DST "):
                return False, f"未知表单类型 {form!r}", summary

            handle.seek(0, 2)
            total = handle.tell()
            pos = 16
            names: list[str] = []
            audio_size = 0
            guard = 0
            while pos + 12 <= total:
                handle.seek(pos)
                header = handle.read(12)
                if len(header) < 12:
                    break
                chunk_id, chunk_size = struct.unpack(">4sQ", header)
                name = chunk_id.decode("ascii", "replace")
                if chunk_size > total:
                    return False, f"{name} 块长度异常（{chunk_size}）", summary
                names.append(name)
                if name in ("DSD ", "DST "):
                    audio_size += chunk_size
                pos += 12 + chunk_size + (chunk_size & 1)
                guard += 1
                if guard > 64:
                    return False, "chunk 数量异常", summary

            declared = size + 12
            summary = {
                "chunks": names,
                "audio_size": audio_size,
                "has_id3": "ID3 " in names,
                "declared": declared,
                "actual": total,
            }
            if declared != total:
                return False, f"FRM8 声明长度 {declared} 与实际 {total} 不符", summary
            if audio_size <= 0:
                return False, "缺少 DSD/DST 音频数据块", summary
    except OSError as exc:
        return False, f"读取失败：{exc}", summary
    return True, "DSDIFF 结构完整", summary


def check_structure(path: Path | str) -> CheckResult:
    path = Path(path)
    ext = path.suffix.lower()
    if not path.exists():
        return CheckResult(False, "文件", "文件不存在")
    if path.stat().st_size == 0:
        return CheckResult(False, "文件", "空文件")

    if ext == ".flac":
        ok, message, summary = _read_flac_blocks(path)
        if not ok:
            return CheckResult(False, "结构", message)
        md5 = summary.get("md5", "")
        if md5 and set(md5) <= {"0"}:
            return CheckResult(True, "结构", "结构完整（STREAMINFO 未写入 MD5）")
        return CheckResult(True, "结构", "结构完整")

    if ext in (".ape", ".wv"):
        with path.open("rb") as handle:
            head = handle.read(8)
        magic_ok = head.startswith(b"MAC ") or head.startswith(b"wvpk")
        return CheckResult(magic_ok, "结构", "头部标识正常" if magic_ok else "头部标识异常")

    if ext in (".wav", ".wave"):
        with path.open("rb") as handle:
            head = handle.read(12)
        if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
            return CheckResult(False, "结构", "RIFF/WAVE 标识异常")
        return CheckResult(True, "结构", "RIFF 结构正常")

    if ext == ".dff":
        ok, message, summary = _read_dsdiff_chunks(path)
        if not ok:
            return CheckResult(False, "结构", message)
        mb = summary.get("audio_size", 0) / 1024 / 1024
        tag = "含 ID3 标签块" if summary.get("has_id3") else "无标签块"
        return CheckResult(True, "结构", f"DSDIFF 完整（音频 {mb:.0f}MB，{tag}）")

    if ext == ".dsf":
        with path.open("rb") as handle:
            head = handle.read(4)
        ok = head == b"DSD "
        return CheckResult(ok, "结构", "DSF 标识正常" if ok else "DSF 标识异常")

    if ext in (".mp3",):
        # 有损文件无法无损校验，只确认非空且能被解析
        from .tags import open_audio

        obj = open_audio(path)
        return CheckResult(obj is not None, "结构", "可解析" if obj is not None else "解析失败")

    return CheckResult(True, "结构", "该格式无内置校验，跳过")


def find_flac_cli() -> Optional[str]:
    return shutil.which("flac")


def deep_check_flac(path: Path | str, timeout: float = 300.0) -> CheckResult:
    """调用官方 ``flac -t`` 做完整 MD5 解码校验（需系统装有 libFLAC 工具）。"""
    path = Path(path)
    cli = find_flac_cli()
    if cli is None:
        return CheckResult(True, "深度", "未检测到 flac.exe，深度校验未执行")
    try:
        proc = subprocess.run(
            [cli, "-t", "-s", str(path)],
            capture_output=True,
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return CheckResult(False, "深度", f"校验执行失败：{exc}")
    if proc.returncode == 0:
        return CheckResult(True, "深度", "MD5 校验通过")
    detail = (proc.stderr or b"").decode("utf-8", "replace").strip()[:200]
    return CheckResult(False, "深度", f"MD5 校验未通过：{detail or proc.returncode}")
