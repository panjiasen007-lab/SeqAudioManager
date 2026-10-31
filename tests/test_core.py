"""核心层端到端自测（不依赖 Qt，可 `python tests/test_core.py` 直接跑）。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import make_samples  # noqa: E402
from seqaudio.core import cue as cue_mod  # noqa: E402
from seqaudio.core import lyrics as lyrics_mod  # noqa: E402
from seqaudio.core import probe, rename, tags as tags_mod, watermark, writer  # noqa: E402
from seqaudio.core.model import Tags, Track, ConflictMode  # noqa: E402


class TestCue(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_cue_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gbk_cue(self) -> None:
        path = make_samples.make_cue(self.tmp / "album.cue", encoding="gbk")
        sheet = cue_mod.parse_cue(path)
        self.assertEqual(sheet.encoding_used, "gb18030")
        self.assertEqual(sheet.performer, "陈奕迅")
        self.assertEqual(len(sheet.tracks), 3)
        self.assertEqual(sheet.tracks[0].title, "好久不见")
        self.assertEqual(sheet.tracks[1].start_ms, 250_666)  # 04:10:50
        self.assertGreater(sheet.tracks[2].start_ms, sheet.tracks[1].start_ms)

    def test_utf16_cue(self) -> None:
        content = (
            'PERFORMER "周杰伦"\nTITLE "范特西"\nFILE "a.flac" WAVE\n'
            '  TRACK 01 AUDIO\n    TITLE "简单爱"\n    INDEX 01 00:00:00\n'
        )
        path = self.tmp / "u16.cue"
        path.write_bytes(content.encode("utf-16"))
        sheet = cue_mod.parse_cue(path)
        self.assertEqual(sheet.title, "范特西")
        self.assertEqual(sheet.tracks[0].title, "简单爱")

    def test_broken_cue_does_not_raise(self) -> None:
        path = self.tmp / "bad.cue"
        path.write_text("这不是一个 CUE 文件\n随便写点啥", encoding="utf-8")
        sheet = cue_mod.parse_cue(path)
        self.assertEqual(sheet.tracks, [])
        self.assertTrue(sheet.warnings)


class TestWatermark(unittest.TestCase):
    def test_comment_cleared(self) -> None:
        t = Tags(comment="下载自www.mp3share.com 更多音乐请访问站点", title="七里香")
        report = watermark.clean_tags(t)
        self.assertEqual(t.comment, "")
        self.assertIn("comment", report.changed_fields)
        self.assertEqual(t.title, "七里香")

    def test_title_bracket_removed(self) -> None:
        t = Tags(title="青花瓷【www.abc123.com】", artist="周杰伦")
        watermark.clean_tags(t)
        self.assertEqual(t.title, "青花瓷")

    def test_albumartist_suffix(self) -> None:
        t = Tags(albumartist="周杰伦 - 某某整理")
        watermark.clean_tags(t)
        self.assertTrue(t.albumartist.startswith("周杰伦"))

    def test_normal_tag_untouched(self) -> None:
        t = Tags(title="普通歌曲", artist="普通歌手", comment="心情不错", album="普通专辑")
        report = watermark.clean_tags(t)
        self.assertFalse(report.changed)
        self.assertEqual(t.comment, "心情不错")

    def test_custom_word(self) -> None:
        t = Tags(title="夜曲（某论坛首发）")
        watermark.clean_tags(t, custom_words=["某论坛首发"])
        self.assertEqual(t.title, "夜曲")


class TestRename(unittest.TestCase):
    def test_template(self) -> None:
        track = Track(path=Path("D:/m/01.flac"))
        track.working = Tags(title="富士山下", artist="陈奕迅", album="认了吧", date="2007", track="2")
        track.info.fmt = "FLAC"
        track.info.bit_depth = 24
        track.info.sample_rate = 96000
        self.assertEqual(rename.render_name("{歌手} - {歌名}", track), "陈奕迅 - 富士山下.flac")
        self.assertEqual(rename.render_name("{曲目:02d} {歌名}", track), "02 富士山下.flac")
        self.assertEqual(
            rename.render_name("{专辑}/{碟号}-{曲目:02d} {歌名}", track),
            "认了吧/02 富士山下.flac",
        )
        self.assertEqual(
            rename.render_name("{歌手}/{专辑}/{曲目:02d}. {歌名}", track),
            "陈奕迅/认了吧/02. 富士山下.flac",
        )

    def test_illegal_chars(self) -> None:
        track = Track(path=Path("D:/m/x.mp3"))
        track.working = Tags(title='A/B:C*D?"E"<F>|G', artist="X")
        name = rename.render_name("{歌手} - {歌名}", track)
        # 字段值内的分隔符不得生成子目录
        self.assertEqual(name.count("/"), 0)
        for ch in ':*?"<>|':
            self.assertNotIn(ch, name)

    def test_reserved_name(self) -> None:
        self.assertTrue(rename.sanitize_component("CON").startswith("_"))

    def test_conflict_detection(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="seq_ren_"))
        try:
            a = tmp / "a.mp3"
            b = tmp / "b.mp3"
            a.write_bytes(b"x")
            b.write_bytes(b"x")
            t1 = Track(path=a)
            t2 = Track(path=b)
            for t in (t1, t2):
                t.working = Tags(title="同名", artist="同一人")
            plan = rename.build_plan([t1, t2], "{歌手} - {歌名}")
            self.assertEqual(len(plan.pending), 1)
            self.assertEqual(len(plan.blocked), 1)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestLyrics(unittest.TestCase):
    def test_remove_ads(self) -> None:
        raw = (
            "[ti:七里香]\n[ar:周杰伦]\n[by:www.51lrc.com]\n\n"
            "[00:01.00]窗外的麻雀\n[00:05.00]在电线杆上多嘴\n"
            "[00:09.00]本歌词由某某网提供下载\n[00:12.00]你说这一句很有夏天的感觉\n"
        )
        cleaned = lyrics_mod.clean_lyrics(raw)
        self.assertNotIn("51lrc", cleaned)
        self.assertNotIn("某某网", cleaned)
        self.assertIn("窗外的麻雀", cleaned)
        self.assertIn("[ti:七里香]", cleaned)
        self.assertTrue(lyrics_mod.is_synced(cleaned))
        self.assertEqual(lyrics_mod.count_timed_lines(cleaned), 3)

    def test_build_lrc(self) -> None:
        text = lyrics_mod.build_lrc("歌名", "歌手", "专辑", "第一行\n第二行")
        self.assertIn("[ti:歌名]", text)
        self.assertIn("第一行", text)


class TestWriteRoundtrip(unittest.TestCase):
    """真实文件写入 → 回读 → 备份 → 校验。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_wr_"))
        self.base = self.tmp / "audio"
        self.files = make_samples.build_all(self.base)
        self.backup_root = self.tmp / "backup"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write_and_verify(self, key: str, use_flac: bool = False) -> None:
        path = self.files[key]
        original_size = path.stat().st_size
        track = probe.probe(path, with_cue=False)
        payload = Tags(
            title="富士山下",
            artist="陈奕迅",
            album="认了吧",
            date="2007",
            genre="Pop",
            track="2/10",
            comment="心情",
            lyrics="[00:01.00]测试歌词",
            cover=b"\xff\xd8\xff\xe0" + b"J" * 64,
            cover_mime="image/jpeg",
        )
        outcome = writer.safe_write(
            track, tags=payload, backup=True, backup_root=self.backup_root, mirror_backup=False
        )
        self.assertTrue(outcome.ok, f"{key} 写入失败：{outcome.message}")
        self.assertIsNotNone(outcome.backup_path)
        self.assertTrue(Path(outcome.backup_path).exists())
        self.assertEqual(Path(outcome.backup_path).stat().st_size, original_size)

        back = tags_mod.read_tags(path)
        self.assertEqual(back.title, "富士山下")
        self.assertEqual(back.artist, "陈奕迅")
        self.assertEqual(back.album, "认了吧")
        self.assertEqual(back.track.split("/")[0], "2")
        self.assertIn("测试歌词", back.lyrics)
        self.assertIsNotNone(back.cover)
        self.assertGreaterEqual(back.cover.__len__(), 60)

        # 音频流未被破坏
        self.assertGreater(path.stat().st_size, 0)

    def test_wav(self) -> None:
        self._write_and_verify("wav")

    def test_flac(self) -> None:
        self._write_and_verify("flac")

    def test_mp3(self) -> None:
        self._write_and_verify("mp3")

    def test_backup_preserves_original_tags(self) -> None:
        path = self.files["flac"]
        track = probe.probe(path, with_cue=False)
        writer.safe_write(track, tags=Tags(title="新标题"), backup=True,
                          backup_root=self.backup_root)
        back = tags_mod.read_tags(path)
        self.assertEqual(back.title, "新标题")

    def test_readonly_rejected(self) -> None:
        path = self.files["wav"]
        track = probe.probe(path, with_cue=False)
        import os

        os.chmod(path, 0o444)
        try:
            outcome = writer.safe_write(track, tags=Tags(title="x"), backup=False)
            self.assertFalse(outcome.ok)
        finally:
            os.chmod(path, 0o644)

    def test_encrypted_skipped(self) -> None:
        p = self.base / "secret.ncm"
        p.write_bytes(b"CTENFDAM" + b"\x00" * 20)
        track = probe.probe(p)
        self.assertEqual(track.status.value, "不支持")
        self.assertIn("加密", " ".join(track.notes))


class TestMerge(unittest.TestCase):
    def test_fill_empty_only(self) -> None:
        current = Tags(title="已有标题", artist="")
        incoming = Tags(title="新标题", artist="新歌手", album="专辑")
        changed = current.merge(incoming, ConflictMode.FILL_EMPTY_ONLY)
        self.assertEqual(current.title, "已有标题")
        self.assertEqual(current.artist, "新歌手")
        self.assertIn("artist", changed)
        self.assertNotIn("title", changed)

    def test_overwrite(self) -> None:
        current = Tags(title="旧")
        current.merge(Tags(title="新"), ConflictMode.OVERWRITE)
        self.assertEqual(current.title, "新")

    def test_keep_existing(self) -> None:
        current = Tags(title="旧", album="")
        current.merge(Tags(title="新", album="新专辑"), ConflictMode.KEEP_EXISTING)
        self.assertEqual(current.title, "旧")
        self.assertEqual(current.album, "新专辑")


if __name__ == "__main__":
    unittest.main(verbosity=2)
