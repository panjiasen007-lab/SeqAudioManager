"""核心层端到端自测（不依赖 Qt，可 `python tests/test_core.py` 直接跑）。"""

from __future__ import annotations

import os
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
from seqaudio.core import clean as clean_mod  # noqa: E402
from seqaudio.core import quarantine, uninstall  # noqa: E402
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

    def test_genre_cleaned(self) -> None:
        t = Tags(genre="流行【www.xxx.com】")
        report = watermark.clean_tags(t)
        self.assertEqual(t.genre, "流行")
        self.assertIn("genre", report.changed_fields)

    def test_contact_watermark_in_comment(self) -> None:
        t = Tags(comment="更多无损请加微信：13800138000 或 QQ：12345678")
        watermark.clean_tags(t)
        self.assertEqual(t.comment, "")

    def test_email_and_weibo_in_comment(self) -> None:
        t = Tags(comment="联系 abc@qq.com，微博 @某某音乐")
        watermark.clean_tags(t)
        self.assertEqual(t.comment, "")

    def test_uppercase_domain_recognized(self) -> None:
        """大小写都要识别：WWW.XXX.COM / ABC@QQ.COM 这类全大写同样清理。"""
        t = Tags(comment="WWW.MUSIC-SHARE.COM 下载 ABC@QQ.COM")
        watermark.clean_tags(t)
        self.assertEqual(t.comment, "")
        t2 = Tags(genre="流行【WWW.XXX.COM】")
        watermark.clean_tags(t2)
        self.assertEqual(t2.genre, "流行")

    def test_phone_and_qq_in_structured_fields(self) -> None:
        """所有字段都要清：date/track 这类结构化字段含广告也要去。"""
        t = Tags(track="3 www.xxx.com", date="2007 [无损音质]")
        watermark.clean_tags(t)
        self.assertEqual(t.track, "3")
        self.assertEqual(t.date, "2007")

    def test_tail_space_segment_removed(self) -> None:
        t = Tags(artist="周杰伦 QQ12345678", album="七里香 无损音乐下载")
        watermark.clean_tags(t)
        self.assertEqual(t.artist, "周杰伦")
        self.assertEqual(t.album, "七里香")

    def test_tail_space_normal_kept(self) -> None:
        """空格分隔的正常内容（Hotel California）不应被误删。"""
        t = Tags(title="Hotel California", track="3/12")
        report = watermark.clean_tags(t)
        self.assertEqual(t.title, "Hotel California")
        self.assertEqual(t.track, "3/12")
        self.assertNotIn("title", report.changed_fields)


class TestScript(unittest.TestCase):
    """书写体检测与繁→简转换。"""

    def test_to_simplified(self) -> None:
        from seqaudio.core.script import to_simplified

        self.assertEqual(to_simplified("陳奕迅"), "陈奕迅")
        self.assertEqual(to_simplified("周杰倫"), "周杰伦")
        self.assertEqual(to_simplified("中島美嘉"), "中岛美嘉")

    def test_has_traditional(self) -> None:
        from seqaudio.core.script import has_traditional

        self.assertTrue(has_traditional("陳奕迅"))
        self.assertFalse(has_traditional("陈奕迅"))
        self.assertFalse(has_traditional("Jay Chou"))

    def test_script_of(self) -> None:
        from seqaudio.core.script import script_of

        self.assertEqual(script_of("陈奕迅"), "simplified")
        self.assertEqual(script_of("陳奕迅"), "traditional")
        self.assertEqual(script_of("Jay Chou"), "latin")
        self.assertEqual(script_of("中島美嘉"), "traditional")
        self.assertEqual(script_of("久石讓"), "traditional")

    def test_unify_artist_names(self) -> None:
        """同目录简繁混搭统一为简体，纯繁体保持原样。"""
        from seqaudio.core.config import Settings
        from seqaudio.core.model import Track, TrackStatus
        from seqaudio.pipeline.processor import Processor

        folder = Path(tempfile.mkdtemp(prefix="seq_uni_"))
        self.addCleanup(shutil.rmtree, folder, ignore_errors=True)

        def mk(title: str, artist: str, albumartist: str = "") -> Track:
            t = Track(path=folder / f"{title}.flac")
            t.status = TrackStatus.DONE
            t.working = Tags(title=title, artist=artist, albumartist=albumartist)
            return t

        tracks = [
            mk("七里香", "周杰伦", "周杰倫"),
            mk("借口", "周杰倫", "周杰倫"),
            mk("将军", "周杰伦", "周杰伦"),
            mk("外婆", "陳奕迅"),
        ]
        Processor(Settings(unify_artist_script=True))._unify_artist_names(tracks)
        self.assertEqual(tracks[0].working.artist, "周杰伦")
        self.assertEqual(tracks[0].working.albumartist, "周杰伦")
        self.assertEqual(tracks[1].working.artist, "周杰伦")
        # 纯繁体歌手保持原样
        self.assertEqual(tracks[3].working.artist, "陳奕迅")


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


class TestFilenameGuess(unittest.TestCase):
    """文件名解析：DSD/整轨资源里「歌手 - 02.歌名」是最常见的坑。"""

    CASES = [
        ("周杰伦 - 02.七里香.dff", "周杰伦", "2", "七里香"),
        ("周杰伦 - 01.我的地盘.dff", "周杰伦", "1", "我的地盘"),
        ("01 - 周杰伦 - 七里香.mp3", "周杰伦", "1", "七里香"),
        ("07. 青花瓷.flac", "", "7", "青花瓷"),
        ("05、将军.ape", "", "5", "将军"),
        ("陈奕迅 - 富士山下.flac", "陈奕迅", "", "富士山下"),
        ("Adele - Hello.mp3", "Adele", "", "Hello"),
        ("Taylor Swift - 03.Lover.flac", "Taylor Swift", "3", "Lover"),
        ("李宗盛-山丘.mp3", "李宗盛", "", "山丘"),
        ("Beyond - 海阔天空.wav", "Beyond", "", "海阔天空"),
    ]

    def test_filename_patterns(self) -> None:
        for name, artist, track, title in self.CASES:
            with self.subTest(name=name):
                tags = probe.guess_from_filename(Path(name))
                self.assertEqual(tags.artist, artist, f"{name} 歌手解析错误")
                self.assertEqual(tags.track, track, f"{name} 轨道号解析错误")
                self.assertEqual(tags.title, title, f"{name} 歌名解析错误")

    def test_english_hyphen_not_split(self) -> None:
        tags = probe.guess_from_filename(Path("Jay-Z - 99 Problems.mp3"))
        self.assertEqual(tags.artist, "Jay-Z")
        self.assertEqual(tags.title, "99 Problems")

    def test_album_from_folder(self) -> None:
        cases = [
            ("周杰伦 -七里香 DSD DFF", "七里香"),
            ("Beyond - 樂與怒 SACD [DSD64] - 2015 DSF", "樂與怒"),
            ("Taylor Swift - Lover DSD128", "Lover"),
            ("周杰伦-七里香", "七里香"),
            ("Adele - 25 DSD64", "25"),
        ]
        for folder, expected in cases:
            with self.subTest(folder=folder):
                self.assertEqual(probe.guess_album_from_folder(Path(folder)), expected)

    def test_catalog_id_not_album(self) -> None:
        # 右侧是资源编号时应整串保留，而不是把编号当专辑名
        got = probe.guess_album_from_folder(Path("Nagra 70th Anniversary Collection - 2XHDFT1223"))
        self.assertEqual(got, "Nagra 70th Anniversary Collection")


#: 真实 DSD 母带样本（若本机没有则跳过，绝不在测试里改动它）
REAL_DFF = Path("D:/02.音乐/周杰伦 -七里香 DSD DFF/周杰伦 - 05.将军.dff")


