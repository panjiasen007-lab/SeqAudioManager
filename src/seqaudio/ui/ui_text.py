"""合并主界面与对话框的翻译表。"""

from __future__ import annotations

from .ui_text_dialogs import UI_TEXT as _DIALOGS
from .ui_text_main import TITLE_PREFIXES, UI_TEXT as _MAIN
from .ui_text_settings import UI_TEXT as _SETTINGS

UI_TEXT = {**_MAIN, **_SETTINGS, **_DIALOGS}
