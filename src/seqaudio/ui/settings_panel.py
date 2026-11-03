"""参数设置面板：卡片式分组，新手一眼看懂，进阶用户全部可调。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Settings
from ..core.model import ConflictMode, LyricsMode
from ..core.rename import DEFAULT_TEMPLATE
from . import fonts, theme
from .widgets import Card


class SettingsPanel(QWidget):
    settingsChanged = Signal()   # 有未保存的修改（不再自动落盘，仅标记脏）
    saved = Signal()             # 「保存参数」成功后发出，主窗口据此刷新依赖设置的状态
    appearancePreviewChanged = Signal(str, str, float)  # 字体/字号预览（id, id, scale）

    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self._loading = False
        self._dirty = False
        self.setMinimumWidth(330)
        self.setMaximumWidth(420)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # 参数分页：匹配 / 歌词 / 写入 / 外观；保存条固定在底部
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        groups = (
            ("匹配", [self._build_match_card(), self._build_source_card()]),
            ("歌词", [self._build_lyrics_card()]),
            (
                "写入",
                [
                    self._build_write_card(),
                    self._build_watermark_card(),
                    self._build_rename_card(),
                    self._build_scan_card(),
                ],
            ),
            ("外观", [self._build_appearance_card(), self._build_ui_card()]),
        )
        for title, cards in groups:
            page = QWidget()
            page_layout = QVBoxLayout(page)
            page_layout.setContentsMargins(8, 8, 4, 8)
            page_layout.setSpacing(10)
            for card in cards:
                page_layout.addWidget(card)
            page_layout.addStretch(1)

            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, title)

        root.addWidget(self.tabs, 1)
        root.addWidget(self._build_save_bar())

        self.load_from(settings)
        self._set_dirty(False)

    # ------------------------------------------------------------ 卡片构建

    def _build_match_card(self) -> Card:
        card = Card("匹配与覆盖")
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
        card = Card("联网数据源")

        self.online_check = QCheckBox("启用联网匹配与歌词下载")
        self.online_check.toggled.connect(self._notify)
        card.add_widget(self.online_check)

        self.mb_check = QCheckBox("MusicBrainz · 元数据主源")
        self.caa_check = QCheckBox("Cover Art Archive · 封面")
        self.lrclib_check = QCheckBox("LRCLIB · 歌词主源")
        self.itunes_check = QCheckBox("iTunes Search · 元数据/封面（Apple 公开接口）")
        self.netmusic_check = QCheckBox("网易云 · 元数据/歌词/封面（兜底）")
        self.qqmusic_check = QCheckBox("QQ音乐 · 元数据/歌词/封面（兜底）")
        self.local_lyrics_check = QCheckBox("优先使用本地已有歌词")
        for widget in (self.mb_check, self.caa_check, self.lrclib_check,
                       self.itunes_check, self.netmusic_check, self.qqmusic_check,
                       self.local_lyrics_check):
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
        card = Card("歌词")
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
        card = Card("写入与备份")

        self.backup_check = QCheckBox("写入前自动备份原文件")
        self.backup_check.toggled.connect(self._notify)
        card.add_widget(self.backup_check)

        backup_row = QHBoxLayout()
        backup_row.setSpacing(6)
        self.backup_dir_edit = QLineEdit()
        self.backup_dir_edit.setPlaceholderText("默认：程序目录下 backup")
        self.backup_dir_edit.editingFinished.connect(self._notify)
        browse = QPushButton("…")
        # 只固定高度，宽度跟主题内边距走；32px 会把「…」压没
        browse.setFixedHeight(30)
        browse.setMinimumWidth(44)
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
        card = Card("水印清理", "", theme.WARN)

        self.watermark_check = QCheckBox("清理站点广告与推广后缀")
        self.watermark_check.toggled.connect(self._notify)
        card.add_widget(self.watermark_check)

        self.strip_number_check = QCheckBox("去除标题开头的曲目序号")
        self.strip_number_check.setToolTip("把「01. 七里香」清洗成「七里香」，序号保留在曲目号字段")
        self.strip_number_check.toggled.connect(self._notify)
        card.add_widget(self.strip_number_check)

        self.clean_title_check = QCheckBox("同时清理歌名/专辑中的水印片段")
        self.clean_title_check.toggled.connect(self._notify)
        card.add_widget(self.clean_title_check)

        self.clear_comment_check = QCheckBox("清空注释字段的全部内容")
        self.clear_comment_check.setToolTip(
            "注释（comment）通常是抓轨/下载工具塞进去的广告或推广，默认整段清空。\n"
            "若想保留自己写进注释的信息，取消此项。"
        )
        self.clear_comment_check.toggled.connect(self._notify)
        card.add_widget(self.clear_comment_check)

        self.unify_script_check = QCheckBox("同目录艺术家名字统一简体（简繁不混搭）")
        self.unify_script_check.setToolTip(
            "MusicBrainz 返回繁体、iTunes/本地多为简体，混写进标签就成了「一张专辑两种写法」。\n"
            "勾选后按目录多数投票，把繁体名字统一成简体；纯繁体曲库保持原样。"
        )
        self.unify_script_check.toggled.connect(self._notify)
        card.add_widget(self.unify_script_check)

        card.add_widget(self._dim_label("自定义屏蔽词（每行一个）"))
        self.words_edit = QPlainTextEdit()
        self.words_edit.setPlaceholderText("例如：某某音乐网\n更多资源请访问")
        self.words_edit.setFixedHeight(62)
        self.words_edit.textChanged.connect(self._notify)
        card.add_widget(self.words_edit)
        return card

    def _build_rename_card(self) -> Card:
        card = Card("批量重命名", "", theme.OK)

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

        self.trim_pregap_check = QCheckBox("分轨在下一轨 INDEX 00 处截止")
        self.trim_pregap_check.setToolTip(
            "勾选：每轨输出到下一轨预间隙起点（INDEX 00），去掉轨尾约 2 秒的静音预间隙；\n"
            "若预间隙里检测到真实音频（隐藏曲/尾奏），会自动保留，不会裁掉内容。\n"
            "取消：按 EAC/CUE 习惯，把预间隙归入前一轨（默认）"
        )
        self.trim_pregap_check.toggled.connect(self._notify)
        card.add_widget(self.trim_pregap_check)
        return card

    def _build_appearance_card(self) -> Card:
        card = Card("字体与外观", "随包开源字体", theme.CYAN)

        self.ui_font_combo = QComboBox()
        for index, choice in enumerate(fonts.UI_CHOICES):
            self.ui_font_combo.addItem(f"{choice.name} · {choice.category}", choice.id)
            tip = f"{choice.name}（{choice.license}）"
            if not choice.cjk:
                tip += "\n不含中文字形：中文自动回退到 Noto Sans SC"
            self.ui_font_combo.setItemData(index, tip, Qt.ToolTipRole)
        self.ui_font_combo.currentIndexChanged.connect(self._on_appearance_changed)

        self.mono_font_combo = QComboBox()
        for choice in fonts.MONO_CHOICES:
            self.mono_font_combo.addItem(choice.name, choice.id)
        self.mono_font_combo.currentIndexChanged.connect(self._on_appearance_changed)

        self.font_scale_combo = QComboBox()
        for label, value in (("90%", 0.9), ("100%", 1.0), ("110%", 1.1), ("125%", 1.25)):
            self.font_scale_combo.addItem(label, value)
        self.font_scale_combo.currentIndexChanged.connect(self._on_appearance_changed)

        card.add_form(
            [
                ("界面字体", self.ui_font_combo),
                ("数据字体", self.mono_font_combo),
                ("字号", self.font_scale_combo),
            ]
        )

        self.font_preview = QLabel("乐与怒 · Beyond · 海阔天空")
        self.font_preview.setObjectName("fontPreview")
        self.font_preview.setWordWrap(True)
        self.mono_preview = QLabel("01/12 · 96kHz / 24bit · 1.2 GB")
        self.mono_preview.setObjectName("monoPreview")
        self.mono_preview.setWordWrap(True)
        card.add_widget(self.font_preview)
        card.add_widget(self.mono_preview)
        return card

    def _appearance_values(self) -> tuple[str, str, float]:
        ui_id = self.ui_font_combo.currentData() or fonts.DEFAULT_UI
        mono_id = self.mono_font_combo.currentData() or fonts.DEFAULT_MONO
        try:
            scale = float(self.font_scale_combo.currentData() or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        return str(ui_id), str(mono_id), scale

    def _refresh_appearance_preview(self) -> None:
        ui_id, mono_id, scale = self._appearance_values()
        ui = next((c for c in fonts.UI_CHOICES if c.id == ui_id), fonts.UI_CHOICES[0])
        mono = next(
            (c for c in fonts.MONO_CHOICES if c.id == mono_id), fonts.MONO_CHOICES[0]
        )
        ui_font = QFont(ui.family)
        ui_font.setFamilies([ui.family, "Noto Sans SC", "Microsoft YaHei UI"])
        ui_font.setPixelSize(round(14 * scale))
        self.font_preview.setFont(ui_font)

        mono_font = QFont(mono.family)
        mono_font.setFamilies([mono.family, "Consolas"])
        mono_font.setPixelSize(round(12 * scale))
        self.mono_preview.setFont(mono_font)

    def _on_appearance_changed(self, *_) -> None:
        self._refresh_appearance_preview()
        if not self._loading:
            self.appearancePreviewChanged.emit(*self._appearance_values())
            self._notify()

    def _build_ui_card(self) -> Card:
        card = Card("界面与清理", "", theme.PURPLE)

        self.suspect_check = QCheckBox("清理时包含「疑似」项")
        self.suspect_check.setToolTip(
            "勾选：连「疑似假无损 / 疑似升频 / 疑似升位深」一起清理\n"
            "取消：只清理明确判定为假无损的文件（更保守）"
        )
        self.suspect_check.toggled.connect(self._notify)
        card.add_widget(self.suspect_check)

        self.cover_check = QCheckBox("每个文件夹生成封面")
        self.cover_check.setToolTip(
            "处理完成后，为每个文件夹写入一张 cover 图：\n"
            "专辑封面优先，匹配不到时用歌手照片兜底"
        )
        self.cover_check.toggled.connect(self._notify)
        card.add_widget(self.cover_check)

        hint = QLabel("判定只在本地做频谱分析，不会上传任何音频。")
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        card.add_widget(hint)
        return card

    def _build_save_bar(self) -> QWidget:
        """底部保存条：修改参数后必须点「保存」才生效，未保存时高亮提示。"""
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(8)

        self.dirty_label = QLabel("")
        self.dirty_label.setObjectName("warnHint")

        self.revert_btn = QPushButton("还原")
        self.revert_btn.setObjectName("ghost")
        self.revert_btn.setCursor(Qt.PointingHandCursor)
        self.revert_btn.setFixedHeight(32)
        self.revert_btn.setToolTip("放弃本次未保存的修改，恢复为已保存的参数")
        self.revert_btn.clicked.connect(self.revert)

        self.save_btn = QPushButton("保存参数")
        self.save_btn.setObjectName("primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setFixedHeight(32)
        self.save_btn.setToolTip("把当前参数写入并持久化（Ctrl+S）")
        self.save_btn.clicked.connect(self.save)

        layout.addWidget(self.dirty_label, 1)
        layout.addWidget(self.revert_btn)
        layout.addWidget(self.save_btn)
        return bar

    # ------------------------------------------------------------ 保存 / 还原

    def is_dirty(self) -> bool:
        return self._dirty

    def _set_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self.save_btn.setEnabled(dirty)
        self.revert_btn.setEnabled(dirty)
        self.dirty_label.setText("● 有未保存的修改" if dirty else "")
        self.save_btn.setStyleSheet("" if dirty else "opacity:0.55;")

    def save(self) -> None:
        """把面板当前值写入 settings 并持久化。"""
        self.apply_to(self.settings)
        try:
            self.settings.save()
        except OSError as exc:
            self.dirty_label.setText(f"保存失败：{exc}")
            self.dirty_label.setStyleSheet(f"color:{theme.BAD};")
            return
        self._set_dirty(False)
        self.saved.emit()

    def revert(self) -> None:
        """放弃未保存的修改，恢复为已保存的参数。"""
        self.load_from(self.settings)
        self._set_dirty(False)
        # 字体是即时预览的，还原时也要立刻退回已保存的字体
        self.appearancePreviewChanged.emit(*self._appearance_values())

    def mark_saved(self) -> None:
        """外部在 apply_to + save 之后调用，清除「未保存」标记。

        运行按钮（一键全自动等）会在启动前把面板参数写入并保存，
        这时面板的脏标记也应一并清除，避免「明明已保存却仍提示未保存」。
        """
        self._set_dirty(False)

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
            self._set_dirty(True)
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
            self.itunes_check.setChecked(settings.use_itunes)
            self.netmusic_check.setChecked(settings.use_netmusic)
            self.qqmusic_check.setChecked(settings.use_qqmusic)
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
            self.strip_number_check.setChecked(settings.strip_track_numbers)
            self.clean_title_check.setChecked(settings.clean_title_watermark)
            self.clear_comment_check.setChecked(settings.clear_comment)
            self.unify_script_check.setChecked(settings.unify_artist_script)
            self.words_edit.setPlainText("\n".join(settings.custom_words))

            self.rename_check.setChecked(settings.rename_enabled)
            self.template_edit.setText(settings.rename_template)

            self.recursive_check.setChecked(settings.recursive_scan)
            self.min_duration_spin.setValue(int(settings.min_duration_sec))
            self.trim_pregap_check.setChecked(settings.cue_split_trim_pregap)
            self.suspect_check.setChecked(settings.cleanup_include_suspect)
            self.cover_check.setChecked(settings.ensure_folder_cover)

            self.ui_font_combo.setCurrentIndex(
                max(0, self.ui_font_combo.findData(settings.ui_font))
            )
            self.mono_font_combo.setCurrentIndex(
                max(0, self.mono_font_combo.findData(settings.mono_font))
            )
            self.font_scale_combo.setCurrentIndex(
                max(0, self.font_scale_combo.findData(float(settings.font_scale or 1.0)))
            )
            self._refresh_appearance_preview()
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
        settings.use_itunes = self.itunes_check.isChecked()
        settings.use_netmusic = self.netmusic_check.isChecked()
        settings.use_qqmusic = self.qqmusic_check.isChecked()
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
        settings.strip_track_numbers = self.strip_number_check.isChecked()
        settings.clean_title_watermark = self.clean_title_check.isChecked()
        settings.clear_comment = self.clear_comment_check.isChecked()
        settings.unify_artist_script = self.unify_script_check.isChecked()
        settings.custom_words = [
            line.strip() for line in self.words_edit.toPlainText().splitlines() if line.strip()
        ]

        settings.rename_enabled = self.rename_check.isChecked()
        settings.rename_template = self.template_edit.text().strip() or DEFAULT_TEMPLATE

        settings.recursive_scan = self.recursive_check.isChecked()
        settings.min_duration_sec = float(self.min_duration_spin.value())
        settings.cue_split_trim_pregap = self.trim_pregap_check.isChecked()

        settings.cleanup_include_suspect = self.suspect_check.isChecked()
        settings.ensure_folder_cover = self.cover_check.isChecked()

        settings.ui_font, settings.mono_font, settings.font_scale = self._appearance_values()
        return settings