@unittest.skipUnless(REAL_DFF.exists(), "需要本机真实 DFF 样本")
class TestDffWrite(unittest.TestCase):
    """DFF (DSDIFF) 标签写入。

    mutagen 的 DSDIFF 类没有 save()，但 tags.save() 能追加 ID3 chunk ——
    这条路径必须验证：DSD 母带一旦写坏，损失无法挽回。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_dff_"))
        self.path = self.tmp / "将军.dff"
        shutil.copy2(REAL_DFF, self.path)
        self.original_size = self.path.stat().st_size

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_read_metadata(self) -> None:
        track = probe.probe(self.path, with_cue=False)
        self.assertEqual(track.info.fmt, "DFF")
        self.assertTrue(track.info.lossless)
        self.assertEqual(track.info.dsd_rate, "DSD64")
        self.assertEqual(track.info.sample_rate, 2_822_400)
        self.assertEqual(track.info.bit_depth, 1)
        self.assertAlmostEqual(track.info.duration or 0, 202.2, delta=1.0)

    def test_write_and_readback(self) -> None:
        track = probe.probe(self.path, with_cue=False)
        tags = Tags(
            title="将军", artist="周杰伦", album="七里香", albumartist="周杰伦",
            date="2004", genre="流行", track="5", comment="测试备注",
            lyrics="[00:01.00]测试歌词",
        )
        outcome = writer.safe_write(track, tags=tags, backup=False)
        self.assertTrue(outcome.ok, outcome.message)

        back = tags_mod.read_tags(self.path)
        self.assertEqual(back.title, "将军")
        self.assertEqual(back.artist, "周杰伦")
        self.assertEqual(back.album, "七里香")
        self.assertEqual(back.track, "5")
        self.assertEqual(back.comment, "测试备注")
        self.assertIn("测试歌词", back.lyrics)

        # 音频流未动：只应增加一个 ID3 块
        grew = self.path.stat().st_size - self.original_size
        self.assertGreater(grew, 0)
        self.assertLess(grew, 64 * 1024, "标签体积异常，可能重写了音频")

        after = probe.probe(self.path, with_cue=False)
        self.assertAlmostEqual(after.info.duration or 0, 202.2, delta=0.1)
        self.assertEqual(after.info.sample_rate, 2_822_400)

    def test_container_structure_intact(self) -> None:
        """FRM8 声明长度必须等于实际文件大小，否则播放器会截断。"""
        track = probe.probe(self.path, with_cue=False)
        writer.safe_write(track, tags=Tags(title="x", artist="y"), backup=False)

        from seqaudio.core.integrity import check_structure

        result = check_structure(self.path)
        self.assertTrue(result.ok, result.text)
        self.assertIn("ID3", result.text)

    def test_backup_matches_original(self) -> None:
        backup_root = self.tmp / "bak"
        track = probe.probe(self.path, with_cue=False)
        outcome = writer.safe_write(
            track, tags=Tags(title="新标题"), backup=True, backup_root=backup_root
        )
        self.assertTrue(outcome.ok)
        self.assertIsNotNone(outcome.backup_path)
        self.assertTrue(outcome.backup_path.exists())
        # 备份必须是写入前的原件
        self.assertEqual(outcome.backup_path.stat().st_size, self.original_size)


def _ffmpeg() -> str | None:
    try:
        from seqaudio.core.authenticity import find_ffmpeg

        return find_ffmpeg()
    except Exception:  # noqa: BLE001
        return None


@unittest.skipUnless(_ffmpeg(), "需要 ffmpeg 才能解码做频谱分析")
class TestAuthenticity(unittest.TestCase):
    """真假无损鉴定。

    基准信号用 ffmpeg 合成白噪声：白噪声的频谱天然平坦延伸到 Nyquist，
    所以「真无损」的截止频率应当接近 22.05kHz；一旦过一遍 128kbps 编码，
    高频会被砍掉，截止掉到 16kHz 附近。这样测试不依赖任何外部素材。
    """

    @classmethod
    def setUpClass(cls) -> None:
        import subprocess

        cls.tmp = Path(tempfile.mkdtemp(prefix="seq_auth_"))
        ff = _ffmpeg()

        def run(*args) -> None:
            subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", *args],
                check=True,
                timeout=120,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )

        cls.true_wav = cls.tmp / "true.wav"
        run("-f", "lavfi", "-i", "anoisesrc=d=12:c=white:a=0.5",
            "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", "-y", str(cls.true_wav))

        cls.fake_mp3 = cls.tmp / "fake.mp3"
        run("-i", str(cls.true_wav), "-b:a", "128k", "-y", str(cls.fake_mp3))
        cls.fake_wav = cls.tmp / "fake.wav"
        run("-i", str(cls.fake_mp3), "-ar", "44100", "-c:a", "pcm_s16le",
            "-y", str(cls.fake_wav))

        cls.upsampled = cls.tmp / "up.wav"
        run("-i", str(cls.true_wav), "-ar", "96000", "-c:a", "pcm_s24le",
            "-y", str(cls.upsampled))

        # 用不同码率的白噪声做「转码伪装无损」的梯度样本，覆盖判定分档的边界
        cls.encoders: dict[str, Path] = {}
        for bitrate, tag in ((128, "mp3_128"), (192, "mp3_192"),
                             (256, "mp3_256"), (320, "mp3_320")):
            mp3 = cls.tmp / f"{tag}.mp3"
            run("-i", str(cls.true_wav), "-b:a", f"{bitrate}k", "-y", str(mp3))
            wav = cls.tmp / f"{tag}.wav"
            run("-i", str(mp3), "-ar", "44100", "-c:a", "pcm_s16le", "-y", str(wav))
            cls.encoders[tag] = wav

        # VBR：恒定质量编码，比 CBR 更贴近真实转码场景
        vbr_mp3 = cls.tmp / "vbr.mp3"
        run("-i", str(cls.true_wav), "-q:a", "5", "-y", str(vbr_mp3))
        vbr_wav = cls.tmp / "vbr.wav"
        run("-i", str(vbr_mp3), "-ar", "44100", "-c:a", "pcm_s16le", "-y", str(vbr_wav))
        cls.encoders["vbr"] = vbr_wav

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _verdict(self, path: Path):
        from seqaudio.core.authenticity import analyze

        info = probe.probe(path, with_cue=False).info
        return analyze(path, info)

    def test_true_lossless(self) -> None:
        result = self._verdict(self.true_wav)
        self.assertEqual(result.verdict, "真无损", result.reasons)
        self.assertGreater(result.cutoff_hz, 20500)

    def test_mp3_transcode_detected(self) -> None:
        result = self._verdict(self.fake_wav)
        self.assertEqual(result.verdict, "假无损", result.reasons)
        self.assertLess(result.cutoff_hz, 18000)

    def test_upsampled_detected(self) -> None:
        result = self._verdict(self.upsampled)
        self.assertEqual(result.verdict, "疑似升频", result.reasons)
        self.assertLess(result.cutoff_hz, result.nyquist_hz * 0.7)

    def test_bitrate_gradient_cutoff_order(self) -> None:
        """码率越高截止频率越高：128 < 192 < 256 < 320，这是判定的物理依据。"""
        cuts = {
            tag: self._verdict(path).cutoff_hz
            for tag, path in self.encoders.items()
            if tag != "vbr"
        }
        self.assertLess(cuts["mp3_128"], cuts["mp3_192"])
        self.assertLess(cuts["mp3_192"], cuts["mp3_256"])
        self.assertLess(cuts["mp3_256"], cuts["mp3_320"])

    def test_320k_flags_suspect_not_true(self) -> None:
        """320k 与真无损只差 2kHz，绝不能判成「真无损」。"""
        result = self._verdict(self.encoders["mp3_320"])
        self.assertIn(result.verdict, ("疑似假无损", "假无损"), result.reasons)

    def test_low_bitrate_flagged_fake(self) -> None:
        """128 / 192 / VBR 都应判为假无损。"""
        for tag in ("mp3_128", "mp3_192", "vbr"):
            result = self._verdict(self.encoders[tag])
            self.assertIn(result.verdict, ("假无损", "疑似假无损"), (tag, result.reasons))

    def test_effective_bit_depth(self) -> None:
        """16bit 内容存进 24bit 容器时，有效位深应仍识别为 16。"""
        from seqaudio.core.authenticity import _decode_segment, _effective_bit_depth

        samples, _ = _decode_segment(_ffmpeg(), self.true_wav, 3.0, 2.0)
        self.assertEqual(_effective_bit_depth(samples), 16)

    def test_mp3_file_can_be_decoded(self) -> None:
        """MP3 本身也应能解码分析（虽然它不需要"真假无损"结论）。"""
        result = self._verdict(self.fake_mp3)
        self.assertNotEqual(result.verdict, "无法判定", result.error)
        self.assertLess(result.cutoff_hz, 18000)


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


class TestClean(unittest.TestCase):
    """标题去序号：只删开头的曲目序号，不能误伤年份/歌名本身。"""

    def test_common_prefixes(self) -> None:
        cases = {
            "01. 七里香": "七里香",
            "01 七里香": "七里香",
            "01-七里香": "七里香",
            "01、七里香": "七里香",
            "[01] 七里香": "七里香",
            "(02)七里香": "七里香",
            "【03】 七里香": "七里香",
            "09.园游会": "园游会",
        }
        for original, expected in cases.items():
            cleaned, changed = clean_mod.strip_track_number(original)
            self.assertEqual(cleaned, expected, original)
            self.assertTrue(changed)

    def test_do_not_touch_non_numbers(self) -> None:
        for title in ("七里香", "1997 - 往事", "U2. Beautiful Day", "1+1=2",
                      "The 1975 - Love It If We Made It", "3 Doors Down - Here Without You"):
            cleaned, changed = clean_mod.strip_track_number(title)
            self.assertFalse(changed, title)
            self.assertEqual(cleaned, title)

    def test_empty_after_strip_keeps_original(self) -> None:
        """纯序号（如「01」）删完就空了，必须保留原值。"""
        cleaned, changed = clean_mod.strip_track_number("01")
        self.assertFalse(changed)
        self.assertEqual(cleaned, "01")

    def test_three_digit_requires_opt_in(self) -> None:
        self.assertFalse(clean_mod.strip_track_number("123 早安")[1])
        self.assertTrue(clean_mod.strip_track_number("123 早安", allow_three_digit=True)[1])


class TestClearComment(unittest.TestCase):
    """默认清空注释字段：抓轨/下载工具塞的广告与推广整段去掉，可关闭。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_cc_"))
        self.files = make_samples.build_all(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _prepare(self, comment: str, clear: bool):
        from seqaudio.core.config import Settings
        from seqaudio.pipeline.processor import BatchOptions, Processor

        track = probe.probe(self.files["mp3"], with_cue=False)
        track.original = Tags(title="测试", artist="某人", comment=comment)
        track.working = track.original.copy()
        processor = Processor(Settings(offline_mode=True, clear_comment=clear, min_duration_sec=0))
        processor.prepare_one(
            track,
            BatchOptions(match_online=False, clean_watermark=True, write_tags=False, fetch_lyrics=False),
            None,
        )
        return track

    def test_clear_normal_comment(self) -> None:
        track = self._prepare("这是我的私人备注", clear=True)
        self.assertEqual(track.working.comment, "")
        self.assertIn("comment", track.changed_fields)

    def test_clear_watermarked_comment(self) -> None:
        track = self._prepare("广告 www.xxx.com 加微信13800138000", clear=True)
        self.assertEqual(track.working.comment, "")

    def test_keep_when_disabled(self) -> None:
        track = self._prepare("保留的备注", clear=False)
        self.assertEqual(track.working.comment, "保留的备注")

    def test_empty_comment_untouched(self) -> None:
        track = self._prepare("", clear=True)
        self.assertEqual(track.working.comment, "")
        self.assertNotIn("comment", track.changed_fields)


class TestWriteOnly(unittest.TestCase):
    """只写模式（「全部确认保存」）：跳过匹配/清洗，直接把 working 变更落盘。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_wo_"))
        self.files = make_samples.build_all(self.tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_only_persists_changes(self) -> None:
        from seqaudio.core.config import Settings
        from seqaudio.core.tags import read_tags
        from seqaudio.pipeline.processor import BatchOptions, Processor

        path = self.files["mp3"]
        track = probe.probe(path, with_cue=False)
        track.working.title = "只写测试歌名"
        track.working.artist = "只写测试歌手"
        track.working.comment = ""

        summary = Processor(Settings(backup_enabled=False, offline_mode=True)).run(
            [track], BatchOptions(write_only=True, write_tags=True)
        )
        self.assertEqual(summary.done, 1, summary.text())

        back = read_tags(path)
        self.assertEqual(back.title, "只写测试歌名")
        self.assertEqual(back.artist, "只写测试歌手")

    def test_write_only_skips_cleaning(self) -> None:
        """只写模式不应触发去序号/水印清理等清洗步骤。"""
        from seqaudio.core.config import Settings
        from seqaudio.pipeline.processor import BatchOptions, Processor

        track = probe.probe(self.files["flac"], with_cue=False)
        track.working.title = "01. 七里香"
        track.working.artist = "周杰伦"

        Processor(Settings(backup_enabled=False, offline_mode=True,
                           strip_track_numbers=True, watermark_enabled=True)).run(
            [track], BatchOptions(write_only=True, write_tags=True)
        )
        # 只写模式不做去序号，标题应保持「01. 七里香」
        self.assertEqual(track.working.title, "01. 七里香")


class TestQuarantine(unittest.TestCase):
    """隔离区：移动 / 还原 / 彻底删除。

    这套逻辑直接删用户文件，测试必须覆盖「原位置已被占用」这种真实场景 ——
    还原时若直接覆盖，用户刚下载的同名文件就没了。
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_quar_"))
        self.music = self.tmp / "music"
        self.music.mkdir()
        self.store = quarantine.QuarantineStore(self.tmp / "quarantine")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make(self, name: str, size: int = 1024) -> Path:
        path = self.music / name
        path.write_bytes(b"\0" * size)
        return path

    def test_move_and_manifest(self) -> None:
        path = self._make("fake.flac")
        created, failed = self.store.move_in([(path, "假无损", "截止 16.0kHz")])
        self.assertEqual(len(created), 1)
        self.assertFalse(failed)
        self.assertFalse(path.exists(), "原文件应已被移走")
        self.assertTrue(Path(created[0].stored).exists())

        entries = self.store.entries()
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].verdict, "假无损")
        self.assertEqual(entries[0].original, str(path))
        self.assertGreater(self.store.total_size(), 0)

    def test_sidecar_moves_with_audio(self) -> None:
        path = self._make("song.flac")
        lrc = self.music / "song.lrc"
        lrc.write_text("[00:01.00]歌词", encoding="utf-8")
        self.store.move_in([(path, "假无损", "")])
        self.assertFalse(lrc.exists(), "同名 .lrc 应一起移走")
        self.assertIn("song.lrc", self.store.entries()[0].companions)

    def test_restore_to_original(self) -> None:
        path = self._make("song.flac")
        self.store.move_in([(path, "假无损", "")])
        entry = self.store.entries()[0]
        ok, _ = self.store.restore(entry)
        self.assertTrue(ok)
        self.assertTrue(path.exists())
        self.assertEqual(self.store.entries(), [])

    def test_restore_when_original_taken(self) -> None:
        path = self._make("song.flac")
        self.store.move_in([(path, "假无损", "")])
        path.write_bytes(b"new")          # 用户又放了一个同名文件
        entry = self.store.entries()[0]
        ok, message = self.store.restore(entry)
        self.assertTrue(ok, message)
        self.assertEqual(path.read_bytes(), b"new", "原有文件不能被覆盖")
        restored = self.music / "song (已恢复).flac"
        self.assertTrue(restored.exists())

    def test_purge_removes_file(self) -> None:
        path = self._make("song.flac")
        self.store.move_in([(path, "假无损", "")])
        entry = self.store.entries()[0]
        stored = Path(entry.stored)
        ok, _ = self.store.purge(entry)
        self.assertTrue(ok)
        self.assertFalse(stored.exists())
        self.assertEqual(self.store.entries(), [])

    def test_batch_grouping(self) -> None:
        self.store.move_in([(self._make("a.flac"), "假无损", "")], batch="B1")
        self.store.move_in([(self._make("b.flac"), "疑似升频", "")], batch="B1")
        batches = self.store.batches()
        self.assertIn("B1", batches)
        self.assertEqual(len(batches["B1"]), 2)

    def test_human_size(self) -> None:
        self.assertEqual(quarantine.human_size(0), "0 B")
        self.assertEqual(quarantine.human_size(1536), "1.5 KB")
        self.assertEqual(quarantine.human_size(1024 ** 3 * 2), "2.0 GB")


