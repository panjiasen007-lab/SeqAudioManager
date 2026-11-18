"""实证：播放联动的全部用户反馈场景（第二轮修复的验收）。

场景：
1. 双击起播 → 清空列表 → 引擎立即停（IDLE、无输出）
2. 再起播 → 移除正在播放的行 → 引擎立即停
3. 空列表按播放 → 拒绝（保持 IDLE）
4. 无选中按播放 → 播第一首
5. 选中第二首按播放 → 播选中的
"""
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.core.probe import probe  # noqa: E402
from seqaudio.player.constants import PlayerState  # noqa: E402
from seqaudio.ui.main_window import MainWindow  # noqa: E402

app = QApplication.instance() or QApplication([])

import subprocess  # noqa: E402

from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402

FF = find_ffmpeg()
tmp = Path(tempfile.gettempdir())
song_a, song_b = tmp / "_sim_a.wav", tmp / "_sim_b.wav"
for f, freq in ((song_a, 440), (song_b, 880)):
    if not f.exists():
        subprocess.run(
            [FF, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration=8",
             "-ar", "44100", "-ac", "2", "-y", str(f)],
            capture_output=True,
        )

window = MainWindow(Settings())
window.show()
app.processEvents()
tracks = [probe(song_a), probe(song_b)]
window.model.set_tracks(list(tracks))
app.processEvents()

ok = True


def wait_playing(timeout_s: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        app.processEvents()
        if window.player_bridge.engine.state is PlayerState.PLAYING:
            return
        time.sleep(0.03)
    app.processEvents()


def drain(ms: float = 0.9) -> None:
    deadline = time.monotonic() + ms
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


def snapshot_line(tag: str) -> None:
    engine = window.player_bridge.engine
    cur = engine.current_item
    snap = window.player_bridge.last_snapshot() or {}
    print(
        f"{tag}: 状态={engine.state.name} "
        f"当前={Path(str(cur.path)).name if cur else None} "
        f"输出率={snap.get('rate')}"
    )


def expect(cond: bool, tag: str) -> None:
    if not cond:
        print(f"  ✗ {tag} 不符")
        globals()["ok"] = False


def load_list() -> None:
    # 传副本：模型的 remove_rows/clear 会就地修改传入列表，引用共享会把
    # 测试素材列表也改掉（场景 2 的移除会让场景 4 的"第一首"变成 b）。
    window.model.set_tracks(list(tracks))
    app.processEvents()


# ---- 场景 1：双击起播 → 清空列表 → 立即停 ----
window._play_track(tracks[0])
wait_playing()
expect(window.player_bridge.engine.state is PlayerState.PLAYING, "场景1 起播")
snapshot_line("场景1 起播后")
with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
    window.clear_all()
drain()
snapshot_line("场景1 清空后")
expect(window.player_bridge.engine.state is PlayerState.IDLE, "场景1 清空后引擎停")
expect(not (window.player_bridge.last_snapshot() or {}).get("rate"), "场景1 无输出")

# ---- 场景 2：移除正在播放的行 → 立即停 ----
load_list()
window._play_track(tracks[0])
wait_playing()
expect(window.player_bridge.engine.state is PlayerState.PLAYING, "场景2 起播")
window.table.selectRow(0)
app.processEvents()
with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
    window.remove_selected()
drain()
snapshot_line("场景2 移除播放中后")
expect(window.player_bridge.engine.state is PlayerState.IDLE, "场景2 移除后引擎停")
expect(window.model.rowCount() == 1, "场景2 剩 1 行")

# ---- 场景 3：真·空列表按播放 → 拒绝 ----
with patch.object(QMessageBox, "question", return_value=QMessageBox.Yes):
    window.clear_all()
drain()
window.player_toggle()
drain()
snapshot_line("场景3 空列表按播放")
expect(window.player_bridge.engine.state is PlayerState.IDLE, "场景3 空列表拒绝起播")

# ---- 场景 4：无选中按播放 → 播第一首 ----
load_list()
window.table.clearSelection()
app.processEvents()
sel = window.table.selectionModel().selectedRows()
expect(not sel, "场景4 前置：确实无选中")
print("场景4 前置: selected =", [Path(str(t.path)).name for t in window._selected_tracks()],
      "| 队列 =", [Path(i["path"]).name for i in window.player_bridge.engine.queue.snapshot()],
      "| index =", window.player_bridge.engine.queue.index)
window.player_toggle()
wait_playing()
snapshot_line("场景4 无选中播放")
cur = window.player_bridge.engine.current_item
expect(cur is not None and Path(str(cur.path)).name == song_a.name, "场景4 播第一首")

# ---- 场景 5：选中第二首按播放 → 播选中的 ----
window.player_bridge.stop()
drain()
window.table.selectRow(1)
app.processEvents()
window.player_toggle()
wait_playing()
snapshot_line("场景5 选中播放")
cur = window.player_bridge.engine.current_item
expect(cur is not None and Path(str(cur.path)).name == song_b.name, "场景5 播选中的")

window.close()
print("\n结论:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
