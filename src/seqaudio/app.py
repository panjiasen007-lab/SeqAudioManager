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
    app = QApplication(argv if argv is not None else sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("SeqAudio")

    # Qt6 默认已启用高 DPI 缩放，这里只需统一圆整策略，避免 125%/150% 下控件错位
    try:
        app.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    font = QFont("Microsoft YaHei UI", 9)
    app.setFont(font)
    app.setStyleSheet(QSS)

    icon_path = Path(__file__).resolve().parent / "assets" / "app.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))
    return app


def main(argv: list[str] | None = None) -> int:
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