class TestUninstallScript(unittest.TestCase):
    """卸载脚本是唯一会删整个安装目录的地方，至少要保证路径写对了。"""

    def test_script_contains_paths(self) -> None:
        install = Path(r"C:\Users\demo\AppData\Local\Programs\SeqAudioManager")
        script = uninstall.build_uninstall_script(install, keep_data=True)
        self.assertIn(str(install), script)
        self.assertIn("SeqAudioManager", script)
        self.assertIn("Remove-Item", script)
        # 保留数据时，必须先把 data 复制出去再删目录，顺序反了备份就没了
        self.assertLess(script.index("Copy-Item"), script.index("Remove-Item"))

    def test_script_without_keep(self) -> None:
        install = Path(r"C:\Temp\SeqAudio")
        script = uninstall.build_uninstall_script(install, keep_data=False)
        self.assertNotIn("Copy-Item", script)
        self.assertIn("Uninstall\\SeqAudioManager", script)

    def test_script_removes_uninstall_shortcut(self) -> None:
        install = Path(r"C:\Temp\SeqAudio")
        script = uninstall.build_uninstall_script(install, keep_data=False)
        # 安装器会在开始菜单创建「卸载」快捷方式，卸载时必须一并删除
        self.assertIn("卸载 音序管家.lnk", script)


