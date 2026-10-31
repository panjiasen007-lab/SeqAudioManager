"""生成应用图标（圆角底 + 音序波形），输出 assets/app.ico 与 app.png。

依赖 PySide6 的绘图能力，避免为一个图标引入 Pillow。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"


def draw(size: int):
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen

    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)

    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)

    # 圆角底板 + 渐变
    margin = size * 0.06
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0.0, QColor("#2b6cf6"))
    gradient.setColorAt(1.0, QColor("#0f2a63"))
    painter.setBrush(gradient)
    painter.setPen(Qt.NoPen)
    painter.drawRoundedRect(rect, size * 0.22, size * 0.22)

    # 音序波形：三组高低不同的圆角竖条
    bar_color = QColor("#e8f0ff")
    painter.setBrush(bar_color)
    heights = [0.26, 0.46, 0.62, 0.40, 0.54, 0.30]
    bar_w = size * 0.075
    gap = size * 0.045
    total_w = len(heights) * bar_w + (len(heights) - 1) * gap
    start_x = (size - total_w) / 2
    center_y = size / 2
    for index, factor in enumerate(heights):
        h = size * factor
        x = start_x + index * (bar_w + gap)
        painter.drawRoundedRect(
            QRectF(x, center_y - h / 2, bar_w, h), bar_w / 2, bar_w / 2
        )

    # 底部一条细线，暗示「顺序 / 轨道」
    pen = QPen(QColor(232, 240, 255, 120), max(1.0, size * 0.012))
    painter.setPen(pen)
    painter.drawLine(
        QPointF(size * 0.24, size * 0.82), QPointF(size * 0.76, size * 0.82)
    )
    painter.end()
    return image


def main() -> int:
    try:
        from PySide6.QtGui import QImage  # noqa: F401
    except ImportError:
        print("需要先安装 PySide6：pip install PySide6")
        return 1

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    ASSETS.mkdir(parents=True, exist_ok=True)

    big = draw(256)
    big.save(str(ASSETS / "app.png"), "PNG")

    # ICO：Qt 的 ICO 写入器只保留单帧，这里用 256px 源（Windows 会自动降采样）
    ico = ASSETS / "app.ico"
    if not big.save(str(ico), "ICO"):
        print("ICO 写入失败，Qt 可能缺少 ico 插件；PNG 已生成，可自行转换")
        return 1
    print(f"已生成：{ico}（{ico.stat().st_size} bytes）与 app.png")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
