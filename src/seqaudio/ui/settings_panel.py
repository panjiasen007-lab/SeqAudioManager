"""参数设置面板：卡片式分组，新手一眼看懂，进阶用户全部可调。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
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
from . import theme
from .widgets import Card


class SettingsPanel(QScrollArea):
    settingsChanged = Signal()

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._loading = False
        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        self.setMinimumWidth(330)
        self.setMaximumWidth(420)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        body = QWidget()
        self.setWidget(body)
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(10)

        layout.addWidget(self._build_match_card())
        layout.addWidget(self._build_source_card())
        layout.addWidget(self._build_lyrics_card())
        layout.addWidget(self._build_write_card())
        layout.addWidget(self._build_watermark_card())
        layout.addWidget(self._build_rename_card())
        layout.addWidget(self._build_scan_card())
        layout.addStretch(1)

        self.load_from(settings)

    # ------------------------------------------------------------ 卡片构建

    def _build_match_card(self) -> Card:
        card = Card("匹配与覆盖", "命中后如何写入")
        self.conflict_combo = QComboBox()
        for mode in ConflictMode:
            self.conflict_combo.addItem(mode.value, mode)
        self.conflict_combo.currentIndexChanged.connect(self._notify)

        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(0.30, 0.95)
        self.threshold_spin.setSingleStep(0.05)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setToolTip(
            "低于该置信度的结果不会自动写入，转入「待人工确认」\n调低＝更激进，调高＝更严格"
        )
        self.threshold_spin.valueChanged.connect(self._notify)

        card.add_form([("已有标签", self.conflict_combo), ("置信度阈值", self.threshold_spin)])

        self.cue_check = QCheckBox("用 CUE 信息补全整轨标签")
        self.cue_check.toggled.connect(self._notify)
        card.add_widget(self.cue_check)
        return card

    def _build_source_card(self) -> Card:
        card = Card("联网数据源", "全部开源平台")

        self.online_check = QCheckBox("启用联网匹配与歌词下载")
        self.online_check.toggled.connect(self._notify)
        card.add_widget(self.online_check)

        self.mb_check = QCheckBox("MusicBrainz · 元数据主源")
        self.caa_check = QCheckBox("Cover Art Archive · 封面")
        self.lrclib_check = QCheckBox("LRCLIB · 歌词主源")
        self.local_lyrics_check = QCheckBox("优先使用本地已有歌词")
        for widget in (self.mb_check, self.caa_check, self.lrclib_check, self.local_lyrics_check):
            widget.toggled.connect(self._notify)
            card.add_widget(self._indent(widget))

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._dim_label("并发"))
        self.concurrency_spin = QSpinBox()
        self.concurrency_spin.setRange(1, 8)
        self.concurrency_spin.setToolTip("同时进行的检索数量，过高容易被数据源限流")
        self.concurrency_spin.valueChanged.connect(self._notify)
        row.addWidget(self.concurrency_spin, 1)

        row.addWidget(self._dim_label("超时"))
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(3.0, 60.0)
        self.timeout_spin.setSingleStep(1.0)
        self.timeout_spin.setSuffix(" s")
        self.timeout_spin.valueChanged.connect(self._notify)
        row.addWidget(self.timeout_spin, 1)
        card.add_layout(row)

        proxy_row = QHBoxLayout()
        proxy_row.setSpacing(8)
        proxy_row.addWidget(self._dim_label("代理"))
        self.proxy_edit = QLineEdit()
        self.proxy_edit.setPlaceholderText("留空使用系统代理")
        self.proxy_edit.editingFinished.connect(self._notify)
        proxy_row.addWidget(self.proxy_edit, 1)
        card.add_layout(proxy_row)
        return card

    def _build_lyrics_card(self) -> Card:
        card = Card("歌词", "LRC 同步歌词")
        self.lyrics_combo = QComboBox()
        for mode in LyricsMode:
            self.lyrics_combo.addItem(mode.value, mode)
        self.lyrics_combo.currentIndexChanged.connect(self._notify)
        card.add_form([("输出方式", self.lyrics_combo)])

        self.clean_lyrics_check = QCheckBox("自动剔除歌词内广告与水印")
        self.clean_lyrics_check.toggled.connect(self._notify)
        card.add_widget(self.clean_lyrics_check)
        return card

    def _build_write_card(self) -> Card:
        card = Card("写入与备份", "无损安全写入")

        self.backup_check = QCheckBox("写入前自动备份原文件")
        self.backup_check.toggled.connect(self._notify)
        card.add_widget(self.backup_check)

        backup_row = QHBoxLayout()
        backup_row.setSpacing(6)
        self.backup_dir_edit = QLineEdit()
        self.backup_dir_edit.setPlaceholderText("默认：程序目录下 backup")
        self.backup_dir_edit.editingFinished.connect(self._notify)
        browse = QPushButton("…")
        browse.setFixedSize(32, 30)
        browse.setObjectName("ghost")
        browse.setCursor(Qt.PointingHandCursor)
        browse.clicked.connect(self._pick_backup_dir)
        backup_row.addWidget(self.backup_dir_edit, 1)
        backup_row.addWidget(browse)
        card.add_layout(backup_row)

        self.compat_check = QCheckBox("兼容模式 · ID3v2.3 + UTF-16")
        self.compat_check.setToolTip("车载/老式播放器读不出标签时开启，Vorbis 系格式不受影响")
        self.compat_check.toggled.connect(self._notify)
        card.add_widget(self.compat_check)
        return card

    def _build_watermark_card(self) -> Card:
        card = Card("水印清理", "仅标签文字", theme.WARN)

        self.watermark_check = QCheckBox("清理站点广告与推广后缀")
        self.watermark_check.toggled.connect(self._notify)
        card.add_widget(self.watermark_check)

        self.clean_title_check = QCheckBox("同时清理歌名/专辑中的水印片段")
        self.clean_title_check.toggled.connect(self._notify)
        card.add_widget(self.clean_title_check)

        card.add_widget(self._dim_label("自定义屏蔽词（每行一个）"))
        self.words_edit = QPlainTextEdit()
        self.words_edit.setPlaceholderText("例如：某某音乐网\n更多资源请访问")
        self.words_edit.setFixedHeight(62)
        self.words_edit.textChanged.connect(self._notify)
        card.add_widget(self.words_edit)
        return card

    def _build_rename_card(self) -> Card:
        card = Card("批量重命名", "按模板", theme.OK)

        self.rename_check = QCheckBox("处理完成后自动重命名")
        self.rename_check.toggled.connect(self._notify)
        card.add_widget(self.rename_check)

        self.template_edit = QLineEdit()
        self.template_edit.setPlaceholderText(DEFAULT_TEMPLATE)
        self.template_edit.editingFinished.connect(self._notify)
        card.add_widget(self.template_edit)

        hint = QLabel(
            "变量：{歌手} {歌名} {专辑} {年份} {流派} {曲目} {碟号} {格式}\n"
            "补零 {曲目:02d}，用 / 建立子文件夹"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        card.add_widget(hint)
        return card

    def _build_scan_card(self) -> Card:
        card = Card("扫描与过滤", "", theme.CYAN)

        self.recursive_check = QCheckBox("递归扫描子文件夹")
        self.recursive_check.toggled.connect(self._notify)
        card.add_widget(self.recursive_check)

        self.min_duration_spin = QSpinBox()
        self.min_duration_spin.setRange(0, 3600)
        self.min_duration_spin.setSuffix(" 秒")
        self.min_duration_spin.setToolTip(
            "低于该时长的音频直接跳过，用于过滤短视频 BGM / 广告音频"
        )
        self.min_duration_spin.valueChanged.connect(self._notify)
        card.add_form([("最短时长", self.min_duration_spin)])
        return card

    # ------------------------------------------------------------ 辅助

    @staticmethod
    def _indent(widget: QWidget) -> QWidget:
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(6, 0, 0, 0)
        layout.addWidget(widget)
        return wrapper

    @staticmethod
    def _dim_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hint")
        return label

    def _notify(self, *_) -> None:
        if not self._loading:
            self.settingsChanged.emit()

    def _pick_backup_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择备份目录")
        if folder:
            self.backup_dir_edit.setText(folder)
            self._notify()

    # ------------------------------------------------------------ 数据同步

    def load_from(self, settings: Settings) -> None:
        self._loading = True
        try:
            index = self.conflict_combo.findData(settings.conflict())
            self.conflict_combo.setCurrentIndex(max(0, index))
            self.cue_check.setChecked(settings.fill_album_from_cue)
            self.threshold_spin.setValue(float(settings.match_threshold or 0.62))

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
        # ConflictMode / LyricsMode 是 str 枚举，经 Qt userData 往返后退化为字符串
        settings.conflict_mode = str(self.conflict_combo.currentData())
        settings.fill_album_from_cue = self.cue_check.isChecked()
        settings.match_threshold = float(self.threshold_spin.value())

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
