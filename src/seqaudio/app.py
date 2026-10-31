"""应用入口：高 DPI、字体、主题、免责声明、异常兜底。"""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, __version__
from .core.config import Settings
from .core.logging_setup import setup_logging
from .ui.disclaimer import DisclaimerDialog
from .ui.main_window import MainWindow
from .ui.theme import QSS

log = logging.getLogger(__name__)


def _install_excepthook(app: QApplication) -> None:
    """未捕获异常不静默退出，弹窗告知并写日志。"""

    def hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.critical("未捕获异常：\n%s", text)
        try:
            QMessageBox.critical(
                None,
                "程序遇到问题",
                f"发生了未预期的错误，操作已中止。\n\n{exc_value}\n\n"
                "详细信息已写入日志，可在「帮助 → 打开日志目录」中查看。",
            )
        except Exception:
            pass

    sys.excepthook = hook


def create_app(argv: list[str] | None = None) -> QApplication:
    # 复用已存在的实例：Qt 同一进程只允许一个 QApplication，
    # 测试与嵌入式调用常常已经建好了实例
    app = QApplication.instance() or QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("SeqAudio")

    # Fusion 是唯一完全遵循 QSS 的内置样式；Windows 原生样式会用自己的绘制逻辑，
    # 把圆角、配色、内边距全部覆盖掉，界面立刻"回退"成系统默认长相
    app.setStyle("Fusion")

    # Qt6 默认已启用高 DPI 缩放，这里统一圆整策略，避免 125%/150% 下控件错位
    try:
        app.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(13)
    font.setHintingPreference(QFont.PreferFullHinting)
    app.setFont(font)
    app.setStyleSheet(QSS)

    icon_path = Path(__file__).resolve().parent / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    return app


def _selftest() -> int:
    """`--selftest`：无人值守自检，把结果写到临时文件。

    打包成 --windowed 后没有控制台，只能靠文件回读来确认
    「内置 ffmpeg 是否被正确打进 EXE、鉴定链路是否可用」。
    """
    import json
    import tempfile

    report: dict = {"version": __version__}
    try:
        from .core.authenticity import ffmpeg_available, find_ffmpeg

        report["ffmpeg_available"] = ffmpeg_available()
        report["ffmpeg_path"] = find_ffmpeg() or ""
        if report["ffmpeg_path"]:
            report["ffmpeg_exists"] = Path(report["ffmpeg_path"]).exists()
            report["ffmpeg_size_mb"] = round(
                Path(report["ffmpeg_path"]).stat().st_size / 1024 / 1024, 1
            )
    except Exception as exc:  # noqa: BLE001
        report["ffmpeg_error"] = str(exc)
    try:
        import mutagen

        report["mutagen"] = mutagen.version_string
    except Exception as exc:  # noqa: BLE001
        report["mutagen_error"] = str(exc)
    try:
        from .core.probe import SUPPORTED_EXTS

        report["formats"] = len(SUPPORTED_EXTS)
    except Exception as exc:  # noqa: BLE001
        report["formats_error"] = str(exc)

    target = Path(tempfile.gettempdir()) / "seqaudio_selftest.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def _selftest_file(target: str) -> int:
    """`--selftest-file <路径>`：对指定文件跑一遍完整鉴定链路并落盘结果。

    用于验证「打包后的 EXE 真的能解码并分析」，而不只是「ffmpeg 文件存在」。
    """
    import json
    import tempfile

    report: dict = {"input": target}
    try:
        from .core.authenticity import analyze
        from .core.probe import probe

        path = Path(target)
        info = probe(path, with_cue=False).info
        report["probe"] = {
            "fmt": info.fmt,
            "spec": info.spec_text,
            "lossless": info.lossless,
            "duration": info.duration,
        }
        result = analyze(path, info)
        report["verdict"] = result.verdict
        report["score"] = result.score
        report["cutoff_hz"] = result.cutoff_hz
        report["nyquist_hz"] = result.nyquist_hz
        report["effective_bit_depth"] = result.bit_depth_effective
        report["reasons"] = result.reasons
        report["error"] = result.error
    except Exception as exc:  # noqa: BLE001
        report["exception"] = f"{type(exc).__name__}: {exc}"

    out = Path(tempfile.gettempdir()) / "seqaudio_selftest_file.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv
    if "--selftest" in args:
        return _selftest()
    if "--selftest-file" in args:
        index = args.index("--selftest-file")
        target = args[index + 1] if index + 1 < len(args) else ""
        return _selftest_file(target)

    setup_logging()
    log.info("启动 %s v%s", APP_NAME, __version__)

    app = create_app(argv)
    _install_excepthook(app)

    settings = Settings.load()

    if not DisclaimerDialog.is_accepted(settings.accepted_disclaimer):
        dialog = DisclaimerDialog()
        if dialog.exec() != DisclaimerDialog.Accepted:
            log.info("用户未同意条款，退出")
            return 0
        settings.accepted_disclaimer = DisclaimerDialog.acceptance_stamp()
        settings.save()

    window = MainWindow(settings)
    window.show()
    return app.exec()
