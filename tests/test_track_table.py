"""曲目表格模型的删行补位与「正在播放」标记回归。

运行：``python -m unittest tests.test_track_table -q``（离屏，不需要显示器、不碰声卡）。

为什么单独建文件
----------------
这两个行为都只发生在模型层（``QAbstractTableModel``）：
1. ``remove_rows`` 漏了 ``endRemoveRows`` 会让视图残留空行 —— 是纯 Qt 契约问题；
2. ``PlayingRole`` / ``playing_row`` 是列表高亮与自动定位的唯一数据源。
用 ``QTableView + TrackFilterProxy`` 组装真实视图链（不建 MainWindow），
断言的是**模型契约**，跑得快且不受播放引擎影响。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QTableView  # noqa: E402

from seqaudio.core.model import Track  # noqa: E402
from seqaudio.ui.track_model import TrackFilterProxy, TrackTableModel  # noqa: E402


def app() -> QApplication:
    instance = QApplication.instance()
    return instance if instance is not None else QApplication([])


def make_tracks(count: int = 6) -> list[Track]:
    tracks = []
    for i in range(count):
        track = Track(path=Path(f"D:/fake/音序测试/{i}.flac"))
        track.original.title = f"曲目{i}"
        tracks.append(track)
    return tracks


class RemoveRowsTest(unittest.TestCase):
    """需求：移除/删除一首或多首后不得留空行，其余行必须连续补位。"""

    def setUp(self) -> None:
        app()

    def test_remove_single_row_keeps_rows_contiguous(self) -> None:
        model = TrackTableModel(make_tracks(5))
        removed = model.remove_rows([2])
        self.assertEqual(removed, 1)
        self.assertEqual(model.rowCount(), 4)
        titles = [model.track_at(r).original.title for r in range(model.rowCount())]
        self.assertEqual(titles, ["曲目0", "曲目1", "曲目3", "曲目4"])

    def test_remove_multiple_rows_keeps_rows_contiguous(self) -> None:
        model = TrackTableModel(make_tracks(6))
        removed = model.remove_rows([1, 3, 4])
        self.assertEqual(removed, 3)
        self.assertEqual(model.rowCount(), 3)
        titles = [model.track_at(r).original.title for r in range(model.rowCount())]
        self.assertEqual(titles, ["曲目0", "曲目2", "曲目5"])

    def test_remove_all_rows_then_append_works(self) -> None:
        tracks = make_tracks(4)
        model = TrackTableModel(tracks)
        model.remove_rows([0, 1, 2, 3])
        self.assertEqual(model.rowCount(), 0)
        # 删行不得反向污染调用方传入的列表（模型内部持有的是拷贝）
        self.assertEqual(len(tracks), 4)
        self.assertEqual(model.add_tracks(tracks[:3]), 3)
        self.assertEqual(model.rowCount(), 3)

    def test_remove_rows_out_of_range_is_ignored(self) -> None:
        model = TrackTableModel(make_tracks(3))
        self.assertEqual(model.remove_rows([-1, 7]), 0)
        self.assertEqual(model.rowCount(), 3)

    def test_view_row_count_follows_model_after_removal(self) -> None:
        """视图（经代理）的行数必须同步收缩，否则界面上会留空行。"""
        model = TrackTableModel(make_tracks(6))
        proxy = TrackFilterProxy()
        proxy.setSourceModel(model)
        view = QTableView()
        view.setModel(proxy)
        self.assertEqual(view.model().rowCount(), 6)

        model.remove_rows([0, 2])
        self.assertEqual(view.model().rowCount(), 4)
        # 代理行到源行的映射在删行后仍应连续
        proxy_rows = [proxy.mapToSource(proxy.index(r, 0)).row() for r in range(4)]
        self.assertEqual(proxy_rows, [0, 1, 2, 3])
        view.deleteLater()

    def test_remove_tracks_by_object(self) -> None:
        tracks = make_tracks(5)
        model = TrackTableModel(tracks)
        self.assertEqual(model.remove_tracks([tracks[0], tracks[3]]), 2)
        titles = [model.track_at(r).original.title for r in range(model.rowCount())]
        self.assertEqual(titles, ["曲目1", "曲目2", "曲目4"])


class PlayingRowTest(unittest.TestCase):
    """需求：正在播放的曲目在列表里高亮并带标记，且播放行号跟随结构变化。"""

    def setUp(self) -> None:
        app()

    def test_set_playing_marks_role_and_bold(self) -> None:
        model = TrackTableModel(make_tracks(4))
        model.set_playing(model.track_at(2))
        self.assertEqual(model.playing_row(), 2)
        self.assertTrue(bool(model.index(2, 1).data(TrackTableModel.PlayingRole)))
        self.assertFalse(bool(model.index(0, 1).data(TrackTableModel.PlayingRole)))
        font = model.index(2, 1).data(Qt.FontRole)
        self.assertIsNotNone(font)
        self.assertTrue(font.bold())

    def test_clear_playing(self) -> None:
        model = TrackTableModel(make_tracks(3))
        model.set_playing(model.track_at(1))
        model.set_playing(None)
        self.assertEqual(model.playing_row(), -1)
        self.assertFalse(bool(model.index(1, 1).data(TrackTableModel.PlayingRole)))
        # 状态列原本没有 FontRole 分支：清除标记后必须回落到 None
        # （标题列本身有「有标题即加粗」的规则，不能用来判断播放标记）
        self.assertIsNone(model.index(1, 0).data(Qt.FontRole))

    def test_playing_row_shifts_after_removal(self) -> None:
        model = TrackTableModel(make_tracks(5))
        model.set_playing(model.track_at(3))
        self.assertEqual(model.playing_row(), 3)
        model.remove_rows([0])
        self.assertEqual(model.playing_row(), 2)
        self.assertTrue(bool(model.index(2, 1).data(TrackTableModel.PlayingRole)))

    def test_playing_row_dropped_when_track_removed(self) -> None:
        model = TrackTableModel(make_tracks(4))
        model.set_playing(model.track_at(1))
        model.remove_rows([1])
        self.assertEqual(model.playing_row(), -1)

    def test_playing_row_survives_reset(self) -> None:
        tracks = make_tracks(4)
        model = TrackTableModel(tracks)
        model.set_playing(tracks[2])
        # set_tracks 会 reset 模型，播放曲目若还在列表里应继续保持标记
        model.set_tracks(tracks)
        self.assertEqual(model.playing_row(), 2)

    def test_proxy_maps_playing_row_for_scroll(self) -> None:
        """自动定位依赖 index → proxy 映射，映射结果必须有效。"""
        model = TrackTableModel(make_tracks(5))
        proxy = TrackFilterProxy()
        proxy.setSourceModel(model)
        model.set_playing(model.track_at(4))
        proxy_index = proxy.mapFromSource(model.index(model.playing_row(), 0))
        self.assertTrue(proxy_index.isValid())
        self.assertEqual(proxy.mapToSource(proxy_index).row(), 4)


if __name__ == "__main__":
    unittest.main()
