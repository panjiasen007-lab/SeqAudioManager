"""Windows 原生视觉效果：深色标题栏、圆角、暗色系统边框。

Qt 默认的标题栏跟随系统主题，深色界面配白色标题栏会很割裂。
这里通过 DWM (Desktop Window Manager) 接口把标题栏也拉进暗色，
失败时静默降级 —— 在 Win10 早期版本或非 Windows 平台上不做任何事。
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

#: Windows 10 1809+：沉浸式深色模式
DWMWA_USE_IMMERSIVE_DARK_MODE = 20
#: Windows 11 22000+：标题栏与边框颜色
DWMWA_CAPTION_COLOR = 35
DWMWA_BORDER_COLOR = 34
DWMWA_WINDOW_CORNER_PREFERENCE = 33

#: 圆角偏好：2 = 圆角
DWMWCP_ROUND = 2


def _colorref(hex_color: str) -> int:
    """#RRGGBB -> Windows COLORREF（0x00BBGGRR）。"""
    value = hex_color.lstrip("#")
    r, g, b = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    return (b << 16) | (g << 8) | r


def apply_titlebar(
    hwnd: int,
    *,
    dark: bool = True,
    caption: str = "#0a0c10",
    border: str = "#232a38",
) -> bool:
    """按主题设置标题栏深浅与颜色。返回是否成功（失败仅意味着保持系统默认）。"""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        hwnd_value = wintypes.HWND(int(hwnd))

        def _set(attr: int, value: int) -> None:
            data = ctypes.c_int(value)
            dwmapi.DwmSetWindowAttribute(
                hwnd_value, attr, ctypes.byref(data), ctypes.sizeof(data)
            )

        _set(DWMWA_USE_IMMERSIVE_DARK_MODE, 1 if dark else 0)
        _set(DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND)
        # 颜色属性需要 Win11 22H2+，旧系统调用会失败但不会抛异常
        try:
            _set(DWMWA_CAPTION_COLOR, _colorref(caption))
            _set(DWMWA_BORDER_COLOR, _colorref(border))
        except Exception:  # pragma: no cover
            pass
        return True
    except Exception as exc:  # pragma: no cover - 非关键增强
        log.debug("标题栏设置失败：%s", exc)
        return False


def apply_dark_titlebar(hwnd: int, caption: str = "#0a0c10", border: str = "#232a38") -> bool:
    """兼容旧调用：切深色标题栏。"""
    return apply_titlebar(hwnd, dark=True, caption=caption, border=border)


def apply_all_windows() -> None:
    """给所有已显示窗口套用深色标题栏（弹窗也要，否则一闪白很刺眼）。"""
    if sys.platform != "win32":
        return
    try:
        from PySide6.QtWidgets import QApplication, QDialog, QWidget

        app = QApplication.instance()
        if app is None:
            return
        for widget in app.allWidgets():
            if isinstance(widget, (QWidget, QDialog)) and widget.isVisible():
                apply_dark_titlebar(int(widget.winId()))
    except Exception:  # pragma: no cover
        pass
