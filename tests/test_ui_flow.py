"""界面级冒烟测试：真实 Qt 线程跑完整批处理链路（离屏模式，无需显示器）。

    python tests/test_ui_flow.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import make_samples  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402

from seqaudio.app import create_app  # noqa: E402
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.core.logging_setup import setup_logging  # noqa: E402
from seqaudio.core.model import Tags, TrackStatus  # noqa: E402
from seqaudio.core.probe import probe  # noqa: E402
from seqaudio.core.tags import read_tags  # noqa: E402
from seqaudio.pipeline.processor import BatchOptions  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402


def main() -> int:
    setup_logging(to_console=False)
    tmp = Path(tempfile.mkdtemp(prefix="seq_ui_"))
    audio_dir = tmp / "audio"
    files = make_samples.build_all(audio_dir)

    app = create_app([])
    settings = Settings(
        backup_enabled=True,
        backup_root=str(tmp / "backup"),
        min_duration_sec=0,
        offline_mode=True,
        online_enabled=False,
        watermark_enabled=True,
        lyrics_mode="内嵌到标签",
        rename_enabled=False,
    )
    window = MainWindow(settings)
    window.show()

    tracks = [probe(p, with_cue=False) for p in files.values() if p.suffix != ".cue"]
    # 制造一条带水印的标签，验证清理链路
    tracks[0].working = Tags(
        title="测试歌曲【www.example-music.com】",
        artist="测试歌手",
        comment="下载自 www.example-music.com 更多音乐请访问本站",
    )
    tracks[0].original = tracks[0].working.copy()
    window.model.add_tracks(tracks)
    assert window.model.rowCount() == 3, window.model.rowCount()

    results: dict[str, object] = {}

    def on_finished(summary) -> None:
        results["summary"] = summary
        app.quit()

    def start() -> None:
        options = BatchOptions(
            match_online=False,
            clean_watermark=True,
            write_tags=True,
            fetch_lyrics=True,
            do_rename=False,
        )
        window.settings_panel.apply_to(settings)
        worker = __import__("seqaudio.ui.worker", fromlist=["ProcessorWorker"]).ProcessorWorker(
            settings, tracks, options
        )
        results["worker"] = worker
        worker.finishedRun.connect(on_finished)
        worker.start()

    QTimer.singleShot(50, start)
    QTimer.singleShot(30000, app.quit)  # 兜底超时
    app.exec()

    summary = results.get("summary")
    ok = True
    if summary is None:
        print("✗ 批处理未在超时内完成")
        ok = False
    else:
        print("批处理结果：", summary.text())

    for track in tracks:
        print(f"  {track.path.name:12s} {track.status.value:6s} | "
              f"{(track.working.title or '')[:20]:22s} | {' '.join(track.notes)[:70]}")

    # 校验：水印被清掉、标签真的写进磁盘
    wav = files["wav"]
    disk = read_tags(wav)
    if "网站" in disk.comment or "example-music" in disk.comment:
        print("✗ 水印未清理：", disk.comment)
        ok = False
    if "example-music" in disk.title:
        print("✗ 标题水印未清理：", disk.title)
        ok = False
    if disk.artist != "测试歌手":
        print("✗ 标签未写入：artist =", disk.artist)
        ok = False
    if not list((tmp / "backup").rglob("*.wav")):
        print("✗ 未生成备份文件")
        ok = False

    all_done = all(t.status in (TrackStatus.DONE, TrackStatus.SKIPPED) for t in tracks)
    if not all_done:
        print("✗ 存在未完成曲目")
        ok = False

    # 参数面板：修改 → 脏标记 → 还原（不写磁盘，避免污染用户配置）
    panel = window.settings_panel
    if panel.is_dirty():
        print("✗ 参数面板初始不应处于未保存状态")
        ok = False
    original_recursive = panel.recursive_check.isChecked()
    panel.recursive_check.setChecked(not original_recursive)
    if not panel.is_dirty():
        print("✗ 修改参数后应标记为未保存")
        ok = False
    if settings.recursive_scan == (not original_recursive):
        print("✗ 未点保存前 settings 不应被改动")
        ok = False
    panel.revert()
    if panel.is_dirty() or panel.recursive_check.isChecked() != original_recursive:
        print("✗ 还原后应清除脏标记并恢复原值")
        ok = False

    # 批量标签编辑：只有勾选的字段才修改，未勾选保持原值（V1.1）
    from seqaudio.ui.dialogs import BatchTagEditDialog

    target = tracks[0]
    target.working.artist = "原歌手"
    target.working.album = "原专辑"
    target.working.genre = "原流派"

    dialog = BatchTagEditDialog([target], window)
    dialog.checks["artist"].setChecked(True)
    dialog.edits["artist"].setText("批量歌手")
    values = dialog.result_values()
    if set(values) != {"artist"}:
        print("✗ 批量编辑只应返回勾选字段：", values)
        ok = False
    if target.working.album != "原专辑":
        print("✗ 勾选阶段不应改动标签")
        ok = False

    window._apply_batch_values([target], values)
    if target.working.artist != "批量歌手":
        print("✗ 批量编辑未生效")
        ok = False
    if target.working.album != "原专辑" or target.working.genre != "原流派":
        print("✗ 未勾选字段被误改")
        ok = False
    if "artist" not in target.changed_fields:
        print("✗ 批量编辑未记录变更字段")
        ok = False

    window.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n界面流程测试：" + ("通过" if ok else "失败"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
