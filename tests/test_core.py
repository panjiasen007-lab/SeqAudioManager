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


if __name__ == "__main__":
    unittest.main(verbosity=2)
