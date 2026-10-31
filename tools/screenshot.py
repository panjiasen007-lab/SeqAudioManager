"""界面截图工具：填充示例数据后离屏渲染，用于快速迭代视觉稿。

    python tools/screenshot.py            # 输出 docs/screenshot.png
    python tools/screenshot.py --out a.png

注意：离屏平台（offscreen）下 Qt 拿不到系统字体家族，中文会显示成方框，
所以这里手动加载 C:/Windows/Fonts 下的字体。真实桌面不受影响。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _load_fonts() -> None:
    from PySide6.QtGui import QFontDatabase

    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\consola.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\segoeuib.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            QFontDatabase.addApplicationFont(path)


def _demo_tracks(tmp: Path):
    import make_samples
    from seqaudio.core.authenticity import AuthenticityResult
    from seqaudio.core.model import AudioInfo, Tags, TrackStatus
    from seqaudio.core.probe import probe

    files = make_samples.build_all(tmp / "shot")
    # (歌手, 歌名, 专辑, 年份, 格式, 无损, 位深, 采样率, 时长, 置信度, 来源, 判定)
    demo = [
        ("陈奕迅", "富士山下", "What's Going On…?", "2007", "FLAC", True, 24, 96000,
         259.0, 0.92, "MusicBrainz", "真无损"),
        ("周杰伦", "青花瓷", "我很忙", "2007", "FLAC", True, 16, 44100,
         236.0, 0.88, "MusicBrainz", "真无损"),
        ("李宗盛", "山丘", "山丘", "2013", "APE", True, 16, 44100,
         391.0, 0.71, "MusicBrainz", "假无损"),
        ("王菲", "红豆", "唱游", "1998", "DSF", True, None, 2822400,
         268.0, 0.95, "MusicBrainz", "真 DSD"),
        ("五月天", "突然好想你", "后青春期的诗", "2008", "MP3", False, None, 44100,
         355.0, 0.58, "LRCLIB", "假无损"),
        ("张学友", "吻别", "吻别", "1993", "WAV", True, 16, 44100,
         274.0, 0.64, "MusicBrainz", "疑似升频"),
    ]
    verdict_detail = {
        "真无损": ("截止 21.7kHz / 上限 22.1kHz", ["频谱平滑延伸到 21.7kHz（上限 22.1kHz）"], 0.85, 21700),
        "真 DSD": ("截止 176.3kHz / 上限 176.4kHz",
                  ["40kHz 以上能量 -14dB，符合 DSD 噪声整形特征"], 0.9, 176300),
        "假无损": ("截止 16.1kHz / 上限 22.1kHz", ["频谱在 16.1kHz 截止，符合 128kbps 有损转码特征"], 0.9, 16100),
        "疑似升频": ("截止 23.3kHz / 上限 48.0kHz",
                    ["采样率 96.0kHz，但频谱只到 23.3kHz（带宽利用率 49%），疑似 44.1kHz 内容升频"], 0.35, 23300),
    }

    tracks = []
    for i, (ar, ti, al, yr, fmt, ll, bd, sr, dur, conf, src, verdict) in enumerate(demo):
        path = tmp / f"{ar} - {ti}.{fmt.lower()}"
        path.write_bytes(b"x" * 1024)
        track = probe(files["flac"], with_cue=False)
        track.path = path
        track.info = AudioInfo(
            fmt=fmt, lossless=ll, bit_depth=bd, sample_rate=sr, duration=dur,
            bitrate=921600 if ll else 320000, channels=2, file_size=38_000_000,
            dsd_rate="DSD64" if fmt == "DSF" else None,
        )
        track.original = Tags(
            title=ti, artist=ar, album=al, date=yr, genre="流行", track=str(i + 1)
        )
        track.working = track.original.copy()
        track.confidence, track.source = conf, src
        track.status = TrackStatus.NEEDS_REVIEW if conf < 0.62 else TrackStatus.DONE
        track.notes = (
            ["待确认：最高分 58% 低于阈值 62%"]
            if conf < 0.62
            else [f"匹配：{ar} - {ti}（{conf:.0%}）", "写入成功（4 个字段，18ms）"]
        )
        detail, reasons, score, cutoff = verdict_detail[verdict]
        track.authenticity = AuthenticityResult(
            verdict=verdict, score=score, cutoff_hz=cutoff,
            nyquist_hz=(sr or 44100) // 2, steepness_db=6.0,
            bit_depth_declared=bd or 1, bit_depth_effective=bd or 1,
            high_band_energy_db=-14.0 if fmt == "DSF" else -70.0,
            analyzed_seconds=8.0, reasons=list(reasons),
        )
        tracks.append(track)
    return tracks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "docs" / "screenshot.png"))
    parser.add_argument("--width", type=int, default=1080)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--advance", type=int, default=90, help="截图前推进的动画帧数")
    args = parser.parse_args()

    from PySide6.QtWidgets import QApplication

    from seqaudio.app import create_app
    from seqaudio.core.config import Settings
    from seqaudio.core.logging_setup import setup_logging

    setup_logging(to_console=False)
    app = create_app([])
    _load_fonts()   # 兜底：随包字体已由 create_app 注册，这里补系统字体防缺字

    from seqaudio.ui.main_window import MainWindow

    window = MainWindow(Settings(offline_mode=True, min_duration_sec=0))
    window.resize(args.width, args.height)

    tracks = _demo_tracks(ROOT / "tests" / "_tmp")
    window.model.add_tracks(tracks)
    window._refresh_stats()

    for level, message in [
        ("INFO", "开始处理 6 首（联网匹配，覆盖策略：仅空白才填充）"),
        ("INFO", "陈奕迅 - 富士山下：MusicBrainz 命中，已获取封面"),
        ("INFO", "王菲 - 红豆：DSD64 整轨标签已流式读取（未加载音频体）"),
        ("WARNING", "李宗盛 - 山丘：置信度 71%，已写入但建议复核"),
        ("ERROR", "五月天 - 突然好想你：候选 58% 低于阈值，转待人工确认"),
        ("INFO", "共 6 首：完成 5，失败 0，待确认 1，跳过 0，不支持 0"),
    ]:
        window._append_log(level, message)

    window.show()
    # 让星场先跑一会儿：截图要截的是"正在飞行"的状态，不是初始的满屏远处小点
    for _ in range(args.advance):
        app.processEvents()
    window.table.selectRow(2)
    app.processEvents()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(out), "PNG")
    print(f"截图已保存：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
