"""播放列表对话框用例（offscreen，不弹模态框）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from seqaudio.core.playlists import PlaylistStore  # noqa: E402
from seqaudio.ui import i18n  # noqa: E402
from seqaudio.ui.playlist_dialog import PlaylistDialog  # noqa: E402

_APP = QApplication.instance() or QApplication([])


class TestPlaylistDialog(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="seq_pldlg_")
        self.store = PlaylistStore(Path(self.tmp.name) / "playlists.json")
        self.store.save("通勤", ["/m/1.flac", "/m/2.flac"])
        self.store.save("深夜", ["/n/1.flac"])
        self.dialog = PlaylistDialog(self.store, ["/cur/1.flac", "/cur/2.flac", "/cur/3.flac"])

    def tearDown(self) -> None:
        self.dialog.deleteLater()
        self.tmp.cleanup()

    def _select(self, name: str) -> None:
        """按名字选中（列表按「最近保存在前」排序，不能写死行号）。"""
        for row in range(self.dialog.saved_list.count()):
            item = self.dialog.saved_list.item(row)
            if item.data(Qt.UserRole) == name:
                self.dialog.saved_list.setCurrentRow(row)
                return
        self.fail(f"列表里找不到 {name}")

    def test_lists_saved_playlists(self) -> None:
        self.assertEqual(self.dialog.saved_list.count(), 2)
        # 最近保存的排在前面：先存「通勤」再存「深夜」
        self.assertEqual(self.dialog.saved_list.item(0).data(Qt.UserRole), "深夜")
        self.assertEqual(self.dialog.saved_list.item(0).text(), "深夜  ·  1")
        self.assertEqual(self.dialog.saved_list.item(1).data(Qt.UserRole), "通勤")

    def test_selecting_shows_tracks(self) -> None:
        self._select("通勤")
        self.assertEqual(self.dialog.track_list.count(), 2)
        self.assertEqual(self.dialog.track_list.item(0).text(), "1.flac")
        self.assertIn("当前列表 3 首", self.dialog.hint.text())

    def test_load_emits_paths(self) -> None:
        received: list[tuple[list[str], bool]] = []
        self.dialog.loadRequested.connect(lambda paths, app: received.append((paths, app)))
        self._select("通勤")
        self.dialog._load(append=False)
        self.assertEqual(received, [(["/m/1.flac", "/m/2.flac"], False)])

    def test_append_flag(self) -> None:
        received: list[bool] = []
        self.dialog.loadRequested.connect(lambda paths, app: received.append(app))
        self._select("深夜")
        self.dialog._load(append=True)
        self.assertEqual(received, [True])

    def test_remove_and_move_persist(self) -> None:
        self._select("通勤")
        self.dialog.track_list.setCurrentRow(0)
        self.dialog._move(1)
        self.assertEqual(self.store.get("通勤").paths(), ["/m/2.flac", "/m/1.flac"])
        self.dialog.track_list.setCurrentRow(0)
        self.dialog._remove_track()
        self.assertEqual(self.store.get("通勤").paths(), ["/m/1.flac"])

    def test_empty_store_hint(self) -> None:
        empty = PlaylistStore(Path(self.tmp.name) / "empty.json")
        dialog = PlaylistDialog(empty, [])
        try:
            self.assertEqual(dialog.saved_list.count(), 0)
            self.assertFalse(dialog.btn_load.isEnabled())
            self.assertIn("共 0 个列表", dialog.hint.text())
        finally:
            dialog.deleteLater()

    def test_playlist_i18n_key_exists(self) -> None:
        for locale in i18n.locale_names():
            with self.subTest(locale=locale):
                original = i18n.current()
                try:
                    i18n.set_locale(locale)
                    self.assertTrue(i18n.tr("ui_playlist"))
                finally:
                    i18n.set_locale(original)


class TestRestoreLastPlaylist(unittest.TestCase):
    """重启后把上次队列回填到表格（v1.7.1 反馈「每次打开都是空的」）。"""

    @classmethod
    def setUpClass(cls) -> None:
        from seqaudio.core.config import Settings
        from seqaudio.ui.main_window import MainWindow

        cls.window = MainWindow(Settings(offline_mode=True, online_enabled=False))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.hide()
        cls.window.deleteLater()

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="seq_plrestore_")
        self.scanned: list[list[Path]] = []
        self._real_scan = self.window._start_scan
        self.window._start_scan = lambda paths: self.scanned.append(list(paths))
        self.window.model.clear()

    def tearDown(self) -> None:
        self.window._start_scan = self._real_scan
        self.window.model.clear()
        self.tmp.cleanup()

    def test_restored_paths_are_scanned_into_table(self) -> None:
        media = Path(self.tmp.name) / "song.flac"
        media.write_bytes(b"not-a-real-flac")   # 只验证调度，不真的解码
        self.window.player_bridge.restore_session = lambda **_: {
            "restored": True, "count": 1, "index": 0, "position_ms": 0,
            "paths": [str(media), str(Path(self.tmp.name) / "gone.flac")],
        }
        self.window._restore_playback_session()
        QApplication.processEvents()
        self.assertEqual(len(self.scanned), 1, "应把存在的文件交给扫描器回填表格")
        self.assertEqual(self.scanned[0], [media], "不存在的文件必须被过滤")

    def test_no_scan_when_table_not_empty(self) -> None:
        from seqaudio.core.model import Track

        self.window.model.add_tracks([Track(path=Path(self.tmp.name) / "x.flac")])
        self.window.player_bridge.restore_session = lambda **_: {
            "restored": True, "count": 1, "paths": [str(Path(self.tmp.name) / "x.flac")],
        }
        self.window._restore_playback_session()
        QApplication.processEvents()
        self.assertEqual(self.scanned, [], "表格已有内容时不应重复扫描")


if __name__ == "__main__":
    unittest.main(verbosity=2)
