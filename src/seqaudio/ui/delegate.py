"""表格绘制代理：状态胶囊、等宽数字、选中行左侧高亮条。

为什么不用 QSS 做状态色
--------------------
QSS 只能改文字/背景色，做不出「药丸形底 + 圆角 + 内边距」的标签效果，
而状态是这张表最需要一眼分辨的信息。用 delegate 自绘可以同时拿到
精确的圆角、微透明底色和与文字一致的强调色。
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)

from . import theme

#: 列索引（与 track_model.COLUMNS 对齐）
COL_STATUS = 0
COL_TITLE = 1
COL_ARTIST = 2
COL_ALBUM = 3
COL_YEAR = 4
COL_FORMAT = 5
COL_SPEC = 6
COL_DURATION = 7
COL_KIND = 8
COL_CONFIDENCE = 9
COL_AUTHENTICITY = 10
MONO_COLUMNS = {COL_YEAR, COL_FORMAT, COL_SPEC, COL_DURATION, COL_KIND, COL_CONFIDENCE}

#: 与 track_model.TrackTableModel.PlayingRole 保持一致（此处重复声明是为了
#: 让 delegate 不反向依赖 model 模块，避免循环导入）
PLAYING_ROLE = Qt.UserRole + 1


def _mono_font(pixel_size: int = 12) -> QFont:
    font = QFont("Cascadia Mono")
    font.setStyleHint(QFont.Monospace)
    font.setFamilies(["Cascadia Mono", "Consolas", "Courier New"])
    font.setPixelSize(pixel_size)
    return font


class TrackDelegate(QStyledItemDelegate):
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index) -> None:
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""

        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        # 背景（交替行 / 选中 / 悬浮）交给 QSS 体系
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        selected = bool(option.state & QStyle.State_Selected)
        playing = bool(index.data(PLAYING_ROLE))

        # 正在播放：整行铺一层极淡的强调色，配合左侧竖条与 ▶ 一眼可辨
        if playing:
            tint = QColor(theme.ACCENT)
            tint.setAlpha(22)
            painter.fillRect(option.rect, tint)
            # 播放行底色叠加后，QSS 已画好的选中/交替行文字色仍可用，不必重绘背景

        if index.column() == COL_STATUS:
            self._paint_status_pill(painter, option.rect, text, playing)
        else:
            self._paint_text(painter, option, text, index.column(), selected, playing)

        if playing and index.column() == 0:
            # 播放行标记：3px 强调竖条 + 实心 ▶
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(
                QRectF(option.rect.left() + 2, option.rect.top() + 4, 3.0,
                       option.rect.height() - 8),
                1.5,
                1.5,
            )
            cx = option.rect.left() + 15
            cy = option.rect.center().y() + 1
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(cx - 4, cy - 5),
                        QPointF(cx - 4, cy + 5),
                        QPointF(cx + 4, cy),
                    ]
                )
            )
        elif selected and index.column() == 0:
            # 选中行左侧高亮条：比整行反白更克制，也更像"当前聚焦项"
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.ACCENT))
            painter.drawRoundedRect(
                QRectF(option.rect.left() + 2, option.rect.top() + 5, 2.5,
                       option.rect.height() - 10),
                1.2,
                1.2,
            )
        painter.restore()

    # ------------------------------------------------------------ 内部

    def _paint_status_pill(
        self, painter: QPainter, rect, text: str, playing: bool = False
    ) -> None:
        if not text:
            return
        fg_hex, bg_css = theme.STATUS_COLORS.get(text, (theme.FG_DIM, "rgba(139,149,168,0.12)"))

        font = QFont()
        font.setPixelSize(11)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()

        text_w = metrics.horizontalAdvance(text)
        # 播放行左侧要留给 ▶，状态胶囊整体右移
        left = rect.left() + (24 if playing else 6)
        pill_w = min(text_w + 16, rect.width() - (left - rect.left()) - 4)
        pill_h = min(20, rect.height() - 8)
        x = left
        y = rect.top() + (rect.height() - pill_h) / 2
        pill_rect = QRectF(x, y, max(pill_w, 24), pill_h)

        bg = QColor(fg_hex)
        bg.setAlpha(30)
        painter.setPen(QPen(QColor(fg_hex).lighter(100), 0))
        border = QColor(fg_hex)
        border.setAlpha(70)
        painter.setPen(QPen(border, 1))
        painter.setBrush(bg)
        painter.drawRoundedRect(pill_rect, pill_h / 2, pill_h / 2)

        painter.setPen(QColor(fg_hex))
        painter.drawText(pill_rect, Qt.AlignCenter, text)

    def _paint_text(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        text: str,
        column: int,
        selected: bool,
        playing: bool = False,
    ) -> None:
        if not text:
            return

        if column in MONO_COLUMNS:
            painter.setFont(_mono_font(11))
        else:
            font = QFont(option.font)
            font.setPixelSize(12)
            if column == 1:
                font.setBold(True)
            painter.setFont(font)

        # 置信度按高低上色，一眼看出哪些需要人工复核
        color = QColor(theme.FG)
        if column == COL_CONFIDENCE:
            color = self._confidence_color(text)
        elif column == COL_KIND:
            color = QColor(theme.OK) if text == "无损" else QColor(theme.FG_MUTE)
        elif column == COL_FORMAT:
            color = QColor(theme.CYAN)
        elif column == COL_ALBUM:
            color = QColor(theme.PURPLE)
        elif column == COL_YEAR:
            color = QColor(theme.ACCENT)
        elif column == COL_AUTHENTICITY:
            from ..core.authenticity import VERDICT_COLORS
            from .i18n import tr_verdict

            # 用原始判定（中文）取颜色，再本地化显示文本，两者不能混淆
            color = QColor(VERDICT_COLORS.get(text, theme.FG_MUTE))
            text = tr_verdict(text)
        elif selected:
            color = QColor(theme.FG)
        else:
            color = QColor(theme.FG_DIM)

        if column == COL_TITLE and not selected:
            color = QColor(theme.FG)
        if column == COL_ARTIST and not selected:
            color = QColor(theme.FG_DIM)

        # 播放行：歌名与歌手统一用强调色，扫一眼就知道在放哪首
        if playing and column in (COL_TITLE, COL_ARTIST):
            color = QColor(theme.ACCENT)

        painter.setPen(color)
        align = Qt.AlignVCenter | (
            Qt.AlignHCenter if column in MONO_COLUMNS else Qt.AlignLeft
        )
        rect = option.rect.adjusted(8, 0, -8, 0)
        painter.drawText(rect, align, painter.fontMetrics().elidedText(
            text, Qt.ElideRight, rect.width()
        ))

    @staticmethod
    def _confidence_color(text: str) -> QColor:
        try:
            value = float(text.rstrip("%")) / 100
        except ValueError:
            return QColor(theme.FG_MUTE)
        if value >= 0.8:
            return QColor(theme.OK)
        if value >= 0.62:
            return QColor(theme.WARN)
        return QColor(theme.BAD)

    def sizeHint(self, option: QStyleOptionViewItem, index):  # noqa: N802
        size = super().sizeHint(option, index)
        size.setHeight(max(size.height(), 32))
        return size
