"""参数设置面板：卡片式分组，新手一眼看懂，进阶用户全部可调。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import Settings
from ..player import devices as player_devices
from ..player.constants import OutputMode
from ..core.model import ConflictMode, LyricsMode
from ..core.rename import DEFAULT_TEMPLATE
from . import fonts, i18n, theme
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
        self.setMinimumWidth(380)
        self.setMaximumWidth(560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)

        # 参数分页：匹配 / 歌词 / 写入 / 外观 / 播放；保存条固定在底部
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
            (
                "播放",
                [
                    self._build_output_card(),
                    self._build_buffer_card(),
                    self._build_playback_card(),
                    self._build_library_card(),
                ],
            ),
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
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
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
        browse.setFixedHeight(34)
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

        self.split_format_combo = QComboBox()
        self.split_format_combo.addItem("FLAC（无损压缩，约原盘 60%）", "flac")
        self.split_format_combo.addItem("WAV（无压缩，体积与原盘相近）", "wav")
        self.split_format_combo.setToolTip(
            "分轨导出的文件格式。\n"
            "FLAC：无损压缩，体积约为原盘的 60%，主流播放器都认；\n"
            "WAV：无压缩 PCM，体积与原盘基本一致；位深跟随源文件\n"
            "（24bit 源出 24bit，不会降成 16bit）。"
        )
        self.split_format_combo.currentIndexChanged.connect(self._notify)
        card.add_form([("分轨格式", self.split_format_combo)])

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
                tip += i18n.tr_text("\n不含中文字形：中文自动回退到 Noto Sans SC")
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

    # ------------------------------------------------------------ 播放页（v1.6）

    def _build_output_card(self) -> Card:
        card = Card("输出设备", "输出后端与设备", theme.CYAN)

        self.device_combo = QComboBox()
        self.device_combo.setMinimumWidth(200)
        self.device_combo.setToolTip(
            "留空表示使用系统默认设备。列表来自 PortAudio，"
            "同一物理设备在不同后端下会出现多次"
        )
        self.device_combo.currentIndexChanged.connect(self._notify)

        self.backend_combo = QComboBox()
        # 后端键与 Settings 里存的值严格一致（不要写中文或枚举名）
        for key, label, hint in (
            ("wasapi", "WASAPI", "dev_hint_wasapi"),
            ("directsound", "DirectSound", "dev_hint_directsound"),
            ("mme", "MME", "dev_hint_mme"),
            ("wdmks", "WDM-KS", "dev_hint_wdmks"),
            ("asio", i18n.tr("dev_hint_asio"), "dev_hint_asio_missing"),
        ):
            self.backend_combo.addItem(i18n.tr_text(label), key)
            index = self.backend_combo.count() - 1
            self.backend_combo.setItemData(index, i18n.tr(hint), Qt.ToolTipRole)
            if key == "asio" and not player_devices.backend_present("asio"):
                # 当前 PortAudio 构建不含 ASIO：**置灰 + 说明**，不做 DLL 替换（Q5）
                model_item = self.backend_combo.model().item(index)
                if model_item is not None:
                    model_item.setEnabled(False)
        self.backend_combo.currentIndexChanged.connect(self._on_backend_changed)

        self.backend_hint = QLabel("")
        self.backend_hint.setObjectName("hint")
        self.backend_hint.setWordWrap(True)

        # ---- 输出模式：三选一（v1.7）----
        # 为什么不是一个复选框：独占有两档，严格档要求整数通路、DSP 全旁路，
        # 与"开了独占就能用"是两件事（见 docs/1.7-独占输出设计.md §4.1）。
        self.mode_group = QButtonGroup(self)
        self.mode_shared = QRadioButton(i18n.tr("dev_mode_shared"))
        self.mode_exclusive = QRadioButton(i18n.tr("dev_mode_exclusive"))
        self.mode_strict = QRadioButton(i18n.tr("dev_mode_exclusive_strict"))
        self.mode_strict.setToolTip(i18n.tr("dev_mode_strict_hint"))
        for index, button in enumerate(
            (self.mode_shared, self.mode_exclusive, self.mode_strict)
        ):
            self.mode_group.addButton(button, index)
            button.toggled.connect(self._on_output_mode_changed)
        self.mode_row = QWidget()
        mode_layout = QHBoxLayout(self.mode_row)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(12)
        for button in (self.mode_shared, self.mode_exclusive, self.mode_strict):
            mode_layout.addWidget(button)
        mode_layout.addStretch(1)

        # 兼容 v1.6 的旧用例/调用方：隐藏的布尔镜像，setChecked(True) == 选「独占」
        self.exclusive_check = QCheckBox("独占输出")
        self.exclusive_check.setVisible(False)
        self.exclusive_check.toggled.connect(self._on_exclusive_changed)

        self.exclusive_hint = QLabel(
            "独占会抢占其他应用的声音（会议软件、浏览器会突然没声）；"
            "停止或暂停即释放设备（可在下方关闭「暂停即释放」）。"
        )
        self.exclusive_hint.setObjectName("hint")
        self.exclusive_hint.setWordWrap(True)
        self.exclusive_hint.setVisible(False)

        self.fallback_combo = QComboBox()
        for key, label in (
            ("auto", "自动回退共享（信号路径留痕）"),
            ("ask", "每次询问"),
            ("never", "不回退，直接报错"),
        ):
            self.fallback_combo.addItem(i18n.tr_text(label), key)
        self.fallback_combo.setToolTip(
            "独占不可用时怎么办。auto 也会在信号路径与日志里写明降级原因，不静默。"
        )
        self.fallback_combo.currentIndexChanged.connect(self._notify)

        self.release_on_pause_check = QCheckBox("暂停时释放设备（推荐）")
        self.release_on_pause_check.setToolTip(
            "关闭：暂停仍占着设备（恢复更快，约 +100 ms 差异）；"
            "开启：暂停即让出设备，浏览器/会议软件马上能出声"
        )
        self.release_on_pause_check.toggled.connect(self._notify)

        # ---- 设备能力矩阵（零出声探测）----
        self.capability_box = QWidget()
        capability_layout = QVBoxLayout(self.capability_box)
        capability_layout.setContentsMargins(0, 6, 0, 0)
        capability_layout.setSpacing(6)
        self.capability_title = QLabel(i18n.tr("dev_cap_title"))
        self.capability_title.setObjectName("hint")
        capability_layout.addWidget(self.capability_title)
        self.capability_table = QTableWidget(0, 4)
        self.capability_table.setHorizontalHeaderLabels(
            [
                i18n.tr("dev_cap_rate"),
                i18n.tr("dev_cap_shared"),
                i18n.tr("dev_cap_exclusive"),
                i18n.tr("dev_cap_strict"),
            ]
        )
        self.capability_table.verticalHeader().setVisible(False)
        self.capability_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.capability_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.capability_table.setMaximumHeight(150)
        self.capability_table.horizontalHeader().setStretchLastSection(True)
        capability_layout.addWidget(self.capability_table)
        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        self.probe_button = QPushButton(i18n.tr("dev_probe_again"))
        self.probe_button.setObjectName("ghost")
        self.probe_button.clicked.connect(self._refresh_capability)
        self.test_button = QPushButton(i18n.tr("dev_test_device"))
        self.test_button.setObjectName("ghost")
        self.test_button.clicked.connect(self._test_device)
        button_row.addWidget(self.probe_button)
        button_row.addWidget(self.test_button)
        button_row.addStretch(1)
        capability_layout.addLayout(button_row)
        self.capability_hint = QLabel("")
        self.capability_hint.setObjectName("hint")
        self.capability_hint.setWordWrap(True)
        capability_layout.addWidget(self.capability_hint)
        self.capability_box.setVisible(False)

        self.follow_rate_check = QCheckBox("采样率跟随源文件")
        self.follow_rate_check.setToolTip(
            "开启：按源文件采样率开流，避免重采样；设备不支持时自动回退并在信号路径标注"
        )
        self.follow_rate_check.toggled.connect(self._notify)

        card.add_form(
            [
                ("输出后端", self.backend_combo),
                ("输出设备", self.device_combo),
            ]
        )
        card.add_widget(self.backend_hint)
        card.add_widget(self.mode_row)
        card.add_widget(self.exclusive_check)
        card.add_widget(self.exclusive_hint)
        card.add_widget(self.fallback_combo)
        card.add_widget(self.release_on_pause_check)
        card.add_widget(self.capability_box)
        card.add_widget(self.follow_rate_check)
        return card

    def _build_buffer_card(self) -> Card:
        card = Card("缓冲与解码", "延迟与 CPU 的折中", theme.PURPLE)

        self.buffer_spin = QSpinBox()
        self.buffer_spin.setRange(10, 500)
        self.buffer_spin.setSingleStep(10)
        self.buffer_spin.setSuffix(" ms")
        self.buffer_spin.setToolTip(
            "缓冲越深越稳（不容易爆音），但拖动进度条与按钮响应会变钝；"
            "官方建议 40 ms 起，机械硬盘或网络盘建议 80 ms 以上"
        )
        self.buffer_spin.valueChanged.connect(self._notify)

        self.dsd_rate_combo = QComboBox()
        for rate in (88200, 176400, 352800):
            self.dsd_rate_combo.addItem(f"{rate / 1000:g} kHz", rate)
        self.dsd_rate_combo.setToolTip(
            "DSD 只做 DSD→PCM 转换，不升频到 DSD。176.4 kHz 是默认值（DSD64/128 倍频）"
        )
        self.dsd_rate_combo.currentIndexChanged.connect(self._notify)

        self.int_pipeline_check = QCheckBox("优先整数通路")
        self.int_pipeline_check.setToolTip(
            "音量旁路且无 DSP 时用整数样本直通；开启后 DSP 与软件音量不可用"
        )
        self.int_pipeline_check.toggled.connect(self._notify)
        self.int_pipeline_hint = QLabel(
            "开启后 DSP 与软件音量不可用（信号路径会标注「整数通路不可用」）。"
        )
        self.int_pipeline_hint.setObjectName("hint")
        self.int_pipeline_hint.setWordWrap(True)

        card.add_form(
            [
                ("缓冲大小", self.buffer_spin),
                ("DSD→PCM 采样率", self.dsd_rate_combo),
            ]
        )
        card.add_widget(self.int_pipeline_check)
        card.add_widget(self.int_pipeline_hint)
        return card

    def _build_playback_card(self) -> Card:
        card = Card("播放行为", "启动与恢复", theme.OK)

        self.restore_session_check = QCheckBox("启动时恢复上次的队列与位置")
        self.restore_session_check.setToolTip(
            "只恢复队列与位置，不会自动播放；关掉后每次启动都是空队列"
        )
        self.restore_session_check.toggled.connect(self._notify)
        card.add_widget(self.restore_session_check)

        self.auto_lyrics_check = QCheckBox("播放时自动匹配歌词（网易云 / QQ / LRCLIB）")
        self.auto_lyrics_check.setToolTip(
            "换歌后自动联网抓取歌词（单次文本请求，不上传音频、不写盘）；"
            "关掉后仍可在歌词栏右键手动匹配"
        )
        self.auto_lyrics_check.toggled.connect(self._notify)
        card.add_widget(self.auto_lyrics_check)

        hint = QLabel(
            "播放相关设置会立即生效，不需要重启；参数面板底部的「保存参数」负责落盘。"
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        card.add_widget(hint)
        return card

    def _build_library_card(self) -> Card:
        card = Card("曲库", "启动时自动载入", theme.ACCENT)

        self.autoload_check = QCheckBox("启动时自动载入上次的曲库")
        self.autoload_check.toggled.connect(self._notify)
        card.add_widget(self.autoload_check)

        self.recent_sort_check = QCheckBox("启用「最近播放」排序")
        self.recent_sort_check.toggled.connect(self._notify)
        card.add_widget(self.recent_sort_check)
        return card

    def set_output_devices(self, devices) -> None:
        """刷新输出设备下拉（设备枚举在 ``PlayerBridge`` 里，本面板不 import player）。"""
        current = self.device_combo.currentData()
        if current is None:
            current = getattr(self.settings, "playback_device", "") or ""
        self._loading = True
        try:
            self.device_combo.clear()
            self.device_combo.addItem(i18n.tr_text("系统默认设备"), "")
            for entry in devices or []:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name") or "")
                if not name:
                    continue
                label = str(entry.get("label") or name)
                self.device_combo.addItem(label, name)
            index = self.device_combo.findData(current)
            self.device_combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            self._loading = False

    def _current_backend_key(self) -> str:
        return str(self.backend_combo.currentData() or "mme")

    def _on_backend_changed(self, _index: int) -> None:
        key = self._current_backend_key()
        self.backend_hint.setText(i18n.tr(f"dev_hint_{key}"))
        if not self._loading:
            mode = self._current_output_mode()
            # 非 WASAPI 后端：独占/严格置灰，并退回共享
            self._apply_mode_constraints(mode)
            if key != "wasapi" and mode.is_exclusive:
                self._set_output_mode(OutputMode.SHARED)
                self.capability_box.setVisible(False)
            elif self.capability_box.isVisible():
                self._refresh_capability()
        self._notify()

    # ------------------------------------------------------------ 输出模式（v1.7）

    def _current_output_mode(self) -> OutputMode:
        """面板上的三选一 → ``OutputMode``。"""
        if self.mode_strict.isChecked():
            return OutputMode.EXCLUSIVE_STRICT
        if self.mode_exclusive.isChecked():
            return OutputMode.EXCLUSIVE
        return OutputMode.SHARED

    def _set_output_mode(self, mode: OutputMode) -> None:
        """设置三选一（不触发探测，避免 load_from 期间反复扫设备）。"""
        button = {
            OutputMode.SHARED: self.mode_shared,
            OutputMode.EXCLUSIVE: self.mode_exclusive,
            OutputMode.EXCLUSIVE_STRICT: self.mode_strict,
        }[mode]
        if not button.isChecked():
            button.setChecked(True)

    def _on_exclusive_changed(self, checked: bool) -> None:
        """v1.6 布尔镜像的槽：``True`` 等价于选「宽松独占」。"""
        if self._loading:
            return
        if checked and self._current_output_mode() is OutputMode.SHARED:
            self._set_output_mode(OutputMode.EXCLUSIVE)

    def _on_output_mode_changed(self, _checked: bool = False) -> None:
        """模式切换：联动严格模式的强制项、能力矩阵与提示。"""
        if self._loading:
            return
        mode = self._current_output_mode()
        self.exclusive_hint.setVisible(mode.is_exclusive)
        self.exclusive_check.blockSignals(True)
        self.exclusive_check.setChecked(mode.is_exclusive)
        self.exclusive_check.blockSignals(False)
        self._apply_mode_constraints(mode)
        self.capability_box.setVisible(mode.is_exclusive)
        if mode.is_exclusive:
            self._refresh_capability()
        self._notify()

    def _apply_mode_constraints(self, mode: OutputMode) -> None:
        """严格模式的强制项：整数通路必勾且锁定；后端不支持时置灰独占。"""
        backend = self._current_backend_key()
        wasapi = backend == "wasapi"
        for button in (self.mode_exclusive, self.mode_strict):
            button.setEnabled(wasapi)
        if not wasapi:
            self.capability_hint.setText(i18n.tr("dev_hint_exclusive_wasapi_only"))
        # 严格：要求整数通路（float32 会被 explicit_sample_format 拒绝）
        if mode.explicit_format:
            self.int_pipeline_check.blockSignals(True)
            self.int_pipeline_check.setChecked(True)
            self.int_pipeline_check.blockSignals(False)
            self.int_pipeline_check.setEnabled(False)
            self.int_pipeline_check.setToolTip(i18n.tr("dev_mode_strict_int_hint"))
        else:
            self.int_pipeline_check.setEnabled(True)
            self.int_pipeline_check.setToolTip(
                "整数通路只在「音量旁路 + 无 EQ」时生效；DSP 一开就自动回浮点"
            )

    # ------------------------------------------------------------ 能力矩阵

    def _capability_device(self):
        """面板当前选中的设备（名字 → DeviceInfo；空 = 后端默认）。"""
        try:
            return player_devices.resolve_device(
                str(self.device_combo.currentData() or ""), self._current_backend_key()
            )
        except Exception:  # noqa: BLE001 - 没有设备时不该让设置页打不开
            return None

    def _refresh_capability(self) -> None:
        """零出声探测并渲染能力矩阵（设计 §9.1）。"""
        device = self._capability_device()
        if device is None:
            self.capability_hint.setText(i18n.tr("dev_no_output_device"))
            return
        try:
            capability = player_devices.probe_cache().get(device, refresh=True)
        except Exception as exc:  # noqa: BLE001
            self.capability_hint.setText(str(exc))
            return
        rates = (44100, 48000, 96000, 192000)
        self.capability_table.setRowCount(len(rates))
        unlocked: list[int] = []
        for row, rate in enumerate(rates):
            self.capability_table.setItem(
                row, 0, QTableWidgetItem(f"{rate / 1000:g} kHz")
            )
            for column, mode in (
                (1, OutputMode.SHARED),
                (2, OutputMode.EXCLUSIVE),
                (3, OutputMode.EXCLUSIVE_STRICT),
            ):
                ok = capability.supports(rate, mode)
                item = QTableWidgetItem("✓" if ok else "✗")
                item.setTextAlignment(Qt.AlignCenter)
                self.capability_table.setItem(row, column, item)
            if capability.supports(rate, OutputMode.EXCLUSIVE) and not capability.supports(
                rate, OutputMode.SHARED
            ):
                unlocked.append(rate)
        # Q2：把「独占解锁的高采样率」讲出来，但只能限定为本设备实测
        if unlocked:
            rate_text = "/".join(f"{rate / 1000:g}k" for rate in unlocked)
            self.capability_hint.setText(
                i18n.tr_format("dev_cap_unlocked", rates=rate_text)
            )
        else:
            self.capability_hint.setText(i18n.tr("dev_cap_none_unlocked"))

    def _show_cached_capability(self) -> None:
        """有缓存就渲染，没有就提示去点「重新探测」——load 阶段不扫设备。"""
        device = self._capability_device()
        if device is None:
            self.capability_hint.setText(i18n.tr("dev_no_output_device"))
            return
        cache = player_devices.probe_cache()
        if not cache.has(device):
            self.capability_hint.setText(i18n.tr("dev_cap_not_probed"))
            self.capability_table.setRowCount(0)
            return
        capability = cache.get(device)
        rates = (44100, 48000, 96000, 192000)
        self.capability_table.setRowCount(len(rates))
        for row, rate in enumerate(rates):
            self.capability_table.setItem(row, 0, QTableWidgetItem(f"{rate / 1000:g} kHz"))
            for column, mode in (
                (1, OutputMode.SHARED),
                (2, OutputMode.EXCLUSIVE),
                (3, OutputMode.EXCLUSIVE_STRICT),
            ):
                item = QTableWidgetItem("✓" if capability.supports(rate, mode) else "✗")
                item.setTextAlignment(Qt.AlignCenter)
                self.capability_table.setItem(row, column, item)

    def _test_device(self) -> None:
        """真开一条 1 s 静音流（唯一允许的真开流探测，用户显式点击才走）。"""
        device = self._capability_device()
        mode = self._current_output_mode()
        if device is None:
            self.capability_hint.setText(i18n.tr("dev_no_output_device"))
            return
        self.capability_hint.setText(i18n.tr("dev_testing"))
        QApplication.processEvents()  # 让提示先画出来，别让用户以为卡死
        ok, reason, actual = player_devices.backend_available(
            self._current_backend_key(), mode=mode, device=device
        )
        if ok:
            self.capability_hint.setText(
                i18n.tr_format("dev_test_ok", rate=actual, mode=mode.value)
            )
        else:
            self.capability_hint.setText(
                i18n.tr_format("dev_test_failed", reason=reason)
            )

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
        self.revert_btn.setFixedHeight(34)
        self.revert_btn.setToolTip("放弃本次未保存的修改，恢复为已保存的参数")
        self.revert_btn.clicked.connect(self.revert)

        self.save_btn = QPushButton("保存参数")
        self.save_btn.setObjectName("primary")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.setFixedHeight(34)
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
        self.dirty_label.setText(
            i18n.tr_text("● 有未保存的修改") if dirty else ""
        )
        self.save_btn.setStyleSheet("" if dirty else "opacity:0.55;")

    def save(self) -> None:
        """把面板当前值写入 settings 并持久化。"""
        self.apply_to(self.settings)
        try:
            self.settings.save()
        except OSError as exc:
            self.dirty_label.setText(
                i18n.tr_format("保存失败：{message}", message=str(exc))
            )
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
            self.split_format_combo.setCurrentIndex(
                max(0, self.split_format_combo.findData(settings.cue_split_format or "flac"))
            )
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

            # ---- 播放页 ----
            backend = str(getattr(settings, "playback_output_backend", "mme") or "mme")
            self.backend_combo.setCurrentIndex(
                max(0, self.backend_combo.findData(backend))
            )
            self.backend_hint.setText(i18n.tr(f"dev_hint_{self._current_backend_key()}"))
            device = str(getattr(settings, "playback_device", "") or "")
            index = self.device_combo.findData(device)
            self.device_combo.setCurrentIndex(index if index >= 0 else 0)
            # 三值模式：以 playback_output_mode 为准，旧布尔只作镜像兜底
            mode = OutputMode(
                str(getattr(settings, "playback_output_mode", "shared") or "shared")
            ) if str(getattr(settings, "playback_output_mode", "shared")) in {
                item.value for item in OutputMode
            } else OutputMode.SHARED
            if mode is OutputMode.SHARED and bool(
                getattr(settings, "playback_exclusive", False)
            ):
                mode = OutputMode.EXCLUSIVE
            self._set_output_mode(mode)
            self.exclusive_check.blockSignals(True)
            self.exclusive_check.setChecked(mode.is_exclusive)
            self.exclusive_check.blockSignals(False)
            self.exclusive_hint.setVisible(mode.is_exclusive)
            fallback = str(getattr(settings, "playback_exclusive_fallback", "auto") or "auto")
            index = self.fallback_combo.findData(fallback)
            self.fallback_combo.setCurrentIndex(index if index >= 0 else 0)
            self.release_on_pause_check.setChecked(
                bool(getattr(settings, "playback_release_on_pause", True))
            )
            self._apply_mode_constraints(mode)
            self.capability_box.setVisible(mode.is_exclusive)
            self._show_cached_capability()
            self.auto_lyrics_check.setChecked(
                bool(getattr(settings, "playback_auto_lyrics", True))
            )
            self.follow_rate_check.setChecked(
                bool(getattr(settings, "playback_follow_source_rate", True))
            )
            self.buffer_spin.setValue(
                max(
                    self.buffer_spin.minimum(),
                    min(
                        self.buffer_spin.maximum(),
                        int(getattr(settings, "playback_buffer_ms", 80) or 80),
                    ),
                )
            )
            self.dsd_rate_combo.setCurrentIndex(
                max(
                    0,
                    self.dsd_rate_combo.findData(
                        int(getattr(settings, "playback_dsd_pcm_rate", 176400) or 176400)
                    ),
                )
            )
            self.int_pipeline_check.setChecked(
                bool(getattr(settings, "playback_int_pipeline", False))
            )
            self.restore_session_check.setChecked(
                bool(getattr(settings, "playback_restore_session", True))
            )
            self.autoload_check.setChecked(bool(getattr(settings, "library_autoload", True)))
            self.recent_sort_check.setChecked(
                bool(getattr(settings, "library_recent_sort", True))
            )
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
        settings.cue_split_format = str(self.split_format_combo.currentData() or "flac")
        settings.cue_split_trim_pregap = self.trim_pregap_check.isChecked()

        settings.cleanup_include_suspect = self.suspect_check.isChecked()
        settings.ensure_folder_cover = self.cover_check.isChecked()

        settings.ui_font, settings.mono_font, settings.font_scale = self._appearance_values()

        # ---- 播放页（字段名必须与 core.config.Settings 的 playback_* 严格对应）----
        settings.playback_output_backend = self._current_backend_key()
        settings.playback_device = str(self.device_combo.currentData() or "")
        mode = self._current_output_mode()
        settings.playback_output_mode = mode.value
        # 布尔镜像：面板永远同时写两个字段，避免「设了模式但旧字段还在说独占」
        settings.playback_exclusive = mode.is_exclusive
        settings.playback_exclusive_fallback = str(
            self.fallback_combo.currentData() or "auto"
        )
        settings.playback_release_on_pause = self.release_on_pause_check.isChecked()
        settings.playback_strict_require_int = True
        settings.playback_auto_lyrics = self.auto_lyrics_check.isChecked()
        settings.playback_follow_source_rate = self.follow_rate_check.isChecked()
        settings.playback_buffer_ms = int(self.buffer_spin.value())
        settings.playback_dsd_pcm_rate = int(
            self.dsd_rate_combo.currentData() or 176400
        )
        settings.playback_int_pipeline = self.int_pipeline_check.isChecked()
        settings.playback_restore_session = self.restore_session_check.isChecked()

        # ---- 曲库 ----
        settings.library_autoload = self.autoload_check.isChecked()
        settings.library_recent_sort = self.recent_sort_check.isChecked()
        return settings
