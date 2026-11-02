"""生成最小可用的测试音频样本（不依赖 ffmpeg，纯字节构造）。

用于端到端验证：导入 → 读标签 → 水印清理 → 安全写入 → 备份回滚 → 重命名 → 报表。
"""

from __future__ import annotations

import struct
from pathlib import Path

BASE = Path(__file__).resolve().parent / "_tmp"


def make_wav(path: Path, seconds: float = 2.0, sample_rate: int = 44100, channels: int = 2) -> Path:
    """构造 16bit PCM 静音 WAV。"""
    frames = int(seconds * sample_rate)
    data = b"\x00\x00" * frames * channels
    fmt = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,
        1,                      # PCM
        channels,
        sample_rate,
        sample_rate * channels * 2,
        channels * 2,
        16,
    )
    data_chunk = struct.pack("<4sI", b"data", len(data)) + data
    body = b"WAVE" + fmt + data_chunk
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<4sI", b"RIFF", len(body)) + body)
    return path


def make_flac(path: Path, seconds: float = 3.0, sample_rate: int = 44100) -> Path:
    """构造带合法 STREAMINFO 的 FLAC 容器（音频帧为零字节占位，仅供标签测试）。"""
    total_samples = int(seconds * sample_rate)
    channels, bps = 2, 16
    # STREAMINFO 34 bytes
    min_block = max_block = 4096
    min_frame = max_frame = 0
    packed = (
        (sample_rate << 44) | ((channels - 1) << 41) | ((bps - 1) << 36) | total_samples
    ).to_bytes(8, "big")
    streaminfo = (
        struct.pack(">HH", min_block, max_block)
        + min_frame.to_bytes(3, "big")
        + max_frame.to_bytes(3, "big")
        + packed
        + b"\x00" * 16              # MD5 留空
    )
    assert len(streaminfo) == 34
    header = bytes([0x00]) + len(streaminfo).to_bytes(3, "big")  # 非最后一块
    padding = bytes([0x81]) + (8).to_bytes(3, "big") + b"\x00" * 8  # PADDING 且 LAST
    fake_audio = b"\xff\xf8" + b"\x00" * 62
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fLaC" + header + streaminfo + padding + fake_audio)
    return path


def make_mp3(path: Path, seconds: float = 2.0, sample_rate: int = 44100, bitrate: int = 128000) -> Path:
    """构造 MPEG1 Layer3 静音帧序列（无 ID3 头，由 mutagen 首次写入时创建）。"""
    frame_len = int(144 * bitrate / sample_rate)
    frames = int(seconds * sample_rate / 1152)
    header = b"\xff\xfb\x90\x64"
    frame = header + b"\x00" * (frame_len - len(header))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(frame * max(1, frames))
    return path


def make_cue(path: Path, encoding: str = "gbk") -> Path:
    content = (
        'REM GENRE Pop\n'
        'REM DATE 2011\n'
        'PERFORMER "陈奕迅"\n'
        'TITLE "？"\n'
        'FILE "album.flac" WAVE\n'
        '  TRACK 01 AUDIO\n'
        '    TITLE "好久不见"\n'
        '    PERFORMER "陈奕迅"\n'
        '    INDEX 01 00:00:00\n'
        '  TRACK 02 AUDIO\n'
        '    TITLE "富士山下"\n'
        '    PERFORMER "陈奕迅"\n'
        '    INDEX 00 04:08:50\n'
        '    INDEX 01 04:10:50\n'
        '  TRACK 03 AUDIO\n'
        '    TITLE "爱情转移"\n'
        '    PERFORMER "陈奕迅"\n'
        '    INDEX 00 08:23:12\n'
        '    INDEX 01 08:25:12\n'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode(encoding))
    return path


def make_rolloff_wav(
    path: Path,
    *,
    knee_hz: float = 15000.0,
    slope_db_per_khz: float = 18.0,
    floor_db: float = -95.0,
    seconds: float = 4.0,
    sample_rate: int = 44100,
    seed: int = 42,
) -> Path:
    """生成「高频按指定斜率滚降」的白噪声 WAV，用于真假无损判定测试。

    * ``slope_db_per_khz`` 小（12~20）→ 平滑滚降，模拟老录音/模拟母带；
    * ``slope_db_per_khz`` 很大（>=200）→ 断崖，模拟有损转码砍高频。

    纯 numpy 构造：先造白噪声，在频域施加增益曲线，再逆变换回时域。
    """
    import numpy as np

    frames = int(seconds * sample_rate)
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft(rng.standard_normal(frames))
    freqs = np.fft.rfftfreq(frames, 1.0 / sample_rate)

    gain_db = np.zeros_like(freqs)
    above = freqs > knee_hz
    gain_db[above] = -slope_db_per_khz * (freqs[above] - knee_hz) / 1000.0
    gain_db = np.maximum(gain_db, floor_db)
    spectrum *= 10.0 ** (gain_db / 20.0)

    signal = np.fft.irfft(spectrum, n=frames)
    peak = float(np.max(np.abs(signal))) or 1.0
    signal = signal / peak * 0.8
    pcm = np.clip(signal * 32767.0, -32768.0, 32767.0).astype("<i2")

    channels = 1
    data = pcm.tobytes()
    fmt = struct.pack(
        "<4sIHHIIHH",
        b"fmt ",
        16,
        1,                      # PCM
        channels,
        sample_rate,
        sample_rate * channels * 2,
        channels * 2,
        16,
    )
    data_chunk = struct.pack("<4sI", b"data", len(data)) + data
    body = b"WAVE" + fmt + data_chunk
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<4sI", b"RIFF", len(body)) + body)
    return path


def build_all(base: Path = BASE) -> dict[str, Path]:
    base.mkdir(parents=True, exist_ok=True)
    return {
        "wav": make_wav(base / "sample.wav"),
        "flac": make_flac(base / "sample.flac"),
        "mp3": make_mp3(base / "sample.mp3"),
        "cue": make_cue(base / "album.cue"),
    }


if __name__ == "__main__":
    files = build_all()
    for name, path in files.items():
        print(f"{name:6s} {path}  {path.stat().st_size} bytes")
