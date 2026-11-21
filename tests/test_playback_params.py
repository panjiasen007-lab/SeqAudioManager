"""播放参数链路回归：音量 / 整数通路 / 缓冲深度 / 参数钳制。

运行：``python -m unittest tests.test_playback_params -q``（离屏，不开流、不碰声卡）。

为什么必须锁死这几条
--------------------
v1.8.0 需求 5 做播放参数排查时抓到两个「听起来像玄学、其实是代码写反」的问题：

1. **静音**：``int_pipeline_allowed`` 把「软件音量会改样本」的判断放在了
   ``not dsp_bypass`` 分支里。默认真的是 ``dsp_bypass=True``，于是
   「直通 + 软件音量 -20 dB + 整数通路」被判成"不用改样本" → 解码器给 int16
   → 回调里 ``buf *= 浮点增益`` 在 numpy 2.x 抛 ``UFuncOutputCastingError``
   → 异常被回调兜底吞掉 → 整场静音，界面只有计数器在动。
2. **音量自己变回满**：``set_bypass(True)`` 无条件把 dsp 增益清零。而
   「保存设置」会重放 ``set_bypass``，于是用户在播放条上拖好的音量，
   点一次保存就回到 0 dB（= 满音量）。这也正是「独占模式为什么很大声」
   的一部分观感来源。

这两条都只靠真开流才暴露，所以这里用**不开流**的等价断言守住。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from unittest import mock  # noqa: E402

from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import engine as engine_mod  # noqa: E402
from seqaudio.player.dsp import int_pipeline_allowed  # noqa: E402
from seqaudio.player.engine import PlayerEngine  # noqa: E402


def make_settings(**overrides) -> Settings:
    settings = Settings()
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


class IntPipelineTest(unittest.TestCase):
    """整数通路只在「确实没有任何一级改样本」时才成立。"""

    def test_software_volume_blocks_int_pipeline_even_when_bypassed(self) -> None:
        settings = make_settings(
            playback_int_pipeline=True,
            playback_dsp_bypass=True,      # 默认值，就是漏判的那一档
            playback_volume_mode="software",
            playback_volume_db=-20.0,
        )
        self.assertFalse(
            int_pipeline_allowed(settings),
            "软件音量会改样本，整数通路上做浮点乘会让回调抛异常导致整场静音",
        )

    def test_zero_db_software_volume_still_allows_int_pipeline(self) -> None:
        """0 dB 的软件增益是数学恒等，不必强制浮点（保位完美的可用性）。"""
        settings = make_settings(
            playback_int_pipeline=True,
            playback_dsp_bypass=True,
            playback_volume_mode="software",
            playback_volume_db=0.0,
        )
        self.assertTrue(int_pipeline_allowed(settings))

    def test_bypass_mode_without_eq_allows_int_pipeline(self) -> None:
        settings = make_settings(
            playback_int_pipeline=True,
            playback_dsp_bypass=True,
            playback_volume_mode="bypass",
        )
        self.assertTrue(int_pipeline_allowed(settings))

    def test_eq_blocks_int_pipeline(self) -> None:
        settings = make_settings(
            playback_int_pipeline=True,
            playback_dsp_bypass=False,
            playback_eq_enabled=True,
        )
        self.assertFalse(int_pipeline_allowed(settings))

    def test_crossfade_blocks_int_pipeline(self) -> None:
        settings = make_settings(
            playback_int_pipeline=True,
            playback_dsp_bypass=True,
            playback_crossfade_sec=3.0,
        )
        self.assertFalse(int_pipeline_allowed(settings))


class SoftwareVolumeSurvivesBypassTest(unittest.TestCase):
    """``dsp_bypass`` 只旁路 EQ/调音，不许动软件音量。"""

    def setUp(self) -> None:
        self.engine = PlayerEngine(
            make_settings(
                playback_volume_mode="software",
                playback_volume_db=-18.0,
                playback_dsp_bypass=True,
            )
        )

    def tearDown(self) -> None:
        try:
            self.engine.shutdown()
        except Exception:  # noqa: BLE001 - 清理失败不影响断言结论
            pass

    def test_gain_restored_at_construction(self) -> None:
        self.assertAlmostEqual(self.engine._dsp.gain.db, -18.0, places=3)

    def test_set_bypass_keeps_software_volume(self) -> None:
        self.engine.set_volume(-18.0)
        self.engine.set_bypass(True)      # 等价于「保存设置」重放一遍
        self.assertAlmostEqual(
            self.engine._dsp.gain.db, -18.0, places=3,
            msg="直通不该把用户调好的软件音量清零（否则保存一次就变回满音量）",
        )

    def test_set_bypass_off_reapplies_volume(self) -> None:
        self.engine.set_volume(-18.0)
        self.engine.set_bypass(False)
        self.assertAlmostEqual(self.engine._dsp.gain.db, -18.0, places=3)

    def test_bypass_mode_zeroes_gain(self) -> None:
        """模式回到 bypass（用户双击归零）时才允许增益归零。"""
        self.engine.settings.playback_volume_mode = "bypass"
        self.engine.set_bypass(True)
        self.assertAlmostEqual(self.engine._dsp.gain.db, 0.0, places=3)


class BufferDepthTest(unittest.TestCase):
    """「缓冲深度」必须真的作用到 PortAudio（曾经只存不用）。"""

    def test_buffer_ms_reaches_output_latency(self) -> None:
        engine = PlayerEngine(make_settings(playback_buffer_ms=120))
        captured: dict = {}
        try:
            with mock.patch.object(engine_mod, "AudioOutput") as fake:
                engine._plan = None
                engine._make_output(mock.MagicMock(), None)
                _, kwargs = fake.call_args
                captured = kwargs
        finally:
            engine.shutdown()
        self.assertAlmostEqual(captured.get("latency", 0.0), 0.120, places=4,
                               msg="120 ms 的缓冲深度应作为 0.12 秒延迟提示传给输出层")

    def test_buffer_ms_has_floor(self) -> None:
        """低于 40 ms 必然欠载：引擎侧也要钳住（不能只靠界面滑块）。"""
        engine = PlayerEngine(make_settings(playback_buffer_ms=5))
        try:
            with mock.patch.object(engine_mod, "AudioOutput") as fake:
                engine._plan = None
                engine._make_output(mock.MagicMock(), None)
                _, kwargs = fake.call_args
                latency = kwargs.get("latency", 0.0)
        finally:
            engine.shutdown()
        self.assertAlmostEqual(latency, 0.040, places=4)


class SettingsClampTest(unittest.TestCase):
    """配置文件被手改/降级带来时，参数必须折进安全区间。"""

    def test_out_of_range_values_are_clamped(self) -> None:
        settings = Settings.from_dict(
            {
                "playback_buffer_ms": 5,
                "playback_block_frames": 7,
                "playback_ramp_ms": 100,
                "playback_volume_db": 999,
                "playback_crossfade_sec": 30,
                "playback_dsd_pcm_rate": 96000,
                "playback_volume_mode": "loud",
                "playback_output_mode": "KTV",
                "playback_mode": "random",
            }
        )
        self.assertEqual(settings.playback_buffer_ms, 40)
        self.assertEqual(settings.playback_block_frames, 128)
        self.assertEqual(settings.playback_ramp_ms, 5)
        self.assertEqual(settings.playback_volume_db, 12.0)
        self.assertEqual(settings.playback_crossfade_sec, 10.0)
        self.assertEqual(settings.playback_dsd_pcm_rate, 176400)
        self.assertEqual(settings.playback_volume_mode, "bypass")
        self.assertEqual(settings.playback_output_mode, "shared")
        self.assertEqual(settings.playback_mode, "sequential")

    def test_legal_values_are_preserved(self) -> None:
        settings = Settings.from_dict(
            {
                "playback_buffer_ms": 120,
                "playback_volume_db": -12.5,
                "playback_dsd_pcm_rate": 352800,
                "playback_output_mode": "exclusive",
                "playback_volume_mode": "software",
            }
        )
        self.assertEqual(settings.playback_buffer_ms, 120)
        self.assertAlmostEqual(settings.playback_volume_db, -12.5)
        self.assertEqual(settings.playback_dsd_pcm_rate, 352800)
        self.assertEqual(settings.playback_output_mode, "exclusive")
        self.assertEqual(settings.playback_volume_mode, "software")

    def test_nan_and_garbage_do_not_crash(self) -> None:
        settings = Settings.from_dict(
            {"playback_volume_db": float("nan"), "playback_block_frames": "abc"}
        )
        self.assertEqual(settings.playback_volume_db, 0.0)
        self.assertEqual(settings.playback_block_frames, 1024)


if __name__ == "__main__":
    unittest.main()