class TestFolderCover(unittest.TestCase):
    """每文件夹至少一张 cover：本地识别/写入 + 离线编排（不依赖网络）。"""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_cover_"))
        self.cover = __import__("seqaudio.core.cover", fromlist=["cover"])

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_folder_has_cover_detects_common_names(self) -> None:
        folder = self.tmp / "a"
        folder.mkdir()
        self.assertFalse(self.cover.folder_has_cover(folder))
        (folder / "cover.jpg").write_bytes(b"x")
        self.assertTrue(self.cover.folder_has_cover(folder))
        (folder / "cover.jpg").unlink()
        (folder / "folder.png").write_bytes(b"x")
        self.assertTrue(self.cover.folder_has_cover(folder))

    def test_write_folder_cover_uses_mime_ext(self) -> None:
        folder = self.tmp / "b"
        folder.mkdir()
        self.cover.write_folder_cover(folder, b"\x89PNG\r\n\x1a\n" + b"0" * 100, "image/png")
        self.assertTrue((folder / "cover.png").exists())
        self.cover.write_folder_cover(folder, b"\xff\xd8\xff" + b"0" * 100, "image/jpeg")
        self.assertTrue((folder / "cover.jpg").exists())

    def test_first_cover_prefers_working(self) -> None:
        track = Track(path=self.tmp / "x.flac")
        track.original.cover = b"\x89PNG\r\n\x1a\n" + b"A" * 100
        track.original.cover_mime = "image/png"
        data, mime = self.cover.first_cover([track])
        self.assertIsNotNone(data)
        self.assertEqual(mime, "image/png")

    def test_ensure_folder_covers_offline(self) -> None:
        """离线：内存封面直接写、已有封面跳过、无封面则失败，不崩。"""
        from seqaudio.core.model import TrackStatus
        from seqaudio.net.http import HttpClient
        from seqaudio.pipeline.covers import ensure_folder_covers

        fa = self.tmp / "有封面"
        fa.mkdir()
        fb = self.tmp / "已有"
        fb.mkdir()
        (fb / "cover.jpg").write_bytes(b"EXIST")

        t1 = Track(path=fa / "a.flac")
        t1.status = TrackStatus.DONE
        t1.working.cover = b"\x89PNG\r\n\x1a\n" + b"B" * 200
        t1.working.cover_mime = "image/png"
        t2 = Track(path=fb / "b.flac")
        t2.status = TrackStatus.DONE

        client = HttpClient(offline=True)
        summary = ensure_folder_covers([t1, t2], client)
        self.assertTrue((fa / "cover.png").exists())
        self.assertEqual((fb / "cover.jpg").read_bytes(), b"EXIST")  # 未被覆盖
        self.assertEqual(summary.created, 1)
        self.assertEqual(summary.existing, 1)


class TestMatcherDurationScoring(unittest.TestCase):
    """本次修复：时长维度真正参与评分，不再恒为中性分。"""

    def test_duration_score_uses_actual(self) -> None:
        from seqaudio.pipeline.matcher import duration_score

        self.assertGreater(duration_score(200.0, 201.0), duration_score(200.0, 230.0))
        self.assertEqual(duration_score(200.0, 0.0), 0.5)  # 未知时长仍是中性分
        self.assertEqual(duration_score(0.0, 200.0), 0.5)

    def test_score_prefers_duration_match(self) -> None:
        from seqaudio.core.model import MatchCandidate
        from seqaudio.net.http import HttpClient
        from seqaudio.pipeline.matcher import Matcher

        track = Track(path=Path("x.flac"))
        track.info.duration = 200.0
        track.working = Tags(title="测试歌", artist="张三")

        matcher = Matcher(HttpClient(offline=True))
        good = MatchCandidate(source="x", title="测试歌", artist="张三", duration=200.0, raw_score=1.0)
        bad = MatchCandidate(source="x", title="测试歌", artist="张三", duration=260.0, raw_score=1.0)

        self.assertGreater(matcher.score(track, good), matcher.score(track, bad))


class TestMusicBrainzFallback(unittest.TestCase):
    """本次修复：中文歌手名与繁体库不匹配时，去掉 artist 限定自动重查。"""

    def test_chinese_artist_fallback(self) -> None:
        from seqaudio.net import musicbrainz

        calls: list[str] = []

        class FakeClient:
            def get_json(self, url, **kw):
                calls.append(kw.get("params", {}).get("query", ""))
                if len(calls) == 1:
                    return {"recordings": []}  # 带 artist 的精确查询 → 空
                return {"recordings": [self._recording()]}

            @staticmethod
            def _recording():
                return {
                    "id": "rec-1", "title": "七里香", "score": 100,
                    "artist-credit": [{"name": "周杰倫", "joinphrase": ""}],
                    "releases": [{
                        "id": "rel-1", "title": "七里香", "date": "2004-08-03",
                        "media": [{"position": 1, "track": [
                            {"id": "rec-1", "number": "2", "title": "七里香"},
                        ]}],
                        "release-group": {"primary-type": "Album", "secondary-types": []},
                    }],
                }

        cands = musicbrainz.search_recordings(FakeClient(), "七里香", "周杰伦", "")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].title, "七里香")
        self.assertEqual(len(calls), 2)           # 第一次空，触发降级重查
        self.assertIn("artist:", calls[0])
        self.assertNotIn("artist:", calls[1])

    def test_english_artist_no_fallback(self) -> None:
        from seqaudio.net import musicbrainz

        calls: list[str] = []

        class FakeClient:
            def get_json(self, url, **kw):
                calls.append(kw.get("params", {}).get("query", ""))
                return {"recordings": []}

        cands = musicbrainz.search_recordings(FakeClient(), "Hello", "Adele", "")
        self.assertEqual(cands, [])
        self.assertEqual(len(calls), 1)           # 英文歌手名不做降级重查


class TestLyricsDurationCheck(unittest.TestCase):
    """本次修复：LRCLIB 精确匹配命中但时长偏差过大时回退搜索比较。"""

    def test_duration_delta(self) -> None:
        from seqaudio.net import lrclib

        self.assertEqual(lrclib._duration_delta(lrclib.LyricsResult(duration=210.0), 200.0), 10.0)
        self.assertEqual(lrclib._duration_delta(lrclib.LyricsResult(duration=0.0), 200.0), 99.0)
        self.assertEqual(lrclib._duration_delta(lrclib.LyricsResult(duration=200.0), None), 99.0)

    def test_fetch_prefers_closer_duration(self) -> None:
        from seqaudio.net import lrclib

        class FakeClient:
            def get_json(self, url, **kw):
                if url.endswith("/get"):
                    return {"trackName": "七里香", "artistName": "周杰伦",
                            "syncedLyrics": "[00:01]远", "duration": 230.0}
                return [{"trackName": "七里香", "artistName": "周杰伦",
                         "syncedLyrics": "[00:01]近", "duration": 201.0}]

        result = lrclib.fetch_lyrics(FakeClient(), "七里香", "周杰伦", "", 200.0)
        self.assertIsNotNone(result)
        self.assertAlmostEqual(result.duration, 201.0, places=1)  # 选时长更接近的搜索结果


class TestHttpReferer(unittest.TestCase):
    """本次修复：网易云图片 CDN 自动补 Referer 防盗链头。"""

    def test_referer_added_for_netmusic(self) -> None:
        from unittest import mock
        from seqaudio.net.http import HttpClient

        client = HttpClient()
        captured: dict = {}

        def fake_request(url, **kw):
            captured["headers"] = kw.get("headers")
            return b"\xff\xd8\xff" + b"x" * 200, "image/jpeg"

        with mock.patch.object(client, "_request", side_effect=fake_request):
            data = client.get_bytes("https://p1.music.126.net/cover.jpg")

        self.assertIsNotNone(data)
        self.assertEqual(captured["headers"]["Referer"], "https://music.163.com/")

    def test_no_referer_for_other_host(self) -> None:
        from unittest import mock
        from seqaudio.net.http import HttpClient

        client = HttpClient()
        captured: dict = {}

        def fake_request(url, **kw):
            captured["headers"] = kw.get("headers")
            return b"\xff\xd8\xff" + b"x" * 200, "image/jpeg"

        with mock.patch.object(client, "_request", side_effect=fake_request):
            client.get_bytes("https://is1-ssl.mzstatic.com/image/x.jpg")

        self.assertIsNone(captured["headers"])  # iTunes 图片不加 Referer


