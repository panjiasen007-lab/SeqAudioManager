"""自定义视觉组件：状态卡片、品牌栏、胶囊标签。

Qt 的 QSS 能力有限（无阴影、无过渡），"现代感"主要靠三件事做出来：
精确的留白比例、四层色阶的深度、以及只在关键处出现的高饱和强调色。
这些组件负责后两者。
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import theme


class StatCard(QFrame):
    """统计卡片：小标题 + 等宽大数字，可点击做筛选。"""

    clicked = Signal(str)

    def __init__(
        self,
        key: str,
        title: str,
        color: str = theme.FG,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("statCard")
        self.key = key
        self._color = color
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(62)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 9, 14, 9)
        layout.setSpacing(1)

        self.title_label = QLabel(title)
        self.title_label.setObjectName("statLabel")

        self.value_label = QLabel("0")
        self.value_label.setObjectName("statValue")
        self.value_label.setStyleSheet(f"color:{color};")

        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)

    def set_value(self, value: int, suffix: str = "") -> None:
        self.value_label.setText(f"{value}{suffix}")
        # 全零时整排彩色数字会很吵；只有真正有数据的那一项才亮起来
        color = self._color if value else theme.FG_MUTE
        self.value_label.setStyleSheet(f"color:{color};")

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.key)
        super().mouseReleaseEvent(event)


class LogoMark(QWidget):
    """程序标识：渐变圆角方块 + 音序竖条，纯 QPainter 绘制（任意 DPI 清晰）。"""

    def __init__(self, size: int = 30, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(QSize(size, size))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        s = float(self._size)
        rect = QRectF(0.5, 0.5, s - 1, s - 1)

        path = QPainterPath()
        path.addRoundedRect(rect, s * 0.28, s * 0.28)

        gradient = QLinearGradient(0, 0, s, s)
        gradient.setColorAt(0.0, QColor("#5b8dff"))
        gradient.setColorAt(1.0, QColor("#2a3fb0"))
        painter.fillPath(path, gradient)

        # 音序竖条
        heights = (0.26, 0.46, 0.60, 0.38)
        bar_w = s * 0.10
        gap = s * 0.055
        total = len(heights) * bar_w + (len(heights) - 1) * gap
        x = (s - total) / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        for factor in heights:
            h = s * factor
            painter.drawRoundedRect(
                QRectF(x, (s - h) / 2, bar_w, h), bar_w / 2, bar_w / 2
            )
            x += bar_w + gap
        painter.end()


class BrandBar(QFrame):
    """顶部品牌条：标识 + 名称 + 版本 + 右侧状态胶囊 + 底部渐变细线。"""

    def __init__(self, version: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("brandBar")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(11)

        layout.addWidget(LogoMark(30))

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        text_col.setContentsMargins(0, 0, 0, 0)
        title = QLabel("音序管家")
        title.setObjectName("brandTitle")
        sub = QLabel(f"SEQAUDIO MANAGER  ·  v{version}")
        sub.setObjectName("brandSub")
        sub_font = QFont(sub.font())
        sub_font.setLetterSpacing(QFont.AbsoluteSpacing, 0.6)
        sub.setFont(sub_font)
        text_col.addWidget(title)
        text_col.addWidget(sub)
        layout.addLayout(text_col)

        layout.addStretch(1)
        self.status_pill = Pill("就绪", theme.FG_DIM)
        layout.addWidget(self.status_pill)

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        # 底部 1px 渐变线：从强调色淡出到透明，作为品牌区的收口
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        gradient = QLinearGradient(0, 0, self.width(), 0)
        top = QColor(theme.ACCENT)
        top.setAlpha(90)
        mid = QColor(theme.CYAN)
        mid.setAlpha(40)
        tail = QColor(theme.ACCENT)
        tail.setAlpha(0)
        gradient.setColorAt(0.0, top)
        gradient.setColorAt(0.45, mid)
        gradient.setColorAt(1.0, tail)
        painter.fillRect(0, self.height() - 1, self.width(), 1, gradient)
        painter.end()

    def set_status(self, text: str, color: str = theme.FG_DIM) -> None:
        self.status_pill.set_text(text, color)


class Pill(QLabel):
    """胶囊标签：药丸底 + 彩色文字，用于状态与来源标识。"""

    def __init__(self, text: str, color: str = theme.FG_DIM, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setMinimumHeight(22)
        self.setMinimumWidth(52)
        self.set_text(text, color)

    def set_text(self, text: str, color: str = theme.FG_DIM) -> None:
        self.setText(f"  {text}  ")
        self.setStyleSheet(
            f"""
            color: {color};
            background: rgba(255,255,255,0.045);
            border: 1px solid rgba(255,255,255,0.07);
            border-radius: 9px;
            padding: 2px 4px;
            font-size: 11px;
            """
        )


class SearchInput(QLineEdit):
    """带前置放大镜与占位提示的搜索框。"""

    def __init__(self, placeholder: str = "搜索", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setClearButtonEnabled(True)
        self.setFixedHeight(34)
        self.setStyleSheet(
            f"""
            QLineEdit {{
                background: {theme.BG_CARD};
                border: 1px solid {theme.LINE};
                border-radius: 8px;
                padding-left: 30px;
                padding-right: 8px;
                color: {theme.FG};
            }}
            QLineEdit:focus {{ border-color: {theme.ACCENT}; background: #12161e; }}
            """
        )

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(QColor(theme.FG_MUTE), 1.4)
        painter.setPen(pen)
        # 放大镜：圆 + 手柄
        painter.drawEllipse(QRectF(11, 10, 9, 9))
        painter.drawLine(19, 18, 23, 22)
        painter.end()


class SectionHeader(QWidget):
    """区块标题：左侧 2px 强调竖条 + 标题 + 右侧可选说明。"""

    def __init__(
        self,
        title: str,
        hint: str = "",
        color: str = theme.ACCENT,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        bar = QFrame()
        bar.setFixedSize(3, 13)
        bar.setStyleSheet(f"background:{color}; border-radius:1px;")
        layout.addWidget(bar, 0, Qt.AlignVCenter)

        label = QLabel(title)
        label.setObjectName("sectionTitle")
        layout.addWidget(label)

        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("hint")
            layout.addWidget(hint_label)

        layout.addStretch(1)


class Divider(QFrame):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet(f"background:{theme.LINE_SOFT}; border:none;")


class Card(QFrame):
    """卡片容器：圆角底 + 左侧强调竖条标题 + 内容区。

    比 QGroupBox 更可控 —— QGroupBox 的 title 定位依赖 subcontrol 规则，
    在不同 DPI 下容易压到边框上；这里用普通布局，天然对齐。
    """

    def __init__(
        self,
        title: str,
        hint: str = "",
        color: str = theme.ACCENT,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(13, 11, 13, 12)
        outer.setSpacing(9)

        outer.addWidget(SectionHeader(title, hint, color))

        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(6)
        outer.addWidget(self.body)

    def add_widget(self, widget: QWidget) -> None:
        self.body_layout.addWidget(widget)

    def add_layout(self, layout) -> None:
        self.body_layout.addLayout(layout)

    def add_form(self, rows: list[tuple[str, QWidget]]) -> None:
        """``(标签, 控件)`` 列表补齐为表单，标签右对齐、控件撑满。"""
        from PySide6.QtWidgets import QFormLayout

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setVerticalSpacing(8)
        form.setHorizontalSpacing(10)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
        for label, widget in rows:
            if label:
                form.addRow(label, widget)
            else:
                form.addRow("", widget)
        self.body_layout.addLayout(form)


def mono_font(size: int = 12, bold: bool = False) -> QFont:
    font = QFont("Cascadia Mono")
    font.setStyleHint(QFont.Monospace)
    font.setPixelSize(size)
    font.setBold(bold)
    return font


class EmptyState(QWidget):
    """列表为空时的引导层：虚线框图标 + 主副文案，居中绘制。

    空白表格是最容易显得"未完成"的地方 —— 有一句明确的下一步引导，
    用户才知道该做什么。
    """

    def __init__(
        self,
        title: str = "把音频文件拖到这里",
        subtitle: str = "支持 FLAC / APE / WAV / WV / TTA / DSF / MP3 等 · 无损优先",
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.title = title
        self.subtitle = subtitle
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        cx = self.width() / 2
        cy = self.height() / 2 - 26

        # 圆角虚线框
        box = QRectF(cx - 34, cy - 78, 68, 68)
        pen = QPen(QColor(theme.LINE), 1.4)
        pen.setStyle(Qt.DashLine)
        pen.setDashPattern([3, 3])
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRoundedRect(box, 14, 14)

        # 框内波形
        bar_color = QColor(theme.FG_MUTE)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bar_color)
        heights = (10, 20, 28, 16, 22)
        bar_w, gap = 3.0, 3.4
        total = len(heights) * bar_w + (len(heights) - 1) * gap
        x = cx - total / 2
        for h in heights:
            painter.drawRoundedRect(QRectF(x, cy - 44 - h / 2, bar_w, h), 1.5, 1.5)
            x += bar_w + gap

        # 主文案
        font = QFont(self.font())
        font.setPixelSize(14)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(theme.FG_DIM))
        painter.drawText(
            QRectF(0, cy + 4, self.width(), 26),
            Qt.AlignHCenter | Qt.AlignTop,
            self.title,
        )

        # 副文案
        sub_font = QFont(self.font())
        sub_font.setPixelSize(11)
        painter.setFont(sub_font)
        painter.setPen(QColor(theme.FG_MUTE))
        painter.drawText(
            QRectF(0, cy + 30, self.width(), 22),
            Qt.AlignHCenter | Qt.AlignTop,
            self.subtitle,
        )
        painter.end()
