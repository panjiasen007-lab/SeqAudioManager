"""界面多语言（i18n）基础设施。

覆盖范围与取舍
--------------
一个桌面工具全部字符串 × 8 语言是数千条的翻译量，一次性做完既不现实也不负责
（机翻的质量反而伤体验）。所以这里采取**渐进式**：

1. 基础设施先建好：``tr()`` 按键取值、``set_locale()`` 运行时切换、
   语言菜单挂在「更多」里，切完立即刷新；
2. **首屏最常看到的字符串**（工具栏按钮、统计卡、主菜单、状态栏、判定结论、
   主题名、语言名）先翻译 8 语言；
3. 设置面板、对话框等深层的字段文案暂以中文为准，后续按优先级补齐。

翻译语言：简体中文、繁体中文、英文、日文、韩文、俄文、法文、德文。
"""

from __future__ import annotations

#: 语言代码 -> 显示名（用各自语言书写，方便切换时辨认）
LOCALE_NAMES = {
    "zh_CN": "简体中文",
    "zh_TW": "繁體中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "ru": "Русский",
    "fr": "Français",
    "de": "Deutsch",
}

_current = "zh_CN"

# ---------------------------------------------------------------- 词条表

# 键 -> 各语言译文。缺某个语言时回退到简体中文，保证任何语言下都不出现空白。
_TRANSLATIONS: dict[str, dict[str, str]] = {
    # 工具栏
    "add_files": {"zh_CN": "添加文件", "zh_TW": "加入檔案", "en": "Add Files", "ja": "ファイル追加",
                  "ko": "파일 추가", "ru": "Добавить файлы", "fr": "Ajouter des fichiers", "de": "Dateien hinzufügen"},
    "add_folder": {"zh_CN": "添加文件夹", "zh_TW": "加入資料夾", "en": "Add Folder", "ja": "フォルダー追加",
                   "ko": "폴더 추가", "ru": "Добавить папку", "fr": "Ajouter un dossier", "de": "Ordner hinzufügen"},
    "remove": {"zh_CN": "移除", "zh_TW": "移除", "en": "Remove", "ja": "削除", "ko": "제거",
               "ru": "Удалить", "fr": "Retirer", "de": "Entfernen"},
    "clear": {"zh_CN": "清空列表", "zh_TW": "清空列表", "en": "Clear List", "ja": "リストを消去",
              "ko": "목록 비우기", "ru": "Очистить список", "fr": "Vider la liste", "de": "Liste leeren"},
    "auto": {"zh_CN": "一键全自动", "zh_TW": "一鍵全自動", "en": "Auto Process", "ja": "一括自動処理",
             "ko": "자동 처리", "ru": "Автообработка", "fr": "Tout automatiser", "de": "Automatisch"},
    "save_all": {"zh_CN": "全部确认保存", "zh_TW": "全部確認保存", "en": "Save All", "ja": "すべて保存",
                 "ko": "모두 저장", "ru": "Сохранить всё", "fr": "Tout enregistrer", "de": "Alles speichern"},
    "local_only": {"zh_CN": "仅本地处理", "zh_TW": "僅本機處理", "en": "Local Only", "ja": "ローカルのみ",
                   "ko": "로컬만", "ru": "Только локально", "fr": "Local seulement", "de": "Nur lokal"},
    "stop": {"zh_CN": "停止", "zh_TW": "停止", "en": "Stop", "ja": "停止", "ko": "중지",
             "ru": "Стоп", "fr": "Arrêter", "de": "Stopp"},
    "auth": {"zh_CN": "真假鉴定", "zh_TW": "真假鑑定", "en": "Lossless Check", "ja": "ロスレス判定",
             "ko": "무손실 검사", "ru": "Проверка lossless", "fr": "Vérifier lossless", "de": "Lossless prüfen"},
    "cleanup": {"zh_CN": "清理假无损", "zh_TW": "清理假無損", "en": "Clean Fakes", "ja": "偽物を削除",
                "ko": "가짜 정리", "ru": "Удалить фейки", "fr": "Nettoyer les faux", "de": "Fakes löschen"},
    "more": {"zh_CN": "更多", "zh_TW": "更多", "en": "More", "ja": "その他", "ko": "더보기",
             "ru": "Ещё", "fr": "Plus", "de": "Mehr"},
    "panel": {"zh_CN": "参数", "zh_TW": "參數", "en": "Options", "ja": "設定", "ko": "옵션",
              "ru": "Параметры", "fr": "Options", "de": "Optionen"},

    # 统计卡
    "stat_total": {"zh_CN": "曲目总数", "zh_TW": "曲目總數", "en": "Tracks", "ja": "曲数",
                   "ko": "트랙 수", "ru": "Треков", "fr": "Pistes", "de": "Titel"},
    "stat_lossless": {"zh_CN": "无损音源", "zh_TW": "無損音源", "en": "Lossless", "ja": "ロスレス",
                      "ko": "무손실", "ru": "Lossless", "fr": "Lossless", "de": "Lossless"},
    "stat_fake": {"zh_CN": "假无损", "zh_TW": "假無損", "en": "Fake", "ja": "偽ロスレス",
                  "ko": "가짜", "ru": "Фейк", "fr": "Faux", "de": "Fake"},
    "stat_done": {"zh_CN": "已完成", "zh_TW": "已完成", "en": "Done", "ja": "完了",
                  "ko": "완료", "ru": "Готово", "fr": "Terminé", "de": "Fertig"},
    "stat_review": {"zh_CN": "待确认", "zh_TW": "待確認", "en": "Review", "ja": "要確認",
                    "ko": "검토", "ru": "Проверить", "fr": "À vérifier", "de": "Prüfen"},
    "stat_failed": {"zh_CN": "失败", "zh_TW": "失敗", "en": "Failed", "ja": "失敗",
                    "ko": "실패", "ru": "Ошибки", "fr": "Échec", "de": "Fehler"},

    # 判定结论
    "verdict_true": {"zh_CN": "真无损", "zh_TW": "真無損", "en": "True lossless", "ja": "真ロスレス",
                     "ko": "진짜 무손실", "ru": "Настоящий lossless", "fr": "Vrai lossless", "de": "Echt lossless"},
    "verdict_dsd": {"zh_CN": "真 DSD", "zh_TW": "真 DSD", "en": "True DSD", "ja": "真 DSD",
                    "ko": "진짜 DSD", "ru": "Настоящий DSD", "fr": "Vrai DSD", "de": "Echtes DSD"},
    "verdict_fake": {"zh_CN": "假无损", "zh_TW": "假無損", "en": "Fake lossless", "ja": "偽ロスレス",
                     "ko": "가짜 무손실", "ru": "Фейк", "fr": "Faux lossless", "de": "Fake"},
    "verdict_suspect": {"zh_CN": "疑似假无损", "zh_TW": "疑似假無損", "en": "Suspected fake", "ja": "偽物の疑い",
                        "ko": "가짜 의심", "ru": "Подозрение на фейк", "fr": "Faux suspecté", "de": "Verdacht"},
    "verdict_upsampled": {"zh_CN": "疑似升频", "zh_TW": "疑似升頻", "en": "Upsampled", "ja": "アップサンプリング疑い",
                          "ko": "업샘플 의심", "ru": "Апсемплинг", "fr": "Suréchantillonné", "de": "Hochskaliert"},
    "verdict_unknown": {"zh_CN": "无法判定", "zh_TW": "無法判定", "en": "Unknown", "ja": "判定不能",
                        "ko": "판정 불가", "ru": "Не определено", "fr": "Inconnu", "de": "Unbekannt"},

    # 判定筛选
    "filter_all": {"zh_CN": "全部判定", "zh_TW": "全部判定", "en": "All verdicts", "ja": "すべて",
                   "ko": "전체", "ru": "Все", "fr": "Tous", "de": "Alle"},
    "filter_suspect": {"zh_CN": "可疑（升频/低码）", "zh_TW": "可疑（升頻/低碼）", "en": "Suspicious",
                       "ja": "疑わしい", "ko": "의심", "ru": "Подозрительные", "fr": "Suspects", "de": "Verdächtig"},
    "filter_any": {"zh_CN": "假无损 + 可疑", "zh_TW": "假無損 + 可疑", "en": "Fake + Suspect",
                   "ja": "偽物+疑い", "ko": "가짜+의심", "ru": "Фейк + подозрение", "fr": "Faux + suspects", "de": "Fake + Verdacht"},

    # 语言 / 主题菜单
    "menu_language": {"zh_CN": "语言", "zh_TW": "語言", "en": "Language", "ja": "言語",
                      "ko": "언어", "ru": "Язык", "fr": "Langue", "de": "Sprache"},
    "menu_theme": {"zh_CN": "主题", "zh_TW": "主題", "en": "Theme", "ja": "テーマ",
                   "ko": "테마", "ru": "Тема", "fr": "Thème", "de": "Design"},
}