class TestLocalLyricsSkipNetwork(unittest.TestCase):
    """本次修复：本地已有歌词时不再触发联网抓取。"""

    def test_local_lyrics_skips_network(self) -> None:
        from seqaudio.core.config import Settings
        from seqaudio.pipeline.matcher import MatchResult
        from seqaudio.pipeline.processor import BatchOptions, Processor

        tmp = Path(tempfile.mkdtemp(prefix="seq_lyric_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "a.flac").write_bytes(b"")

        class FakeMatcher:
            def __init__(self):
                self.need_lyrics_called = None

            def match(self, track, need_lyrics=True, album_hint=""):
                self.need_lyrics_called = need_lyrics
                return MatchResult()

        track = Track(path=tmp / "a.flac")
        track.info.duration = 200.0
        track.original = Tags(title="测试", artist="张三", lyrics="[00:01]本地歌词")

        settings = Settings()
        settings.use_local_lyrics = True
        fake = FakeMatcher()
        opts = BatchOptions(match_online=True, fetch_lyrics=True, write_tags=False)
        Processor(settings).prepare_one(track, opts, matcher=fake)

        self.assertIs(fake.need_lyrics_called, False)

    def test_no_local_lyrics_keeps_network(self) -> None:
        from seqaudio.core.config import Settings
        from seqaudio.pipeline.matcher import MatchResult
        from seqaudio.pipeline.processor import BatchOptions, Processor

        tmp = Path(tempfile.mkdtemp(prefix="seq_lyric2_"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "b.flac").write_bytes(b"")

        class FakeMatcher:
            def __init__(self):
                self.need_lyrics_called = None

            def match(self, track, need_lyrics=True, album_hint=""):
                self.need_lyrics_called = need_lyrics
                return MatchResult()

        track = Track(path=tmp / "b.flac")
        track.info.duration = 200.0
        track.original = Tags(title="测试", artist="张三")

        settings = Settings()
        settings.use_local_lyrics = True
        fake = FakeMatcher()
        opts = BatchOptions(match_online=True, fetch_lyrics=True, write_tags=False)
        Processor(settings).prepare_one(track, opts, matcher=fake)

        self.assertIs(fake.need_lyrics_called, True)


class TestQQMusicSource(unittest.TestCase):
    """QQ 音乐源解析（V1.1）：不联网，只验证返回体解析正确。"""

    def test_search_parses_candidates(self) -> None:
        from seqaudio.net import qqmusic

        class FakeClient:
            def get_json(self, url, **kw):
                return {
                    "code": 0,
                    "data": {"song": {"list": [{
                        "mid": "0039MnYb0qxYhV",
                        "title": "七里香",
                        "singer": [{"name": "周杰伦"}],
                        "album": {"mid": "002fRO0N4FftzY", "name": "七里香"},
                        "interval": 299,
                    }]}},
                }

        candidates = qqmusic.search(FakeClient(), "七里香", "周杰伦")
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertEqual(cand.source, "QQ音乐")
        self.assertEqual(cand.title, "七里香")
        self.assertEqual(cand.artist, "周杰伦")
        self.assertEqual(cand.album, "七里香")
        self.assertAlmostEqual(cand.duration, 299.0)
        self.assertIn("002fRO0N4FftzY", cand.cover_url)
        self.assertEqual(cand.recording_id, "0039MnYb0qxYhV")

    def test_lyrics_returns_synced(self) -> None:
        from seqaudio.net import qqmusic

        class FakeClient:
            def get_json(self, url, **kw):
                return {"retcode": 0, "lyric": "[00:01.00]窗外的麻雀"}

        result = qqmusic.lyrics(FakeClient(), "七里香", "周杰伦", song_mid="0039")
        self.assertIsNotNone(result)
        self.assertEqual(result.source, "QQ音乐")
        self.assertTrue(result.is_synced)
        self.assertIn("窗外的麻雀", result.synced)

    def test_empty_response_is_safe(self) -> None:
        from seqaudio.net import qqmusic

        class FakeClient:
            def get_json(self, url, **kw):
                return {}

        self.assertEqual(qqmusic.search(FakeClient(), "不存在", ""), [])
        self.assertIsNone(qqmusic.lyrics(FakeClient(), "不存在", "", song_mid="x"))


class TestDsdJudgement(unittest.TestCase):
    """DSD 判定（V1.2）：噪声整形斜率是主判据，参考 check-dsd。"""

    def test_genuine_dsd(self) -> None:
        from seqaudio.core.authenticity import _judge_dsd

        verdict, score, _ = _judge_dsd(12.0, -20.0, 30000.0, 5.0, -45.0)
        self.assertEqual(verdict, "真 DSD")
        self.assertGreaterEqual(score, 0.8)

    def test_flat_ultrasonic_is_fake(self) -> None:
        from seqaudio.core.authenticity import _judge_dsd

        verdict, _, _ = _judge_dsd(0.0, -90.0, 22050.0, 60.0, -90.0)
        self.assertEqual(verdict, "疑似假 DSD")

    def test_cd_sourced_dsd_is_fake(self) -> None:
        """噪声整形成立，但基带在 CD 的 22.05kHz 边界截止且之上是数字静音。

        真实数据（周杰伦 DFF）：slope +10.8dB/oct、base_cutoff 22179Hz、
        基带陡度 12.7dB/kHz、22.5~24kHz -85.6dB。
        """
        from seqaudio.core.authenticity import _judge_dsd

        verdict, _, reasons = _judge_dsd(10.8, -7.0, 22179.0, 12.7, -85.6)
        self.assertEqual(verdict, "疑似假 DSD")
        self.assertTrue(any("22.05kHz" in r for r in reasons))

    def test_weak_shaping_is_suspect(self) -> None:
        from seqaudio.core.authenticity import _judge_dsd

        verdict, _, _ = _judge_dsd(2.0, -45.0, 0.0, 0.0, 0.0)
        self.assertEqual(verdict, "疑似假 DSD")

    def test_noise_shaping_slope(self) -> None:
        """上升频谱应得到正斜率，平坦频谱应接近 0。"""
        import numpy as np

        from seqaudio.core.authenticity import _noise_shaping_slope

        freqs = np.linspace(0, 176400, 20001)
        rise = 12.0 * np.log2(np.maximum(freqs, 1.0) / 50000.0) - 80.0
        self.assertGreater(_noise_shaping_slope(freqs, rise, 176400.0), 6.0)

        flat = np.full_like(freqs, -80.0)
        self.assertAlmostEqual(_noise_shaping_slope(freqs, flat, 176400.0), 0.0, places=1)


class TestInstallerMode(unittest.TestCase):
    """安装器版本检测：全新 / 修复 / 升级（V1.1.1）。"""

    def setUp(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "installer"))
        try:
            import installer  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"无法导入安装器：{exc}")
        self.installer = installer

    def test_modes(self) -> None:
        i = self.installed = self.installer
        self.assertEqual(i.install_mode(""), "fresh")
        self.assertEqual(i.install_mode(i.VERSION), "repair")
        self.assertEqual(i.install_mode("0.9.0"), "upgrade")

    def test_parse_version(self) -> None:
        i = self.installer
        self.assertEqual(i._parse_version("1.0.0"), (1, 0, 0))
        self.assertEqual(i._parse_version(""), (0,))
        self.assertEqual(i._parse_version("1.1"), (1, 1))


class TestInstallerExtract(unittest.TestCase):
    """升级安装必须真的覆盖程序文件（V1.2.2 修复）。

    旧实现靠「target 下只有一个子目录」判断要不要把压缩包的顶层目录拍平，
    升级安装时 target 里已有旧文件，条件永远不成立：新版本被留在
    ``音序管家-vX.Y.Z/`` 子目录里，根目录的 exe 还是旧版。
    """

    ARC_ROOT = "音序管家-v9.9.9"

    def setUp(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "installer"))
        try:
            import installer
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"无法导入安装器：{exc}")
        try:
            import py7zr  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"缺少 py7zr：{exc}")
        self.installer = installer

    def _make_payload(self, path: Path, exe_bytes: bytes, dll_bytes: bytes) -> None:
        """造一个和真实发行包同构的 7z：带一层顶层目录。"""
        import py7zr

        src = path.parent / "_stage"
        inner = src / self.ARC_ROOT
        inner.mkdir(parents=True, exist_ok=True)
        (inner / self.installer.EXE_NAME).write_bytes(exe_bytes)
        (inner / "_internal").mkdir(exist_ok=True)
        (inner / "_internal" / "new.dll").write_bytes(dll_bytes)
        (inner / "使用说明.txt").write_text("新版说明", encoding="utf-8")
        with py7zr.SevenZipFile(path, "w") as archive:
            archive.writeall(inner, arcname=self.ARC_ROOT)

    def test_upgrade_overwrites_old_files(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload.7z"
            self._make_payload(payload, b"NEW-EXE", b"NEW-DLL")

            target = root / "installed"
            (target / "_internal").mkdir(parents=True)
            (target / "data").mkdir(parents=True)
            (target / self.installer.EXE_NAME).write_bytes(b"OLD-EXE")
            (target / "_internal" / "old.dll").write_bytes(b"OLD-DLL")
            (target / "data" / "config.json").write_text('{"keep": true}', encoding="utf-8")
            # 旧 bug 留下的整包副本
            stale = target / "音序管家-v1.2.1-x86-x64"
            stale.mkdir()
            (stale / "音序管家.exe").write_bytes(b"STALE")

            self.installer.extract_payload(payload, target)

            exe = target / self.installer.EXE_NAME
            self.assertEqual(exe.read_bytes(), b"NEW-EXE")           # 程序被真的换成新版
            self.assertTrue((target / "_internal" / "new.dll").exists())
            self.assertFalse((target / "_internal" / "old.dll").exists())  # 旧模块不残留
            self.assertEqual((target / "data" / "config.json").read_text(encoding="utf-8"), '{"keep": true}')
            self.assertFalse(stale.exists())                          # 历史残留被清理
            self.assertFalse((target / self.ARC_ROOT).exists())        # 不再套一层子目录

    def test_fresh_install_flattens_root_dir(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = root / "payload.7z"
            self._make_payload(payload, b"NEW-EXE", b"NEW-DLL")
            target = root / "fresh"

            self.installer.extract_payload(payload, target)

            self.assertEqual((target / self.installer.EXE_NAME).read_bytes(), b"NEW-EXE")
            self.assertTrue((target / "_internal" / "new.dll").exists())
            self.assertFalse((target / self.ARC_ROOT).exists())


class TestInstallerRunningGuard(unittest.TestCase):
    """安装前拦住「程序正在运行」，关掉后可以重试（V1.2.3）。"""

    def setUp(self) -> None:
        import sys

        sys.path.insert(0, str(ROOT / "installer"))
        try:
            import installer
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"无法导入安装器：{exc}")
        self.installer = installer

    def test_running_instances_matches_full_path(self) -> None:
        """按完整路径比对：同名但不同目录的可执行文件不算占用。"""
        self.assertEqual(
            self.installer.running_instances(Path(r"C:\other\notepad.exe")), []
        )

    @unittest.skipUnless(
        os.environ.get("SEQAUDIO_E2E") == "1",
        "设置 SEQAUDIO_E2E=1 才启动真实进程（较慢）",
    )
    def test_running_instances_finds_running_app(self) -> None:
        """真实启动便携版，应能被按路径找到，关掉后消失。"""
        import subprocess
        import time

        exe = ROOT / "build" / "portable" / "dist" / "音序管家" / "音序管家.exe"
        if not exe.exists():
            self.skipTest("没有便携版可执行文件")
        self.assertEqual(self.installer.running_instances(exe), [])

        proc = subprocess.Popen([str(exe)], cwd=str(exe.parent))
        try:
            deadline = time.time() + 60
            pids: list[int] = []
            while time.time() < deadline:
                pids = self.installer.running_instances(exe)
                if pids:
                    break
                time.sleep(0.5)
            self.assertTrue(pids, "应检测到正在运行的便携版")
            self.assertEqual(self.installer.blocking_pids(exe.parent), pids)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

        deadline = time.time() + 20
        while time.time() < deadline and self.installer.running_instances(exe):
            time.sleep(0.3)
        self.assertEqual(self.installer.running_instances(exe), [])

    def test_wait_until_closed_retries_until_gone(self) -> None:
        """占用 → 用户点重试 → 关掉 → 继续。"""
        sequence = [[101], [202], []]
        asked: list[list[int]] = []
        original = self.installer.running_instances
        self.installer.running_instances = lambda exe: sequence.pop(0) if sequence else []
        try:
            def ask(pids, action):
                asked.append(list(pids))
                return True

            ok = self.installer.wait_until_closed(Path(r"C:\nonexistent"), "升级", ask=ask)
        finally:
            self.installer.running_instances = original
        self.assertTrue(ok)
        self.assertEqual(asked, [[101], [202]])

    def test_wait_until_closed_cancel(self) -> None:
        original = self.installer.running_instances
        self.installer.running_instances = lambda exe: [7]
        try:
            ok = self.installer.wait_until_closed(
                Path(r"C:\nonexistent"), "安装", ask=lambda pids, action: False
            )
        finally:
            self.installer.running_instances = original
        self.assertFalse(ok)

    def test_wait_until_closed_no_need_to_ask(self) -> None:
        original = self.installer.running_instances
        self.installer.running_instances = lambda exe: []
        try:
            ok = self.installer.wait_until_closed(Path(r"C:\nonexistent"), "安装")
        finally:
            self.installer.running_instances = original
        self.assertTrue(ok)


class TestAuthenticityV13(unittest.TestCase):
    """升采样 / 升位深 / 数字静音三类判据（V1.2.4）。

    这三条对应 LAC 2015 与 Derrien 2019 的做法：把「截止频率」从判决依据
    降级为证据，改看三类各自独立的物理事实。
    """

    def setUp(self) -> None:
        from seqaudio.core.model import AudioInfo

        self.info16 = AudioInfo(
            fmt="FLAC", lossless=True, sample_rate=44100, bit_depth=16,
            channels=2, duration=30.0,
        )
        self.info24 = AudioInfo(
            fmt="FLAC", lossless=True, sample_rate=48000, bit_depth=24,
            channels=2, duration=30.0,
        )

    def test_digital_silence_marks_transcode(self) -> None:
        """截止之上是数字静音（-101dB）→ 高频被编码器砍掉，不能判真无损。"""
        from seqaudio.core.authenticity import _judge

        verdict, _, reasons = _judge(
            self.info16, 20286.0, 16.0, -75.0, 16, edge_db=-101.3, ratio=0.92
        )
        self.assertIn(verdict, ("假无损", "疑似假无损"), reasons)

    def test_analog_noise_is_not_condemned(self) -> None:
        """老录音截止之上有模拟底噪（-75dB），不能因截止偏低就判假。"""
        from seqaudio.core.authenticity import _judge

        verdict, _, reasons = _judge(
            self.info16, 18500.0, 15.0, -70.0, 16, edge_db=-75.0, ratio=0.84
        )
        self.assertEqual(verdict, "真无损", reasons)

    def test_full_bandwidth_is_true(self) -> None:
        """带宽接近满值 → 真无损，不受数字静音判据影响。"""
        from seqaudio.core.authenticity import _judge

        verdict, _, reasons = _judge(
            self.info16, 21850.0, 30.0, -70.0, 16, edge_db=-120.0, ratio=0.99
        )
        self.assertEqual(verdict, "真无损", reasons)

    def test_24bit_padding_detected(self) -> None:
        """声明 24bit 但样本从未越过 16bit 上限 → 位深注水。"""
        from seqaudio.core.authenticity import _judge

        verdict, _, reasons = _judge(
            self.info24, 23000.0, 30.0, -70.0, 24,
            edge_db=-90.0, ratio=0.96, above16=False, peak_dbfs=-4.9,
        )
        self.assertEqual(verdict, "疑似升位深", reasons)

    def test_real_24bit_not_flagged(self) -> None:
        """真 24bit：样本越过 16bit 上限，不能判位深注水。"""
        from seqaudio.core.authenticity import _judge

        verdict, _, reasons = _judge(
            self.info24, 23000.0, 30.0, -70.0, 24,
            edge_db=-80.0, ratio=0.96, above16=True, peak_dbfs=-1.0,
        )
        self.assertNotEqual(verdict, "疑似升位深", reasons)

    def test_quiet_24bit_not_flagged(self) -> None:
        """电平极低的 24bit 文件本来就用不满动态范围，不能据此判注水。"""
        from seqaudio.core.authenticity import _judge

        verdict, _, reasons = _judge(
            self.info24, 23000.0, 30.0, -70.0, 24,
            edge_db=-80.0, ratio=0.96, above16=False, peak_dbfs=-52.0,
        )
        self.assertNotEqual(verdict, "疑似升位深", reasons)

    def test_upsample_by_empty_edge(self) -> None:
        """48kHz 声明但内容止于 22kHz、扩展带全空 → 44.1k 升采样。"""
        from seqaudio.core.authenticity import _judge

        info = self.info24
        verdict, _, reasons = _judge(
            info, 21984.0, 30.0, -70.0, 24,
            edge_db=-139.7, ratio=0.92, above16=True, peak_dbfs=-1.0,
        )
        self.assertEqual(verdict, "疑似升频", reasons)

    def test_spectral_holes_detects_narrow_dips(self) -> None:
        """4~16kHz 的窄带凹陷应被计数。"""
        import numpy as np

        from seqaudio.core.authenticity import _spectral_holes

        freqs = np.arange(0, 22050, 5.4)
        db = np.full(freqs.size, -40.0)
        # 在 6k / 9k / 12k 各挖一个 200Hz 宽、40dB 深的窄洞
        for center in (6000.0, 9000.0, 12000.0):
            db[(freqs > center) & (freqs < center + 200)] = -80.0
        self.assertGreaterEqual(_spectral_holes(freqs, db), 3.0)

    def test_sfb21_notch_depth(self) -> None:
        import numpy as np

        from seqaudio.core.authenticity import _sfb21_notch

        freqs = np.arange(0, 22050, 5.4)
        db = np.full(freqs.size, -40.0)
        db[(freqs >= 15500) & (freqs <= 16800)] = -60.0   # 20dB 凹陷
        self.assertGreater(_sfb21_notch(freqs, db), 10.0)

    @unittest.skipUnless(
        __import__("seqaudio.core.authenticity", fromlist=["x"]).ffmpeg_available(),
        "需要 ffmpeg",
    )
    def test_16bit_padded_to_24bit_file(self) -> None:
        """真实文件：16bit 内容装进 24bit 容器，应判「疑似升位深」。"""
        import subprocess

        ff = __import__("seqaudio.core.authenticity", fromlist=["x"]).find_ffmpeg()
        tmp = Path(tempfile.mkdtemp(prefix="seq_up_"))
        try:
            src16 = tmp / "a16.wav"
            subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-y",
                 "-f", "lavfi", "-i", "anoisesrc=d=8:c=pink:a=0.5",
                 "-ar", "44100", "-ac", "1", "-c:a", "pcm_s16le", str(src16)],
                check=True, timeout=120,
            )
            src24 = tmp / "a24.wav"
            subprocess.run(
                [ff, "-hide_banner", "-loglevel", "error", "-y",
                 "-i", str(src16), "-c:a", "pcm_s24le", str(src24)],
                check=True, timeout=120,
            )
            info = probe.probe(src24, with_cue=False).info
            self.assertEqual(info.bit_depth, 24)
            from seqaudio.core.authenticity import analyze

            result = analyze(src24, info)
            self.assertEqual(result.verdict, "疑似升位深", result.reasons)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestDsdSourceCliff(unittest.TestCase):
    """PCM 转 DSD 的带宽断崖检测（V1.2.5）。

    源码的带宽边界会留在基带上：44.1k 源在 22.05kHz、48k 源在 24kHz、
    96k 源在 48kHz。断崖之上若是数字静音，说明那片高频从来就不存在。
    """

    def _spectrum(self, boundary: float, above_db: float = -110.0):
        import numpy as np

        freqs = np.arange(0.0, 176_400.0, 10.0)
        db = np.where(freqs < boundary, -30.0, above_db)
        return freqs, db

    def test_48k_source_detected(self) -> None:
        from seqaudio.core.authenticity import _pcm_source_cliff

        freqs, db = self._spectrum(24_000.0)
        source, step, above = _pcm_source_cliff(freqs, db, 176_400.0)
        self.assertEqual(source, 48_000.0, (step, above))
        self.assertGreater(step, 20.0)

    def test_96k_source_detected(self) -> None:
        from seqaudio.core.authenticity import _pcm_source_cliff

        freqs, db = self._spectrum(48_000.0)
        source, _, _ = _pcm_source_cliff(freqs, db, 176_400.0)
        self.assertEqual(source, 96_000.0)

    def test_44k_source_detected(self) -> None:
        from seqaudio.core.authenticity import _pcm_source_cliff

        freqs, db = self._spectrum(22_050.0)
        source, _, _ = _pcm_source_cliff(freqs, db, 176_400.0)
        self.assertEqual(source, 44_100.0)

    def test_genuine_dsd_has_no_cliff(self) -> None:
        """真 DSD 能量一路延伸，断崖之上不是静音 → 不该报。"""
        from seqaudio.core.authenticity import _pcm_source_cliff

        freqs, db = self._spectrum(22_050.0, above_db=-50.0)
        source, _, _ = _pcm_source_cliff(freqs, db, 176_400.0)
        self.assertEqual(source, 0.0)

    def test_gradual_rolloff_not_a_cliff(self) -> None:
        """平缓滚降（老录音）不是断崖。"""
        import numpy as np

        from seqaudio.core.authenticity import _pcm_source_cliff

        freqs = np.arange(0.0, 176_400.0, 10.0)
        db = np.where(freqs < 22_050.0, -30.0, -30.0 - (freqs - 22_050.0) / 1000.0)
        source, _, _ = _pcm_source_cliff(freqs, db, 176_400.0)
        self.assertEqual(source, 0.0)

    def test_judge_prefers_pcm_cliff(self) -> None:
        """给出断崖证据时，判定要直接落到「疑似假 DSD」。"""
        from seqaudio.core.authenticity import _judge_dsd

        verdict, _, reasons = _judge_dsd(
            12.0, -20.0, 0.0, 0.0, 0.0,
            pcm_source=48_000.0, pcm_step=39.0, pcm_above=-81.0,
        )
        self.assertEqual(verdict, "疑似假 DSD", reasons)
        self.assertTrue(any("48.0kHz" in r for r in reasons), reasons)

    def test_judge_accepts_high_freq_extension(self) -> None:
        """无断崖 + 超声基底 → 真 DSD。"""
        from seqaudio.core.authenticity import _judge_dsd

        verdict, score, reasons = _judge_dsd(
            9.0, -5.0, 29_000.0, 20.0, -50.0,
            pcm_source=0.0, ultra_body=-39.0,
        )
        self.assertEqual(verdict, "真 DSD", reasons)
        self.assertGreaterEqual(score, 0.8)


