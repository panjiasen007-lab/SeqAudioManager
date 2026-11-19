"""v1.7 信号路径与话术分级自测（T03）。

运行：``python tests/test_player_wording.py``（纯逻辑，不需要声卡）。

两条底线在这里被逐条钉住：

1. **四级话术只能说前三**（L0/L1/L2），L3 永久禁止；
2. **面板不许说谎**：请求独占但实际降级共享时，信号路径必须显示"共享"。

第二条是 v1.6 的真实缺陷：``_configure_signal()`` 把 ``settings.playback_exclusive``
（请求值）当实际值传下去，用户勾了独占、设备给了共享，界面照样写"独占"。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seqaudio.player.constants import StageStatus  # noqa: E402
from seqaudio.player.signal_path import (  # noqa: E402
    FORBIDDEN_CLAIMS,
    TEXT_NO_SOFTWARE_CHANGE,
    TEXT_OUTPUT_ASIO_MISSING,
    TEXT_OUTPUT_LOOSE,
    TEXT_OUTPUT_REFUSED,
    TEXT_OUTPUT_SHARED,
    TEXT_OUTPUT_STRICT,
    SignalPath,
    assert_wording,
)


class TestBackwardCompatibility(unittest.TestCase):
    """v1.6 的调用方式不能被 v1.7 的参数破坏。"""

    def test_old_call_still_works(self) -> None:
        path = SignalPath().configure(
            source="FLAC", sample_fmt="s16le", channels=2, sample_rate=44100,
            int_pipeline=True, rate_want=44100, rate_actual=44100,
            backend="mme", device="扬声器",
        )
        self.assertEqual(len(path.snapshot()), 9)
        self.assertEqual(path.summary(), TEXT_NO_SOFTWARE_CHANGE)

    def test_legacy_exclusive_flag_is_treated_as_actual(self) -> None:
        path = SignalPath().configure(
            sample_fmt="s16le", channels=2, sample_rate=44100, int_pipeline=True,
            rate_want=44100, rate_actual=44100, backend="wasapi",
            device="扬声器", exclusive=True, mode_label="loose",
        )
        self.assertIn(TEXT_OUTPUT_LOOSE, path.stage("output").detail)


class TestOutputStage(unittest.TestCase):
    """设计 §8.1 的六种场景。"""

    def _path(self, **kwargs) -> SignalPath:
        base = dict(
            source="FLAC", sample_fmt="s16le", channels=2, sample_rate=44100,
            rate_want=44100, rate_actual=44100, device="扬声器",
        )
        base.update(kwargs)
        return SignalPath().configure(**base)

    def test_strict_success(self) -> None:
        path = self._path(
            backend="wasapi", exclusive_actual=True, mode_label="strict",
            int_pipeline=True, system_mix_rate=48000,
        )
        stage = path.stage("output")
        self.assertIn(TEXT_OUTPUT_STRICT, stage.detail)
        self.assertIs(stage.status, StageStatus.PASSTHROUGH)

    def test_loose_success(self) -> None:
        path = self._path(backend="wasapi", exclusive_actual=True, mode_label="loose")
        stage = path.stage("output")
        self.assertIn(TEXT_OUTPUT_LOOSE, stage.detail)
        self.assertNotIn("严格", stage.detail)
        self.assertIs(stage.status, StageStatus.PASSTHROUGH)

    def test_requested_but_downgraded(self) -> None:
        """请求独占、实际共享：必须 FALLBACK + 写明"共享（系统混音器）"。"""
        path = self._path(
            backend="wasapi", exclusive=False, mode_label="shared",
            system_mix_rate=48000, mode_note="该设备不支持独占，已退回共享",
        )
        stage = path.stage("output")
        self.assertIn(TEXT_OUTPUT_SHARED, stage.detail)
        self.assertIs(stage.status, StageStatus.FALLBACK)
        self.assertIn("不支持独占", stage.detail)

    def test_device_refused_exclusive(self) -> None:
        path = self._path(
            backend="wasapi", exclusive=False, mode_label="shared",
            exclusive_refused=True, mode_note="该设备不支持独占",
        )
        stage = path.stage("output")
        self.assertIn(TEXT_OUTPUT_REFUSED, stage.detail)
        self.assertIs(stage.status, StageStatus.FALLBACK)

    def test_non_wasapi_backend(self) -> None:
        path = self._path(backend="mme", exclusive=False, mode_label="shared")
        stage = path.stage("output")
        self.assertIn("MME", stage.detail)
        self.assertIn("共享", stage.detail)
        self.assertIs(stage.status, StageStatus.PASSTHROUGH)

    def test_asio_missing(self) -> None:
        path = self._path(backend="asio", exclusive=False, mode_label="shared")
        stage = path.stage("output")
        self.assertEqual(stage.detail, TEXT_OUTPUT_ASIO_MISSING)
        self.assertIs(stage.status, StageStatus.ERROR)


class TestDeviceStage(unittest.TestCase):
    """设计 §8.2：绕开混音 / 源率回退都要能看出来。"""

    def test_mixer_bypassed_text(self) -> None:
        path = SignalPath().configure(
            sample_fmt="s16le", channels=2, sample_rate=44100,
            rate_want=44100, rate_actual=44100, backend="wasapi",
            exclusive_actual=True, mode_label="strict", system_mix_rate=48000,
            device_dtype="int16", latency_ms=40,
        )
        detail = path.stage("device").detail
        self.assertIn("44100 Hz", detail)
        self.assertIn("系统混音 48000 Hz", detail)
        self.assertIn("已绕开", detail)
        self.assertIs(path.stage("device").status, StageStatus.PASSTHROUGH)

    def test_rate_fallback_text(self) -> None:
        path = SignalPath().configure(
            sample_fmt="f32le", channels=2, sample_rate=48000,
            rate_want=44100, rate_actual=48000, backend="wasapi", device="扬声器",
        )
        detail = path.stage("device").detail
        self.assertIn("48000 Hz（源 44100 Hz）", detail)
        self.assertIs(path.stage("device").status, StageStatus.FALLBACK)
        self.assertIs(path.stage("resample").status, StageStatus.FALLBACK)

    def test_plain_text_without_mix_rate(self) -> None:
        path = SignalPath().configure(
            sample_fmt="s16le", channels=2, sample_rate=44100,
            rate_want=44100, rate_actual=44100, backend="mme", device="扬声器",
        )
        self.assertEqual(path.stage("device").detail.split(" · ")[0], "44100 Hz")


class TestWordingLevels(unittest.TestCase):
    """L0/L1/L2 的判定条件（L3 不存在）。"""

    def _strict(self) -> SignalPath:
        return SignalPath().configure(
            source="FLAC", sample_fmt="s32", channels=2, sample_rate=44100,
            int_pipeline=True, gain_active=False, eq_enabled=False,
            rate_want=44100, rate_actual=44100, backend="wasapi",
            exclusive_actual=True, mode_label="strict", system_mix_rate=48000,
        )

    def test_l2_strict_integer_no_dsp(self) -> None:
        path = self._strict()
        self.assertEqual(path.wording_level(), "L2")
        self.assertEqual(path.wording_key(), "sig_l2_no_resample")
        text = path.wording_text()
        self.assertIn("未重采样", text)
        self.assertIn("32 位容器", text)

    def test_l1_exclusive_but_float(self) -> None:
        path = SignalPath().configure(
            sample_fmt="f32le", channels=2, sample_rate=44100,
            int_pipeline=False, rate_want=44100, rate_actual=44100,
            backend="wasapi", exclusive_actual=True, mode_label="loose",
            system_mix_rate=48000,
        )
        self.assertEqual(path.wording_level(), "L1")
        self.assertIn("已绕开系统混音器", path.wording_text())
        self.assertIn("48000", path.wording_text())

    def test_l0_shared(self) -> None:
        path = SignalPath().configure(
            sample_fmt="f32le", channels=2, sample_rate=48000,
            rate_want=48000, rate_actual=48000, backend="wasapi",
            exclusive_actual=False, mode_label="shared", system_mix_rate=48000,
        )
        self.assertEqual(path.wording_level(), "L0")
        self.assertEqual(path.wording_text(), TEXT_NO_SOFTWARE_CHANGE)

    def test_l0_when_dsp_changes_samples(self) -> None:
        """严格独占但音量真的改了样本 → 不能说 L2（"未加软件增益"不成立）。"""
        path = SignalPath().configure(
            sample_fmt="s16le", channels=2, sample_rate=44100, int_pipeline=True,
            gain_db=-6.0, gain_active=True, rate_want=44100, rate_actual=44100,
            backend="wasapi", exclusive_actual=True, mode_label="strict",
            system_mix_rate=48000,
        )
        self.assertEqual(path.wording_level(), "L1", "有增益时只能降到 L1")

    def test_l0_when_rate_mismatch(self) -> None:
        path = SignalPath().configure(
            sample_fmt="s16le", channels=2, sample_rate=48000, int_pipeline=True,
            rate_want=44100, rate_actual=48000, backend="wasapi",
            exclusive_actual=True, mode_label="strict", system_mix_rate=48000,
        )
        self.assertEqual(path.wording_level(), "L0")


class TestForbiddenClaims(unittest.TestCase):
    """措辞守卫只收不放（设计 §7.3）。"""

    def test_blacklist_intact(self) -> None:
        for claim in ("bit-perfect", "bit perfect", "位完美", "逐样本一致", "比特完美"):
            self.assertIn(claim, FORBIDDEN_CLAIMS, f"禁用词 {claim} 不许删")

    def test_reverse_cases_degrade_to_l0(self) -> None:
        self.assertEqual(assert_wording("位完美已验证"), TEXT_NO_SOFTWARE_CHANGE)
        self.assertEqual(assert_wording("24bit 直通 DAC"), TEXT_NO_SOFTWARE_CHANGE)
        self.assertEqual(assert_wording("bit perfect 输出"), TEXT_NO_SOFTWARE_CHANGE)
        self.assertEqual(assert_wording("逐样本一致"), TEXT_NO_SOFTWARE_CHANGE)
        # 变体（空格/大小写）也要拦
        self.assertEqual(assert_wording("位 完美"), TEXT_NO_SOFTWARE_CHANGE)
        self.assertEqual(assert_wording("Bit-Perfect"), TEXT_NO_SOFTWARE_CHANGE)

    def test_normal_text_passes(self) -> None:
        for text in ("未做软件改动", "已绕开系统混音器（系统混音 48000 Hz · 本流 44100 Hz）", "48000 Hz"):
            self.assertEqual(assert_wording(text), text)

    def test_stage_detail_is_guarded(self) -> None:
        path = SignalPath()
        path.mark("device", StageStatus.CHANGED, "位完美已验证 44100 Hz")
        self.assertEqual(path.stage("device").detail, TEXT_NO_SOFTWARE_CHANGE)

    def test_summary_never_contains_forbidden(self) -> None:
        path = self._strict_with_note()
        text = path.to_text() + path.summary() + path.wording_text()
        for claim in FORBIDDEN_CLAIMS:
            self.assertNotIn(claim.lower(), text.lower())

    @staticmethod
    def _strict_with_note() -> SignalPath:
        return SignalPath().configure(
            sample_fmt="s16le", channels=2, sample_rate=44100, int_pipeline=True,
            rate_want=44100, rate_actual=44100, backend="wasapi",
            exclusive_actual=True, mode_label="strict", system_mix_rate=48000,
        )


class _StubOutput:
    """只为验证「引擎传实际值」的最小输出替身。"""

    def __init__(self, mode: str, exclusive: bool) -> None:
        self._snapshot = {
            "is_open": True,
            "mode": mode,
            "exclusive": exclusive,
            "system_mix_rate": 48000,
            "rate": 44100,
            "dtype": "int16",
            "latency_ms": 40.0,
            "device": "扬声器",
            "backend": "wasapi",
            "mode_note": "该设备不支持独占",
        }

    def snapshot(self) -> dict:
        return dict(self._snapshot)


class TestEngineReportsActualMode(unittest.TestCase):
    """引擎必须把**实际**模式传给信号路径（T03 的核心修复）。"""

    def test_downgrade_is_visible(self) -> None:
        from seqaudio.core.config import Settings
        from seqaudio.player.engine import PlayerEngine

        settings = Settings()
        settings.playback_output_backend = "wasapi"
        settings.playback_output_mode = "exclusive_strict"
        settings.playback_exclusive = True
        engine = PlayerEngine(settings)
        try:
            engine._output = _StubOutput(mode="shared", exclusive=False)
            # 直接调用私有配置函数：它只读 output.snapshot()，不需要真开流
            from seqaudio.player.decoder import DecodeSpec

            decode_spec = DecodeSpec(path=Path("D:/fake.flac"), sample_rate=44100, channels=2)
            engine._rate = 44100
            engine._channels = 2
            engine._configure_signal(decode_spec, None, 44100)
            stage = next(item for item in engine.signal_path.snapshot() if item["key"] == "output")
            self.assertEqual(stage["status"], StageStatus.FALLBACK.value)
            self.assertIn("共享", stage["detail"])
            self.assertNotIn("独占（严格）", stage["detail"])
        finally:
            engine._output = None
            engine.shutdown()

    def test_snapshot_exposes_wording(self) -> None:
        from seqaudio.core.config import Settings
        from seqaudio.player.engine import PlayerEngine

        engine = PlayerEngine(Settings())
        try:
            snapshot = engine.snapshot()
            self.assertIn(snapshot["wording_level"], ("L0", "L1", "L2"))
            self.assertNotIn("bit-perfect", str(snapshot).lower())
            self.assertEqual(snapshot["wording_text"], TEXT_NO_SOFTWARE_CHANGE)
            self.assertIn("output_mode", snapshot)
        finally:
            engine.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
