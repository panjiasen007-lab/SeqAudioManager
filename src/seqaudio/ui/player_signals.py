"""信号路径面板：9 级固定链 + 4 个计数器。

三条硬约束（架构文档 3.3 / 10.4）
---------------------------------
1. **只读**：固定链的顺序由引擎定义，面板不许拖拽、不许增删级 —— 用户看到
   的必须是真实链路，而不是可编辑的示意图。
2. **状态必须同时有色和字**：色觉差异用户与灰度主题下也能读（TC-GUI-17）。
   ``CHANGED`` 黄、``BYPASSED`` 灰、``PASSTHROUGH`` 绿、``FALLBACK``/``ERROR``
   红并换成 ``signalChanged`` 的 objectName，方便测试直接断言。
3. **措辞铁律 R2**：全直通只能写「未做软件改动」，正文里不得出现
   ``bit-perfect`` / ``逐样本一致`` 这类承诺（引擎侧 ``assert_wording``
   已经过滤过一次，这里再兜一层）。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from . import i18n, theme
from .widgets import SectionHeader

log = logging.getLogger(__name__)

#: 无快照时的占位顺序（真值来自引擎 ``STAGE_KEYS``，由 ``main_window`` 注入）
FALLBACK_STAGE_KEYS: tuple[str, ...] = (
    "source",
    "decode",
    "format",
    "gain",
    "eq",
    "fade",
    "resample",
    "output",
    "device",
)

#: 状态 -> (文案 key, 颜色, objectName)
STATUS_STYLE: dict[str, tuple[str, str, str]] = {
    "PASSTHROUGH": ("原样通过", theme.OK, "signalRow"),
    "CHANGED": ("已改变", theme.WARN, "signalChanged"),
    "BYPASSED": ("已旁路", theme.FG_MUTE, "signalRow"),
    "FALLBACK": ("已回退", theme.BAD, "signalChanged"),
    "ERROR": ("错误", theme.BAD, "signalChanged"),
}

#: R2 措辞铁律：这些说法一律不许出现在面板里
FORBIDDEN_TEXT = ("bit-perfect", "bit perfect", "bitperfect", "逐样本一致", "位完美已验证")


def _sanitize(text: str) -> str:
    """拦掉措辞铁律禁止的说法（引擎已过滤，界面再兜一层）。"""
    cleaned = str(text or "")
    for phrase in FORBIDDEN_TEXT:
        if phrase in cleaned.lower() or phrase in cleaned:
            cleaned = cleaned.replace(phrase, "—")
    return cleaned


class _SignalRow(QWidget):
    """一行：级名 + 状态徽标 + 说明。"""

    def __init__(self, key: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("signalRow")
        self.key = key
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 3, 2, 3)
        layout.setSpacing(8)

        self.name_label = QLabel(i18n.tr_text(key))
        name_font = QFont(self.name_label.font())
        name_font.setBold(True)
        self.name_label.setFont(name_font)
        self.name_label.setMinimumWidth(64)
        layout.addWidget(self.name_label, 0)

        self.badge = QLabel("")
        self.badge.setAlignment(Qt.AlignCenter)
        self.badge.setMinimumWidth(56)
        layout.addWidget(self.badge, 0)

        self.detail_label = QLabel("—")
        self.detail_label.setObjectName("hint")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label, 1)

    def update_stage(self, stage: dict[str, Any]) -> None:
        """按一级快照刷新（缺字段一律降级成占位，不抛异常）。"""
        key = str(stage.get("key") or self.key)
        self.key = key
        i18n_key = str(stage.get("i18n_key") or f"sig_stage_{key}")
        label = i18n.tr(i18n_key)
        if not label or label == i18n_key:
            label = str(stage.get("label") or key)
        self.name_label.setText(label)

        status = str(stage.get("status") or "PASSTHROUGH").upper()
        text_key, color, object_name = STATUS_STYLE.get(
            status, ("原样通过", theme.FG_MUTE, "signalRow")
        )
        self.badge.setText(i18n.tr_text(text_key))
        self.badge.setStyleSheet(
            f"color:{color}; border:1px solid {color}; border-radius:8px;"
            f" padding:1px 6px; font-size:11px;"
        )
        self.detail_label.setText(_sanitize(str(stage.get("detail") or "—")))
        if object_name != self.objectName():
            self.setObjectName(object_name)
            self.style().unpolish(self)
            self.style().polish(self)


class PlayerSignalsPanel(QWidget):
    """「信号路径」面板（默认折叠，随播放条开关展开）。"""

    def __init__(
        self,
        stage_keys: Optional[tuple[str, ...]] = None,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("signalPathPanel")
        keys = tuple(stage_keys or FALLBACK_STAGE_KEYS)
        self._rows: dict[str, _SignalRow] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(6)

        self.summary_label = QLabel(i18n.tr_text("未做软件改动"))
        summary_font = QFont(self.summary_label.font())
        summary_font.setBold(True)
        self.summary_label.setFont(summary_font)
        self.summary_label.setWordWrap(True)
        root.addWidget(
            SectionHeader(
                i18n.tr_text("信号路径"), i18n.tr_text("只读：显示真实处理链，不可拖拽")
            )
        )
        root.addWidget(self.summary_label)

        grid = QVBoxLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)
        for key in keys:
            row = _SignalRow(key, self)
            self._rows[key] = row
            grid.addWidget(row)
        root.addLayout(grid)

        divider = QFrame()
        divider.setFixedHeight(1)
        divider.setStyleSheet(f"background:{theme.LINE_SOFT}; border:none;")
        root.addWidget(divider)

        counters = QGridLayout()
        counters.setContentsMargins(0, 0, 0, 0)
        counters.setHorizontalSpacing(14)
        counters.setVerticalSpacing(2)
        self.counter_values: dict[str, QLabel] = {}
        self.counter_captions: dict[str, QLabel] = {}
        self._counter_titles = {
            "underruns": "欠载",
            "dropped_frames": "解码丢弃帧",
            "resample_events": "重采样次数",
            "device_rate": "实际设备采样率",
        }
        specs = (
            ("underruns", "欠载"),
            ("dropped_frames", "解码丢弃帧"),
            ("resample_events", "重采样次数"),
            ("device_rate", "实际设备采样率"),
        )
        for column, (key, label) in enumerate(specs):
            caption = QLabel(i18n.tr_text(label))
            caption.setObjectName("hint")
            value = QLabel("—")
            value_font = QFont(value.font())
            value_font.setBold(True)
            value.setFont(value_font)
            self.counter_values[key] = value
            self.counter_captions[key] = caption
            wrapper = QVBoxLayout()
            wrapper.setContentsMargins(0, 0, 0, 0)
            wrapper.setSpacing(0)
            wrapper.addWidget(caption)
            wrapper.addWidget(value)
            counters.addLayout(wrapper, 0, column)
        root.addLayout(counters)
        root.addStretch(1)

    # ------------------------------------------------------------ 接口

    def update_from(self, snapshot: Optional[dict[str, Any]]) -> None:
        """用引擎快照（或单条信号路径载荷）刷新面板。

        支持三种入参：完整 ``engine.snapshot()``（含 ``signal_path``）、
        ``SignalPath.to_dict()``（含 ``stages``）、``stage.to_dict()`` 单条。
        """
        stages, counters, summary = self._extract(snapshot)
        if not stages:
            return
        for stage in stages:
            key = str(stage.get("key") or "")
            row = self._rows.get(key)
            if row is None:
                # 引擎出现了面板没预置的级：补一行，而不是丢掉这条信息
                row = _SignalRow(key, self)
                self._rows[key] = row
                layout = self.layout()
                if layout is not None:
                    layout.insertWidget(layout.count() - 1, row)
            row.update_stage(stage)
        self.summary_label.setText(self._summary_text(stages, summary))
        self._update_counters(counters, snapshot)

    def row_count(self) -> int:
        return len(self._rows)

    def row_status(self, key: str) -> str:
        """某级当前状态的文字（测试与手工核对用）。"""
        row = self._rows.get(key)
        return row.badge.text() if row is not None else ""

    def row_detail(self, key: str) -> str:
        row = self._rows.get(key)
        return row.detail_label.text() if row is not None else ""

    def summary_text(self) -> str:
        return self.summary_label.text()

    def counter_text(self, key: str) -> str:
        label = self.counter_values.get(key)
        return label.text() if label is not None else ""

    # ------------------------------------------------------------ 内部

    @staticmethod
    def _extract(
        snapshot: Optional[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any], str]:
        """从三种载荷形态里取出 ``(stages, counters, summary)``。"""
        if not snapshot:
            return [], {}, ""
        if isinstance(snapshot, list):
            return [dict(item) for item in snapshot if isinstance(item, dict)], {}, ""
        if not isinstance(snapshot, dict):
            return [], {}, ""
        if isinstance(snapshot.get("signal_path"), list):
            return (
                [dict(item) for item in snapshot["signal_path"] if isinstance(item, dict)],
                dict(snapshot.get("counters") or {}),
                str(snapshot.get("signal_summary") or ""),
            )
        if isinstance(snapshot.get("stages"), list):
            return (
                [dict(item) for item in snapshot["stages"] if isinstance(item, dict)],
                dict(snapshot.get("counters") or {}),
                str(snapshot.get("summary") or ""),
            )
        if snapshot.get("key"):
            return [dict(snapshot)], {}, ""
        return [], {}, ""

    @staticmethod
    def _summary_text(stages: list[dict[str, Any]], summary: str) -> str:
        """总述：优先用引擎给的；全直通时统一成「未做软件改动」。"""
        changed = [
            stage
            for stage in stages
            if str(stage.get("status") or "").upper()
            in ("CHANGED", "FALLBACK", "ERROR")
        ]
        if not changed:
            return i18n.tr_text("未做软件改动")
        text = _sanitize(summary) if summary else " · ".join(
            f"{i18n.tr(str(stage.get('i18n_key') or ''))}：{stage.get('detail')}"
            for stage in changed
        )
        return text or i18n.tr_text("未做软件改动")

    def _update_counters(self, counters: dict[str, Any], snapshot: Optional[dict[str, Any]]) -> None:
        for key in ("underruns", "dropped_frames", "resample_events"):
            value = counters.get(key, 0)
            try:
                number = int(value or 0)
            except (TypeError, ValueError):
                number = 0
            self.counter_values[key].setText(str(number))
        rate = 0
        if isinstance(snapshot, dict):
            try:
                rate = int(snapshot.get("rate") or 0)
            except (TypeError, ValueError):
                rate = 0
            if not rate:
                output = snapshot.get("output")
                if isinstance(output, dict):
                    for field in ("samplerate", "rate", "sample_rate"):
                        try:
                            rate = int(output.get(field) or 0)
                        except (TypeError, ValueError):
                            rate = 0
                        if rate:
                            break
        self.counter_values["device_rate"].setText(f"{rate} Hz" if rate else "—")

    def refresh_texts(self) -> None:
        """语言切换后刷新固定文案（动态 detail 由下一次快照刷新）。"""
        for key, row in self._rows.items():
            label = i18n.tr(f"sig_stage_{key}")
            if label and label != f"sig_stage_{key}":
                row.name_label.setText(label)
        for key, caption in self.counter_captions.items():
            caption.setText(i18n.tr_text(self._counter_titles.get(key, "")))
        if self._rows and self.summary_label.text() in (
            "未做软件改动",
            i18n.tr_text("未做软件改动"),
        ):
            self.summary_label.setText(i18n.tr_text("未做软件改动"))


__all__ = ["PlayerSignalsPanel", "FALLBACK_STAGE_KEYS", "STATUS_STYLE"]