class TestDsdSignalEdge(unittest.TestCase):
    """DSD 信号边界判据（V1.2.6）。

    断崖判据看的是「电平台阶」，会被调制器带内噪声盖住；波动判据只看
    「这个频段还跟不跟着音乐起伏」，因此能补上台阶被淹没的盲区。
    """

    def _rows(self, edge: float, nyquist: float = 352_800.0, frames: int = 120):
        """构造分帧频谱：``edge`` 以下随时间起伏，以上是恒定噪声。"""
        import numpy as np

        freqs = np.arange(0.0, nyquist / 2.0, 40.0)
        rows = np.full((frames, freqs.size), -60.0)
        t = np.linspace(0.0, 6.0, frames)[:, None]
        swing = 9.0 * np.sin(2.0 * np.pi * 0.7 * t)
        variable = (freqs >= 5_000.0) & (freqs < edge)
        rows[:, variable] += swing
        rows += np.random.default_rng(7).normal(0.0, 0.2, rows.shape)
        return rows, freqs

    def test_edge_at_cd_boundary(self) -> None:
        from seqaudio.core.authenticity import _variation_edge

        rows, freqs = self._rows(22_050.0)
        edge, std, floor = _variation_edge(rows, freqs, 176_400.0)
        self.assertAlmostEqual(edge, 22_000.0, delta=2_100.0, msg=(std, floor))

    def test_edge_at_96k_boundary(self) -> None:
        from seqaudio.core.authenticity import _variation_edge

        rows, freqs = self._rows(48_000.0)
        edge, _, _ = _variation_edge(rows, freqs, 352_800.0)
        self.assertAlmostEqual(edge, 48_000.0, delta=2_100.0)

    def test_no_edge_when_content_extends(self) -> None:
        """内容一路延伸到扫描上限 → 不该报边界。"""
        from seqaudio.core.authenticity import _variation_edge

        rows, freqs = self._rows(200_000.0)
        edge, _, _ = _variation_edge(rows, freqs, 352_800.0)
        self.assertEqual(edge, 0.0)

    def test_empty_input_is_safe(self) -> None:
        import numpy as np

        from seqaudio.core.authenticity import _variation_edge

        edge, std, floor = _variation_edge(np.empty((0, 0)), np.empty(0), 176_400.0)
        self.assertEqual((edge, std, floor), (0.0, 0.0, 0.0))

    def test_grid_matching(self) -> None:
        from seqaudio.core.authenticity import _match_edge_grid

        self.assertEqual(_match_edge_grid(22_000.0), 44_100.0)
        self.assertEqual(_match_edge_grid(23_000.0), 44_100.0)
        self.assertEqual(_match_edge_grid(26_000.0), 48_000.0)
        self.assertEqual(_match_edge_grid(46_000.0), 96_000.0)
        self.assertEqual(_match_edge_grid(98_000.0), 192_000.0)
        # 落在网格之间的自然滚降不该被硬套
        self.assertEqual(_match_edge_grid(37_000.0), 0.0)
        self.assertEqual(_match_edge_grid(70_000.0), 0.0)

    def test_judge_flags_96k_source(self) -> None:
        """台阶被噪声淹没的 DSD128（Taylor 那类）靠边界判据抓出来。"""
        from seqaudio.core.authenticity import _judge_dsd

        verdict, score, reasons = _judge_dsd(
            9.0, -5.0, 29_000.0, 20.0, -45.0,
            pcm_source=0.0, ultra_body=-39.0,
            edge_hz=46_000.0, edge_std=2.4, edge_floor=1.5,
        )
        self.assertEqual(verdict, "疑似假 DSD", reasons)
        self.assertLess(score, 0.5)
        self.assertTrue(any("96.0kHz" in r for r in reasons), reasons)

    def test_judge_flags_sub_cd_bandwidth(self) -> None:
        """带宽连 CD 都不到的（20kHz 就像 320k MP3）要单独报出来。"""
        from seqaudio.core.authenticity import _judge_dsd

        verdict, _, reasons = _judge_dsd(
            12.0, -10.0, 20_000.0, 15.0, -55.0,
            pcm_source=0.0, ultra_body=-45.0,
            edge_hz=20_000.0, edge_std=1.4, edge_floor=1.0,
        )
        self.assertEqual(verdict, "疑似假 DSD", reasons)
        self.assertTrue(any("有损编码器" in r for r in reasons), reasons)

    def test_judge_keeps_genuine_when_edge_off_grid(self) -> None:
        """边界落在网格之外（自然滚降）→ 仍然判真 DSD。"""
        from seqaudio.core.authenticity import _judge_dsd

        verdict, _, reasons = _judge_dsd(
            9.0, -5.0, 29_000.0, 20.0, -50.0,
            pcm_source=0.0, ultra_body=-39.0,
            edge_hz=37_000.0, edge_std=1.8, edge_floor=1.2,
        )
        self.assertEqual(verdict, "真 DSD", reasons)

    def test_judge_without_edge_still_works(self) -> None:
        """没算出边界（默认 0）时不能误触发，保持原有行为。"""
        from seqaudio.core.authenticity import _judge_dsd

        verdict, _, _ = _judge_dsd(12.0, -20.0, 30_000.0, 5.0, -50.0)
        self.assertEqual(verdict, "真 DSD")


