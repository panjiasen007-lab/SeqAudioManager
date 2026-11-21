"""播放列表存储层用例（纯 core，无 Qt）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seqaudio.core.playlists import (  # noqa: E402
    SCHEMA_VERSION,
    PlaylistStore,
    clean_name,
)


class TestPlaylistStore(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="seq_pl_")
        self.path = Path(self.tmp.name) / "playlists.json"
        self.store = PlaylistStore(self.path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_save_and_read_back(self) -> None:
        self.assertTrue(self.store.save("通勤", ["/a/1.flac", "/a/2.flac"]))
        playlist = self.store.get("通勤")
        self.assertIsNotNone(playlist)
        self.assertEqual(playlist.count, 2)
        self.assertEqual(playlist.paths(), ["/a/1.flac", "/a/2.flac"])
        self.assertEqual(self.store.names(), ["通勤"])
        self.assertEqual(self.store.count(), 1)

    def test_overwrite_moves_to_front(self) -> None:
        self.store.save("A", ["/a.flac"])
        self.store.save("B", ["/b.flac"])
        self.store.save("A", ["/a.flac", "/a2.flac"])
        self.assertEqual(self.store.names()[0], "A")
        self.assertEqual(self.store.get("A").count, 2)

    def test_delete_and_rename(self) -> None:
        self.store.save("旧名", ["/a.flac"])
        self.assertTrue(self.store.rename("旧名", "新名"))
        self.assertEqual(self.store.names(), ["新名"])
        self.assertIsNone(self.store.get("旧名"))
        self.assertTrue(self.store.delete("新名"))
        self.assertEqual(self.store.names(), [])
        self.assertFalse(self.store.delete("不存在"))

    def test_rename_to_existing_rejected(self) -> None:
        self.store.save("A", ["/a.flac"])
        self.store.save("B", ["/b.flac"])
        self.assertFalse(self.store.rename("A", "B"))
        self.assertEqual(sorted(self.store.names()), ["A", "B"])

    def test_add_tracks_dedupes(self) -> None:
        self.store.save("A", ["/a.flac"])
        added = self.store.add_tracks("A", ["/a.flac", "/b.flac", "/b.flac"])
        self.assertEqual(added, 1)
        self.assertEqual(self.store.get("A").count, 2)
        self.assertEqual(self.store.add_tracks("没有这个表", ["/c.flac"]), 0)

    def test_move_and_remove(self) -> None:
        self.store.save("A", ["/1.flac", "/2.flac", "/3.flac"])
        self.assertTrue(self.store.move_at("A", 0, 1))
        self.assertEqual(self.store.get("A").paths(), ["/2.flac", "/1.flac", "/3.flac"])
        self.assertFalse(self.store.move_at("A", 0, -1))
        self.assertTrue(self.store.remove_at("A", 1))
        self.assertEqual(self.store.get("A").paths(), ["/2.flac", "/3.flac"])
        self.assertFalse(self.store.remove_at("A", 9))

    def test_invalid_names_rejected(self) -> None:
        for bad in ("", "   "):
            with self.subTest(name=bad):
                self.assertFalse(self.store.save(bad, ["/a.flac"]))
        # 含非法字符的名称是「清洗」而不是「拒绝」：避免用户输入被无声丢弃
        self.assertEqual(clean_name("  a/b:c  "), "abc")
        self.assertTrue(self.store.save("a/b:c", ["/a.flac"]))
        self.assertIsNotNone(self.store.get("abc"))
        self.assertLessEqual(len(clean_name("x" * 200)), 60)

    def test_file_is_valid_json_and_atomic(self) -> None:
        self.store.save("A", ["/a.flac"])
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["version"], SCHEMA_VERSION)
        self.assertEqual(data["items"][0]["name"], "A")
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [], "不应残留临时文件")

    def test_corrupt_file_quarantined(self) -> None:
        self.path.write_text("{ not json", encoding="utf-8")
        self.assertEqual(self.store.all(), [])
        backups = list(self.path.parent.glob("playlists.corrupt-*.json"))
        self.assertEqual(len(backups), 1, "坏文件应改名留档，不能静默删除")
        # 坏文件不影响后续写入
        self.assertTrue(self.store.save("A", ["/a.flac"]))

    def test_future_version_ignored(self) -> None:
        self.path.write_text(
            json.dumps({"version": SCHEMA_VERSION + 1, "items": [{"name": "A"}]}),
            encoding="utf-8",
        )
        self.assertEqual(self.store.all(), [], "未来版本按空表处理，不能用旧程序猜新字段")

    def test_cue_track_kept(self) -> None:
        self.store.save("CUE", [{"path": "/disc.flac", "cue_track": 3}])
        item = self.store.get("CUE").tracks[0]
        self.assertEqual(item["cue_track"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
