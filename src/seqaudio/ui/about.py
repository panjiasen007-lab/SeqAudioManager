"""关于对话框：版本、开源协议、第三方组件、运行环境。

这个窗口承担三件事
------------------
1. **合规**：GPLv3 要求分发时附带协议全文与版权声明，这里内嵌完整文本，
   而不是只给一个网址（离线也要能看到）；
2. **可诊断**：用户报问题时，"复制诊断信息"一键拿到版本/字体/路径/解码器，
   省掉十几个来回；
3. **体面**：开源软件也该有个像样的关于页，而不是一行 QMessageBox。
"""

from __future__ import annotations

import os
import platform
import sys
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, APP_NAME_EN, __version__
from ..core import paths
from . import i18n, theme
from .widgets import app_icon_pixmap

LICENSE_NAME = "GNU General Public License v3.0"

LICENSE_SUMMARY = """
<p style="line-height:170%">
<b>音序管家（{app}）</b> 是自由软件：你可以依据自由软件基金会发布的
<b>GNU 通用公共许可证第 3 版</b>（或你选择的任何更新版本）的条款，
再分发和／或修改它。</p>

<p style="line-height:170%">本程序分发时希望它有用，但<b>不提供任何担保</b>，
甚至不包含适销性或特定用途适用性的默示担保。详见 GNU 通用公共许可证。</p>

<p style="line-height:170%">你应当已经收到本程序的 GNU 通用公共许可证副本；
如果没有，请访问
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a>。</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
本程序不提供任何形式的保证。这是开源软件，欢迎在遵守 GPLv3 的前提下
自由使用、修改与再分发。<br/><br/>
GPLv3 的核心是「四大自由」：<br/>
· 为任何目的运行本程序的自由；<br/>
· 研究并修改本程序的自由（前提是能拿到源代码）；<br/>
· 再分发副本的自由；<br/>
· 改进本程序并向公众发布改进的自由。<br/><br/>
作为交换，任何分发本程序（或修改版）的人，都必须同样以 GPLv3 提供源代码。
</p>
""".strip()

THIRD_PARTY = [
    ("PySide6 / Qt 6", "LGPL-3.0", "界面框架（Qt for Python）", "https://www.qt.io/"),
    ("mutagen", "GPL-2.0-or-later", "音频标签读写（FLAC/ID3/APEv2/MP4/DSDIFF）",
     "https://mutagen.readthedocs.io/"),
    ("NumPy", "BSD-3-Clause", "频谱分析数值计算", "https://numpy.org/"),
    ("FFmpeg", "LGPL-2.1+ / GPL-2.0+", "解码 DSD/APE/WV/FLAC 做频谱分析",
     "https://ffmpeg.org/"),
    ("imageio-ffmpeg", "BSD-2-Clause", "随包分发的 ffmpeg 二进制",
     "https://github.com/imageio/imageio-ffmpeg"),
    ("Noto Sans SC", "SIL OFL 1.1", "界面中文字体（随包分发）",
     "https://fonts.google.com/noto"),
    ("JetBrains Mono", "SIL OFL 1.1", "数据列等宽字体（随包分发）",
     "https://www.jetbrains.com/lp/mono/"),
    ("py7zr", "LGPL-2.1", "安装包与便携版压缩（仅构建期）",
     "https://github.com/miurahr/py7zr"),
]

DISCLAIMER = (
    "本软件只修改音频文件的标签区块，不重新编码音频流，并按你的设置生成备份。"
    "但任何批量写操作都存在风险，请先用小样本验证，重要母带请自行另存。"
)


def _license_text() -> str:
    """读取随包的 GPLv3 全文。"""
    from ..core.paths import asset_path

    for name in ("LICENSE", "LICENSE.txt", "COPYING"):
        found = asset_path(name)
        if found is not None:
            try:
                return found.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return i18n.tr_text(
        "未能读取许可证文件。请访问 https://www.gnu.org/licenses/gpl-3.0.txt 获取\n"
        "GNU General Public License v3.0 全文。"
    )