class TestBatchConsensus(unittest.TestCase):
    """同批一致性推定（V1.2.7）。

    同一批资源的出处只有一次：真 SACD 与 PCM 转制不会混在同一批里。
    转换器的噪声整形斜率是稳定指纹（实测批内标准差 ≤0.2），据此把
    「边界被噪声盖住」的漏网文件补判回来。
    """

    def _result(self, verdict: str, slope: float):
        from seqaudio.core.authenticity import AuthenticityResult

        result = AuthenticityResult()
        result.verdict = verdict
        result.noise_shaping_slope = slope
        result.reasons = ["原始理由"]
        return result

    def test_matching_fingerprint_gets_flagged(self) -> None:
        from seqaudio.core.authenticity import apply_batch_consensus

        pairs = [(Path(f"/a/t{i}.dsf"), self._result("疑似假 DSD", 21.3)) for i in range(6)]
        pairs += [(Path(f"/a/u{i}.dsf"), self._result("真 DSD", 21.4)) for i in range(3)]
        changed = apply_batch_consensus(pairs)
        self.assertEqual(len(changed), 3)
        for _, result in changed:
            self.assertEqual(result.verdict, "疑似假 DSD")
            self.assertIn("同目录", result.reasons[0])

    def test_different_fingerprint_is_kept(self) -> None:
        """指纹对不上就不能推定 —— 那可能是另一台调制器录的真 DSD。"""
        from seqaudio.core.authenticity import apply_batch_consensus

        pairs = [(Path(f"/a/t{i}.dsf"), self._result("疑似假 DSD", 21.3)) for i in range(6)]
        pairs.append((Path("/a/genuine.dsf"), self._result("真 DSD", 6.2)))
        self.assertEqual(apply_batch_consensus(pairs), [])

    def test_minority_fake_does_not_trigger(self) -> None:
        """只有一两首是假时不足以推定整批，避免把真 DSD 连坐。"""
        from seqaudio.core.authenticity import apply_batch_consensus

        pairs = [(Path(f"/a/t{i}.dsf"), self._result("真 DSD", 6.2)) for i in range(6)]
        pairs.append((Path("/a/x.dsf"), self._result("疑似假 DSD", 21.3)))
        self.assertEqual(apply_batch_consensus(pairs), [])

    def test_small_group_ignored(self) -> None:
        from seqaudio.core.authenticity import apply_batch_consensus

        pairs = [
            (Path("/a/x.dsf"), self._result("疑似假 DSD", 21.3)),
            (Path("/a/y.dsf"), self._result("真 DSD", 21.3)),
        ]
        self.assertEqual(apply_batch_consensus(pairs), [])

    def test_folders_are_independent(self) -> None:
        from seqaudio.core.authenticity import apply_batch_consensus

        pairs = [(Path("/a/only.dsf"), self._result("疑似假 DSD", 21.3))]
        pairs += [(Path(f"/b/t{i}.dsf"), self._result("真 DSD", 6.2)) for i in range(3)]
        self.assertEqual(apply_batch_consensus(pairs), [])

    def test_result_keeps_original_reason(self) -> None:
        """推定要保留原始证据，便于用户复核。"""
        from seqaudio.core.authenticity import apply_batch_consensus

        pairs = [(Path(f"/a/t{i}.dsf"), self._result("疑似假 DSD", 21.3)) for i in range(4)]
        pairs.append((Path("/a/u.dsf"), self._result("真 DSD", 21.3)))
        changed = apply_batch_consensus(pairs)
        self.assertEqual(len(changed), 1)
        self.assertIn("原始理由", changed[0][1].reasons)


