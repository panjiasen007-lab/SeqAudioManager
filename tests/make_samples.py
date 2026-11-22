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
        "dsf": make_dsf(base / "sample.dsf", blocks=2),
        "dff": make_dff(base / "sample.dff", byte_frames=256),
    }


# ---------------------------------------------------------------- DSD 夹具
#
# DSF/DFF 都没有可用的编码器（ffmpeg 只能解，不能造），所以这里按规范手写
# 容器头 + 确定性位流。位流**必须**可复现：单元测试要拿同一套字节做逐位比对，
# 用 random 会让失败变得无法定位。


def dsd_channel_bytes(channel: int, length: int) -> bytes:
    """生成某声道 ``length`` 字节的确定性位流（``0x00``–``0xFF`` 全覆盖）。

    取一对互质的乘数，保证相邻声道、相邻位置的字节都不同 —— 若解交错把声道
    或块顺序搞错了，比对会立刻失败，而不是「碰巧一样」。
    """
    return bytes(((channel + 1) * 37 + pos * 11 + (pos >> 3)) & 0xFF for pos in range(length))


def make_dsf(
    path: Path,
    *,
    dsd_rate: int = 2822400,
    channels: int = 2,
    block_bytes: int = 4096,
    blocks: int = 4,
) -> Path:
    """构造一个合法的 DSF 文件（DSD 位流按块交错、每字节 LSB first 的容器）。

    三处必须照规范写，否则 ``probe_dsd`` 与真实文件的行为会分叉：

    1. ``DSD `` chunk 的 ``size`` 字段**含** 12 字节头（值恒为 28）；
    2. ``fmt `` chunk 的 ``size`` 同样含头（值恒为 52），载荷 40 字节；
    3. ``data`` chunk 的 ``size`` 也是「载荷 + 12」。

    位流布局：``block0.ch0(block_bytes) · block0.ch1 · block1.ch0 …``。
    """
    samples_per_channel = block_bytes * blocks
    payload = bytearray()
    for block_index in range(blocks):
        chunk = dsd_channel_bytes(block_index, block_bytes)
        for channel in range(channels):
            # 每个块内先放 ch0 的所有字节，再放 ch1 —— 这正是 DSF 的交错方式
            payload += bytes(((byte + channel * 61) & 0xFF) for byte in chunk)

    sample_count_bits = samples_per_channel * 8
    fmt_payload = struct.pack(
        "<IIIIIIQII",
        1,                      # formatVersion
        0,                      # formatID（0 = DSD raw）
        2 if channels > 1 else 0,  # channelType
        channels,
        dsd_rate,
        1,                      # bitsPerSample（DSD 恒为 1）
        sample_count_bits,
        block_bytes,            # blockSizePerChannel
        0,                      # reserved
    )
    assert len(fmt_payload) == 40, len(fmt_payload)

    data_size = 12 + len(payload)
    total_size = 28 + 52 + data_size
    # 元数据指针指向文件末尾（= 没有 ID3v2 元数据块）。DSF 允许这么写，
    # 我们的解析器也不读它，但要给它一个合法值，别留 0。
    metadata_ptr = total_size

    header = b"DSD " + struct.pack("<QQQ", 28, total_size, metadata_ptr)
    fmt_chunk = b"fmt " + struct.pack("<Q", 52) + fmt_payload
    data_chunk = b"data" + struct.pack("<Q", data_size) + bytes(payload)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + fmt_chunk + data_chunk)
    return path


def make_dff(
    path: Path,
    *,
    dsd_rate: int = 2822400,
    channels: int = 2,
    byte_frames: int = 512,
) -> Path:
    """构造一个合法的 DFF（DSDIFF）文件。

    与 DSF 的三处差别都在这里体现：**大端**长度、chunk ``size`` **不含** 12
    字节头（IFF 惯例）、位流按字节帧交错（``ch0, ch1, ch0, ch1 …``）且每字节
    **MSB first**。
    """
    payload = bytearray()
    per_channel = byte_frames
    streams = [dsd_channel_bytes(channel + 1, per_channel) for channel in range(channels)]
    for frame in range(byte_frames):
        for channel in range(channels):
            payload.append(streams[channel][frame])

    def _chunk(chunk_id: bytes, body: bytes) -> bytes:
        # DFF：size 只是载荷长度，奇数长度补 1 字节
        return chunk_id + struct.pack(">Q", len(body)) + body + (b"\x00" if len(body) % 2 else b"")

    fver = _chunk(b"FVER", struct.pack(">I", 0x01050000))
    fs = _chunk(b"FS  ", struct.pack(">I", dsd_rate))
    chnl = _chunk(
        b"CHNL",
        struct.pack(">H", channels) + b"".join(struct.pack(">I", 0) for _ in range(channels)),
    )
    snd = b"SND " + struct.pack(">Q", len(fs) + len(chnl)) + fs + chnl
    prop = _chunk(b"PROP", snd)
    dsd = _chunk(b"DSD ", bytes(payload))

    body = b"DSD " + fver + prop + dsd
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"FRM8" + struct.pack(">Q", len(body)) + body)
    return path


if __name__ == "__main__":
    files = build_all()
    for name, path in files.items():
        print(f"{name:6s} {path}  {path.stat().st_size} bytes")