def diagnostics() -> str:
    """可复制粘贴的诊断信息。"""
    chinese = i18n.current() in ("zh_CN", "zh_TW")
    name = APP_NAME if chinese else APP_NAME_EN
    lines = [
        f"{name} ({APP_NAME_EN}) v{__version__}" if chinese else f"{name} v{__version__}",
        f"{i18n.tr_text('系统')}: {platform.platform()}",
        f"Python: {sys.version.split()[0]}",
    ]
    try:
        from PySide6 import __version__ as pyside_version
        from PySide6.QtCore import qVersion

        lines.append(f"PySide6: {pyside_version} / Qt {qVersion()}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from . import fonts

        lines.append(f"{i18n.tr_text('字体')}: {fonts.describe()}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..core.authenticity import find_ffmpeg

        ffmpeg = find_ffmpeg()
        lines.append(f"{i18n.tr_text('解码器')}: {ffmpeg or i18n.tr_text('未找到')}")
    except Exception:  # noqa: BLE001
        pass
    try:
        import mutagen

        lines.append(f"mutagen: {mutagen.version_string}")
    except Exception:  # noqa: BLE001
        pass
    for label, path in (
        (i18n.tr_text("配置目录"), paths.data_dir()),
        (i18n.tr_text("日志目录"), paths.log_dir()),
    ):
        lines.append(f"{label}: {path}")
    screen = QGuiApplication.primaryScreen()
    if screen is not None:
        size = screen.size()
        ratio = screen.devicePixelRatio()
        lines.append(f"{i18n.tr_text('屏幕')}: {size.width()}x{size.height()} @{ratio:g}x")
    return "\n".join(lines)


class AboutDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(f"关于 {APP_NAME}")
        self.resize(760, 620)
        self.setMinimumSize(680, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        layout.addLayout(self._build_header())

        tabs = QTabWidget()
        tabs.addTab(self._build_about_tab(), "概览")
        tabs.addTab(self._build_license_tab(), "开源协议")
        tabs.addTab(self._build_third_party_tab(), "第三方组件")
        tabs.addTab(self._build_env_tab(), "运行环境")
        layout.addWidget(tabs, 1)

        # 不用 QDialogButtonBox：它会按平台习惯重排按钮，把 ActionRole 挪到最左，
        # 结果"关闭"跑到了最前面。这里直接排，顺序完全可控。
        row = QHBoxLayout()
        row.setSpacing(8)
        self.copy_btn = QPushButton("复制诊断信息")
        self.copy_btn.setObjectName("ghost")
        self.copy_btn.clicked.connect(self._copy_diagnostics)
        log_btn = QPushButton("打开日志目录")
        log_btn.setObjectName("ghost")
        log_btn.clicked.connect(self._open_log_dir)
        license_btn = QPushButton("查看 GPLv3 全文")
        license_btn.setObjectName("ghost")
        license_btn.clicked.connect(self._open_license_file)
        close_btn = QPushButton("关闭")
        close_btn.setObjectName("primary")
        close_btn.clicked.connect(self.accept)

        row.addWidget(self.copy_btn)
        row.addWidget(log_btn)
        row.addWidget(license_btn)
        row.addStretch(1)
        row.addWidget(close_btn)
        for button in (self.copy_btn, log_btn, license_btn, close_btn):
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(32)
        layout.addLayout(row)

    # ------------------------------------------------------------

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(16)

        pix = app_icon_pixmap(64)
        mark = QLabel()
        mark.setFixedSize(64, 64)
        if pix is not None:
            mark.setPixmap(pix)
        row.addWidget(mark, 0, Qt.AlignTop)

        column = QVBoxLayout()
        column.setSpacing(3)
        title = QLabel(APP_NAME)
        title.setStyleSheet(f"color:{theme.FG}; font-size:22px; font-weight:700;")
        column.addWidget(title)

        sub = QLabel(f"{APP_NAME_EN} · v{__version__}")
        sub.setStyleSheet(f"color:{theme.FG_MUTE}; font-size:12px; letter-spacing:0.4px;")
        column.addWidget(sub)

        tagline = QLabel("本地音乐元数据批量处理 · 真假无损鉴定 · 纯离线可用")
        tagline.setStyleSheet(f"color:{theme.FG_DIM}; font-size:12px;")
        column.addWidget(tagline)

        licence = QLabel(f"以 {LICENSE_NAME} 授权分发")
        licence.setStyleSheet(f"color:{theme.CYAN}; font-size:11px;")
        column.addWidget(licence)

        row.addLayout(column, 1)

        pill = QLabel("GPLv3")
        pill.setStyleSheet(
            f"color:{theme.OK}; background:rgba(52,211,153,0.12);"
            f"border:1px solid rgba(52,211,153,0.35); border-radius:9px;"
            "padding:3px 10px; font-size:11px; font-weight:700;"
        )
        row.addWidget(pill, 0, Qt.AlignTop)
        return row

    def _build_about_tab(self) -> QWidget:
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        browser.setHtml(
            i18n.tr_html(
                "about_intro",
                fg=theme.FG,
                mute=theme.FG_MUTE,
                disclaimer=i18n.tr_html("disclaimer"),
            )
        )
        return browser

    def _build_license_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(8)

        summary = QTextBrowser()
        summary.setOpenExternalLinks(True)
        summary.setMaximumHeight(190)
        summary.setHtml(
            i18n.tr_html(
                "license_summary",
                app=APP_NAME_EN,
                accent=theme.ACCENT,
                dim=theme.FG_MUTE,
            )
        )
        layout.addWidget(summary)

        label = QLabel("协议全文（随软件分发，离线可查）")
        label.setObjectName("hint")
        layout.addWidget(label)

        browser = QTextBrowser()
        browser.setObjectName("licenseView")
        browser.setFont(QFont("monospace"))
        browser.setPlainText(_license_text())
        layout.addWidget(browser, 1)
        return page

    def _build_third_party_tab(self) -> QWidget:
        browser = QTextBrowser()
        browser.setOpenExternalLinks(True)
        rows = "".join(
            f"<tr>"
            f"<td style='padding:7px 10px; color:{theme.FG}'>{name}</td>"
            f"<td style='padding:7px 10px; color:{theme.CYAN}'>{lic}</td>"
            f"<td style='padding:7px 10px; color:{theme.FG_DIM}'>{i18n.tr_purpose(purpose)}</td>"
            f"<td style='padding:7px 10px;'><a href='{url}' style='color:{theme.ACCENT}'>{url}</a></td>"
            f"</tr>"
            for name, lic, purpose, url in THIRD_PARTY
        )
        browser.setHtml(
            f"""
            <div style="color:{theme.FG_DIM}; font-size:12px">
            <p style="line-height:170%">{i18n.tr_html("third_party_intro")}</p>
            <table width="100%" cellspacing="0" style="font-size:12px">
            <tr style="color:{theme.FG_MUTE}; font-size:11px">
              <td style="padding:4px 10px">{i18n.tr_text("组件")}</td>
              <td style="padding:4px 10px">{i18n.tr_text("授权")}</td>
              <td style="padding:4px 10px">{i18n.tr_text("用途")}</td>
              <td style="padding:4px 10px">{i18n.tr_text("主页")}</td>
            </tr>
            {rows}
            </table>
            <p style="color:{theme.FG_MUTE}; margin-top:12px; line-height:170%">
            {i18n.tr_html("third_party_note")}</p>
            </div>
            """
        )
        return browser

    def _build_env_tab(self) -> QWidget:
        browser = QTextBrowser()
        browser.setObjectName("licenseView")
        browser.setPlainText(diagnostics())
        return browser

    # ------------------------------------------------------------

    def _copy_diagnostics(self) -> None:
        QGuiApplication.clipboard().setText(diagnostics())
        self.copy_btn.setText(i18n.tr_text("已复制 ✓"))

    def _open_log_dir(self) -> None:
        try:
            os.startfile(str(paths.log_dir()))  # type: ignore[attr-defined]
        except OSError:
            pass

    def _open_license_file(self) -> None:
        from ..core.paths import asset_path

        found = asset_path("LICENSE")
        if found is None:
            return
        try:
            os.startfile(str(found))  # type: ignore[attr-defined]
        except OSError:
            pass