class TestAuthenticityOldCD(unittest.TestCase):
    """老 CD / 模拟母带：高频平滑滚降不能被判成假无损（V1.1 修复）。"""

    def setUp(self) -> None:
        from seqaudio.core.authenticity import ffmpeg_available

        if not ffmpeg_available():
            self.skipTest("未安装 ffmpeg，跳过鉴定测试")
        self.tmp = Path(tempfile.mkdtemp(prefix="seq_auth_"))

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _analyze(self, name: str, **kw):
        from seqaudio.core.authenticity import analyze
        from seqaudio.core.model import AudioInfo

        path = make_samples.make_rolloff_wav(self.tmp / f"{name}.wav", **kw)
        info = AudioInfo(
            fmt="WAV", lossless=True, sample_rate=44100,
            bit_depth=16, channels=1, duration=4.0,
        )
        return analyze(path, info)

    def test_hard_cutoff_is_fake(self) -> None:
        """断崖式截止（有损转码特征）仍要判假。"""
        result = self._analyze("hard", knee_hz=15000, slope_db_per_khz=300)
        self.assertEqual(result.verdict, "假无损")

    def test_smooth_rolloff_old_cd_is_true(self) -> None:
        """平滑滚降到 19kHz 的老录音，不能判假。"""
        result = self._analyze("smooth", knee_hz=15000, slope_db_per_khz=15)
        self.assertEqual(result.verdict, "真无损")
        self.assertTrue(any("老录音" in reason for reason in result.reasons))

    def test_full_band_is_true(self) -> None:
        result = self._analyze("full", knee_hz=22000, slope_db_per_khz=300)
        self.assertEqual(result.verdict, "真无损")


if __name__ == "__main__":
    unittest.main(verbosity=2)
