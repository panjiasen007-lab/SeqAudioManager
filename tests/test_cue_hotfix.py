"""v1.8.2 热修回归：多 FILE CUE 误展开（用户实测 19 首变 361 首）与删除选中报错。

场景还原：EAC「分轨抓轨 + 保存 CUE」会在单曲目录里生成**多 FILE CUE**
（每轨一个 FILE 行）。旧逻辑把目录里唯一的 CUE 兜底挂给每个单曲、再把
多轨 CUE 逐轨展开，19 首单曲就被复制成 361 行。
"""

import tempfile
import unittest
from pathlib import Path

from seqaudio.core import cue as cue_mod
from seqaudio.core.model import AudioInfo, Track
from seqaudio.core.probe import expand_cue_tracks, find_cue_for


MULTIFILE_CUE = (
    'PERFORMER "Various"\n'
    'TITLE "Kuschelrock 23 CD1"\n'
    'FILE "01. a.flac" WAVE\n'
    '  TRACK 01 AUDIO\n    TITLE "A"\n    INDEX 01 00:00:00\n'
    'FILE "02. b.flac" WAVE\n'
    '  TRACK 02 AUDIO\n    TITLE "B"\n    INDEX 01 00:00:00\n'
)


class CueHotfixTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_expand_keeps_multifile_cue_singles(self) -> None:
        """多 FILE CUE 是「每轨一文件」的索引清单，不得展开。"""
        cue_path = self.tmp / "Kuschelrock 23 CD1.cue"
        cue_path.write_text(MULTIFILE_CUE, encoding="utf-8")
        track = Track(path=self.tmp / "02. b.flac")
        track.cue = cue_mod.parse_cue(cue_path)
        self.assertEqual(track.cue.file_count, 2)

        expanded = expand_cue_tracks([track])
        self.assertEqual(len(expanded), 1)
        self.assertIsNone(expanded[0].cue_track)
        self.assertTrue(any("按单曲保留" in n for n in expanded[0].notes))

    def test_expand_rejects_cue_longer_than_audio(self) -> None:
        """单 FILE 整碟 CUE 挂到单曲上：末轨起点超出音频时长时不展开。"""
        from tests.make_samples import make_cue

        track = Track(path=self.tmp / "album.flac")
        track.info = AudioInfo(fmt="FLAC", lossless=True, duration=240.0)
        track.cue = cue_mod.parse_cue(make_cue(self.tmp / "album.cue"))

        expanded = expand_cue_tracks([track])
        self.assertEqual(len(expanded), 1)
        self.assertIsNone(expanded[0].cue_track)
        self.assertTrue(any("超出音频时长" in n for n in expanded[0].notes))

    def test_expand_still_works_for_matching_whole_track(self) -> None:
        """正常「整轨 + 单 FILE CUE」必须照常展开（防误伤）。"""
        from tests.make_samples import make_cue

        track = Track(path=self.tmp / "album.wav")
        track.info = AudioInfo(fmt="WAV", lossless=True, duration=750.0)
        track.cue = cue_mod.parse_cue(make_cue(self.tmp / "album.cue"))

        expanded = expand_cue_tracks([track])
        self.assertEqual(len(expanded), 3)
        self.assertEqual([t.cue_track for t in expanded], [0, 1, 2])

    def test_find_cue_for_ignores_multifile_cue(self) -> None:
        """目录里的多 FILE CUE 不应关联给任何单曲。"""
        cue_path = self.tmp / "Kuschelrock 23 CD1.cue"
        cue_path.write_text(MULTIFILE_CUE, encoding="utf-8")
        first = self.tmp / "01. a.flac"
        second = self.tmp / "02. b.flac"
        first.write_bytes(b"")
        second.write_bytes(b"")

        self.assertIsNone(find_cue_for(first))
        self.assertIsNone(find_cue_for(second))

    def test_main_window_has_no_ui_core_relative_import(self) -> None:
        """删除选中曾因 `from .core.quarantine`（= seqaudio.ui.core，不存在）崩溃。"""
        source = (
            Path(__file__).resolve().parent.parent
            / "src" / "seqaudio" / "ui" / "main_window.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("from .core.", source)
        self.assertIn("from ..core.quarantine import send_to_recycle_bin", source)


if __name__ == "__main__":
    unittest.main()
