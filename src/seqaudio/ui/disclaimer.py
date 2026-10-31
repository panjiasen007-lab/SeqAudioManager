"""启动免责声明（需求 1.4 / 5.1：必须勾选确认才能进入）。"""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from .widgets import LogoMark
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

NOTICE = """
<h3>使用前须知</h3>
<p><b>1. 版权声明</b><br>
本软件仅用于整理<b>您本人合法拥有或已获得授权</b>的音频文件。请勿用于盗版资源抓取、
分发或任何商业侵权行为。因使用不当产生的法律责任由使用者自行承担。</p>

<p><b>2. 数据来源</b><br>
歌曲信息与封面来自 MusicBrainz、Cover Art Archive、LRCLIB 等开源平台，
遵循其各自的授权协议；歌词版权归原词曲权利人所有，本软件<b>不存储、不分发</b>歌词内容，
仅在您的本机写入您自己的音频文件。</p>

<p><b>3. 隐私承诺</b><br>
本软件<b>绝不上传您的音频文件本体</b>，不捆绑任何广告与推广。
联网时仅发送用于检索的文本关键词（歌名/歌手/专辑/时长）。</p>

<p><b>4. 写入安全</b><br>
标签写入只修改标签区块，<b>不重新编码音频</b>；默认在写入前备份原文件，
写入失败自动回滚。仍建议您对重要曲库先自行留一份副本。</p>

<p><b>5. 功能边界</b><br>
不支持 ncm / qmc / kgm 等加密格式的解析与解密（会直接跳过）；
无法去除音频流内的人声水印，只能清理标签文字水印与封面图水印。</p>

<p style="color:#8b909a">继续使用即表示您已阅读并同意以上条款。</p>
"""


class DisclaimerDialog(QDialog):
    """首次启动的合规确认弹窗。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("音序管家 — 使用须知")
        self.setModal(True)
        self.resize(620, 520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)

        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(LogoMark(30))
        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        title = QLabel("音序管家")
        title.setObjectName("brandTitle")
        subtitle = QLabel("首次使用需确认以下条款")
        subtitle.setObjectName("brandSub")
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        head.addLayout(text_col)
        head.addStretch(1)
        layout.addLayout(head)

        browser = QTextBrowser()
        browser.setHtml(NOTICE)
        browser.setOpenExternalLinks(True)
        layout.addWidget(browser, 1)

        self.checkbox = QCheckBox("我已阅读并同意上述条款，且仅用于整理本人合法拥有的音频文件")
        layout.addWidget(self.checkbox)

        row = QHBoxLayout()
        row.addStretch(1)
        self.quit_btn = QPushButton("退出")
        self.quit_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("开始使用")
        self.ok_btn.setObjectName("primary")
        self.ok_btn.setEnabled(False)
        self.ok_btn.clicked.connect(self.accept)
        self.checkbox.toggled.connect(self.ok_btn.setEnabled)
        row.addWidget(self.quit_btn)
        row.addWidget(self.ok_btn)
        layout.addLayout(row)

    @staticmethod
    def acceptance_stamp() -> str:
        return f"v1.0@{datetime.now():%Y-%m-%d %H:%M}"

    @staticmethod
    def is_accepted(stamp: str) -> bool:
        return bool(stamp and stamp.startswith("v1.0"))