#: 主题名也进入翻译（供主题菜单显示）
for _key, _names in {
    "深空": ("深空", "深空", "Deep Space", "深宇宙", "딥 스페이스", "Глубокий космос", "Espace profond", "Weltraum"),
    "春": ("春", "春", "Spring", "春", "봄", "Весна", "Printemps", "Frühling"),
    "夏": ("夏", "夏", "Summer", "夏", "여름", "Лето", "Été", "Sommer"),
    "秋": ("秋", "秋", "Autumn", "秋", "가을", "Осень", "Automne", "Herbst"),
    "冬": ("冬", "冬", "Winter", "冬", "겨울", "Зима", "Hiver", "Winter"),
    "太极": ("太极", "太極", "Taiji", "太極", "태극", "Тайцзи", "Taiji", "Taiji"),
    "黑白极简": ("黑白极简", "黑白極簡", "Monochrome", "モノクロ", "흑백 미니멀", "Монохром", "Monochrome", "Monochrom"),
}.items():
    _TRANSLATIONS[f"theme:{_key}"] = dict(zip(LOCALE_NAMES, _names))


# ---------------------------------------------------------------- 接口


def set_locale(locale: str) -> None:
    global _current
    if locale in LOCALE_NAMES:
        _current = locale


def current() -> str:
    return _current


def locale_names() -> dict[str, str]:
    return dict(LOCALE_NAMES)


def tr(key: str, **kwargs: str) -> str:
    """取当前语言译文；缺词条/缺语言时回退到简体中文。"""
    entry = _TRANSLATIONS.get(key)
    text = ""
    if entry:
        text = entry.get(_current) or entry.get("zh_CN") or key
    else:
        text = key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except (KeyError, IndexError):
            pass
    return text


def tr_verdict(verdict: str) -> str:
    """把判定结论映射到当前语言（不影响内部判断，只用于显示）。"""
    mapping = {
        "真无损": "verdict_true", "真 DSD": "verdict_dsd", "假无损": "verdict_fake",
        "疑似假无损": "verdict_suspect", "疑似升频": "verdict_upsampled",
        "无法判定": "verdict_unknown",
    }
    key = mapping.get(verdict)
    return tr(key) if key else verdict


def tr_theme(name: str) -> str:
    return tr(f"theme:{name}")
