"""参数设置面板（需求 4.8：新手一键全自动，进阶用户开放全部参数）。"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Settings
from ..core.model import ConflictMode, LyricsMode
from ..core.rename import DEFAULT_TEMPLATE


class SettingsPanel(QScrollArea):
    settingsChanged = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setMinimumWidth(330)
        self.setMaximumWidth(420)

        body = QWidget()
        self.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        layout.addWidget(self._build_conflict_group())
        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_lyrics_group())
        layout.addWidget(self._build_write_group())
        layout.addWidget(self._build_watermark_group())
        layout.addWidget(self._build_rename_group())
        layout.addWidget(self._build_scan_group())
        layout.addStretch(1)

        self.load_from(settings)

    # ------------------------------------------------------------ 分组构建

    def _build_conflict_group(self) -> QGroupBox:
        box = QGroupBox("匹配与覆盖策略")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.conflict_combo = QComboBox()
        for mode in ConflictMode:
            self.conflict_combo.addItem(mode.value, mode)
        self.conflict_combo.currentIndexChanged.connect(self._notify)
        form.addRow("已有标签", self.conflict_combo)

        self.cue_check = QCheckBox("用 CUE 信息补全整轨标签")
        self.cue_check.toggled.connect(self._notify)
        form.addRow("", self.cue_check)
        return box

    def _build_source_group(self) -> QGroupBox:
        box = QGroupBox("联网数据源（开源平台）")
        outer = QVBoxLayout(box)
        outer.setSpacing(6)

        self.online_check = QCheckBox("启用联网匹配与歌词下载")
        self.online_check.toggled.connect(self._notify)
        outer.addWidget(self.online_check)

        self.mb_check = QCheckBox("MusicBrainz（元数据主源）")
        self.caa_check = QCheckBox("Cover Art Archive（封面）")
        self.lrclib_check = QCheckBox("LRCLIB（歌词主源）")
        self.local_lyrics_check = QCheckBox("优先使用本地已有歌词")
        for widget in (self.mb_check, self.caa_check, self.lrclib_check, self.local_lyrics_check):
            widget.setContentsMargins(14, 0, 0, 0)
            widget.toggled.connect(self._notify)
            outer.addWidget(widget)

        row = QHBoxLayout()
        row.addWidget(QLabel("并发"))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 8)
        self.concurrency_spin.setToolTip("同时进行的检索数量，过高容易被数据源限流")
        self.concurrency_spin.valueChanged.connect(self._notify)
        row.addWidget(self.concurrency_spin)
        row.addSpacing(10)
        row.addWidget(QLabel("超时(秒)"))
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(3.0, 60.0)
        self.timeout_spin.setSingleStep(1.0)
        self.timeout_spin.valueChanged.connect(self._notify)
        row.addWidget(self.timeout_spin)
        row.addStretch(1)
        outer.addLayout(row)

        proxy_row = QHBoxLayout()
        proxy_row.addWidget(QLabel("代理"))
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("如 http://127.0.0.1:7890，留空用系统设置")
        self.proxy_edit.editingFinished.connect(self._notify)
        proxy_row.addWidget(self.proxy_edit, 1)
        outer.addLayout(proxy_row)
        return box

    def _build_lyrics_group(self) -> QGroupBox:
        box = QGroupBox("歌词")
        form = QFormLayout(box)
        self.lyrics_combo = QComboBox()
        for mode in LyricsMode:
            self.lyrics_combo.addItem(mode.value, mode)
        self.lyrics_combo.currentIndexChanged.connect(self._notify)
        form.addRow("输出方式", self.lyrics_combo)

        self.clean_lyrics_check = QCheckBox("自动剔除歌词内广告与水印")
        self.clean_lyrics_check.toggled.connect(self._notify)
        form.addRow("", self.clean_lyrics_check)
        return box

    def _build_write_group(self) -> QGroupBox:
        box = QGroupBox("写入与备份")
        outer = QVBoxLayout(box)

        self.backup_check = QCheckBox("写入前自动备份原文件")
        self.backup_check.toggled.connect(self._notify)
        outer.addWidget(self.backup_check)

        backup_row = QHBoxLayout()
        self.backup_dir_edit = QLineEdit()
        self.backup_dir_edit.setPlaceholderText("默认：程序目录下 backup")
        self.backup_dir_edit.editingFinished.connect(self._notify)
        browse = QPushButton("…")
        browse.setFixedWidth(30)
        browse.clicked.connect(self._pick_backup_dir)
        backup_row.addWidget(self.backup_dir_edit, 1)
        backup_row.addWidget(browse)
        outer.addLayout(backup_row)

        self.compat_check = QCheckBox("兼容模式（ID3v2.3 + UTF-16，适配老播放器）")
        self.compat_check.setToolTip("车载/老式播放器读不出标签时开启，Vorbis 系格式不受影响")
        self.compat_check.toggled.connect(self._notify)
        outer.addWidget(self.compat_check)
        return box

    def _build_watermark_group(self) -> QGroupBox:
        box = QGroupBox("水印清理")
        outer = QVBoxLayout(box)

        self.watermark_check = QCheckBox("清理标签文字水印（站点广告、推广后缀）")
        self.watermark_check.toggled.connect(self._notify)
        outer.addWidget(self.watermark_check)

        self.clean_title_check = QCheckBox("同时清理歌名/专辑中的水印片段")
        self.clean_title_check.toggled.connect(self._notify)
        outer.addWidget(self.clean_title_check)

        outer.addWidget(QLabel("自定义屏蔽词（每行一个）"))
        self.words_edit = QPlainTextEdit()
        self.words_edit.setPlaceholderText("例如：某某音乐网\n更多资源请访问")
        self.words_edit.setFixedHeight(70)
        self.words_edit.textChanged.connect(self._notify)
        outer.addWidget(self.words_edit)
        return box

    def _build_rename_group(self) -> QGroupBox:
        box = QGroupBox("批量重命名")
        outer = QVBoxLayout(box)

        self.rename_check = QCheckBox("处理完成后按模板重命名")
        self.rename_check.toggled.connect(self._notify)
        outer.addWidget(self.rename_check)

        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText(DEFAULT_TEMPLATE)
        self.template_edit.editingFinished.connect(self._notify)
        outer.addWidget(self.template_edit)

        hint = QLabel(
            "可用变量：{歌手} {歌名} {专辑} {年份} {流派} {曲目} {碟号} {格式} {位深} {采样率}\n"
            "支持补零 {曲目:02d}，用 / 建立子文件夹"
        )
        hint.setStyleSheet("color:#8b909a; font-size:11px;")
        hint.setWordWrap(True)
        outer.addWidget(hint)
        return box

    def _build_scan_group(self) -> QGroupBox:
        box = QGroupBox("扫描与过滤")
        form = QFormLayout(box)

        self.recursive_check = QCheckBox("递归扫描子文件夹")
        self.recursive_check.toggled.connect(self._notify)
        form.addRow("", self.recursive_check)

        self.min_duration_spin = QSpinBox()
        self.min_duration_spin.setRange(0, 3600)
        self.min_duration_spin.setSuffix(" 秒")
        self.min_duration_spin.setToolTip("低于该时长的音频直接跳过，用于过滤短视频 BGM / 广告音频")
        self.min_duration_spin.valueChanged.connect(self._notify)
        form.addRow("最短时长", self.min_duration_spin)
        return box

    # ------------------------------------------------------------ 数据同步

    def _notify(self, *_) -> None:
        self.settingsChanged.emit()

    def _pick_backup_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择备份目录")
        if folder:
            self.backup_dir_edit.setText(folder)
            self._notify()

    def load_from(self, settings: Settings) -> None:
        self._loading = True
        try:
            index = self.conflict_combo.findData(settings.conflict())
            self.conflict_combo.setCurrentIndex(max(0, index))
            self.cue_check.setChecked(settings.fill_album_from_cue)

            self.online_check.setChecked(settings.online_enabled and not settings.offline_mode)
            self.mb_check.setChecked(settings.use_musicbrainz)
            self.caa_check.setChecked(settings.use_coverart)
            self.lrclib_check.setChecked(settings.use_lrclib)
            self.local_lyrics_check.setChecked(settings.use_local_lyrics)
            self.concurrency_spin.setValue(max(1, settings.concurrency))
            self.timeout_spin.setValue(settings.request_timeout)
            self.proxy_edit.setText(settings.http_proxy)

            idx = self.lyrics_combo.findData(settings.lyrics_mode_enum())
            self.lyrics_combo.setCurrentIndex(max(0, idx))
            self.clean_lyrics_check.setChecked(settings.clean_lyrics)

            self.backup_check.setChecked(settings.backup_enabled)
            self.backup_dir_edit.setText(settings.backup_root)
            self.compat_check.setChecked(settings.compat_mode)

            self.watermark_check.setChecked(settings.watermark_enabled)
            self.clean_title_check.setChecked(settings.clean_title_watermark)
            self.words_edit.setPlainText("\n".join(settings.custom_words))

            self.rename_check.setChecked(settings.rename_enabled)
            self.template_edit.setText(settings.rename_template)

            self.recursive_check.setChecked(settings.recursive_scan)
            self.min_duration_spin.setValue(int(settings.min_duration_sec))
        finally:
            self._loading = False

    def apply_to(self, settings: Settings) -> Settings:
        # ConflictMode / LyricsMode 是 str 枚举，经 Qt 往返后会退化成普通字符串，
        # 所以这里读 value 而不是枚举对象本身
        settings.conflict_mode = str(self.conflict_combo.currentData())
        settings.fill_album_from_cue = self.cue_check.isChecked()

        settings.online_enabled = self.online_check.isChecked()
        settings.offline_mode = not self.online_check.isChecked()
        settings.use_musicbrainz = self.mb_check.isChecked()
        settings.use_coverart = self.caa_check.isChecked()
        settings.use_lrclib = self.lrclib_check.isChecked()
        settings.use_local_lyrics = self.local_lyrics_check.isChecked()
        settings.concurrency = self.concurrency_spin.value()
        settings.request_timeout = float(self.timeout_spin.value())
        settings.http_proxy = self.proxy_edit.text().strip()

        settings.lyrics_mode = str(self.lyrics_combo.currentData())
        settings.clean_lyrics = self.clean_lyrics_check.isChecked()

        settings.backup_enabled = self.backup_check.isChecked()
        settings.backup_root = self.backup_dir_edit.text().strip()
        settings.compat_mode = self.compat_check.isChecked()

        settings.watermark_enabled = self.watermark_check.isChecked()
        settings.clean_title_watermark = self.clean_title_check.isChecked()
        settings.custom_words = [
            line.strip() for line in self.words_edit.toPlainText().splitlines() if line.strip()
        ]

        settings.rename_enabled = self.rename_check.isChecked()
        settings.rename_template = self.template_edit.text().strip() or DEFAULT_TEMPLATE

        settings.recursive_scan = self.recursive_check.isChecked()
        settings.min_duration_sec = float(self.min_duration_spin.value())
        return settings
