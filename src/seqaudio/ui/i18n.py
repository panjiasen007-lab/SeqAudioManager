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

import re

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
    "sidebar": {"zh_CN": "侧栏", "zh_TW": "側欄", "en": "Sidebar", "ja": "サイドバー",
                "ko": "사이드바", "ru": "Боковая панель", "fr": "Panneau latéral",
                "de": "Seitenleiste"},

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
    "verdict_upscaled": {"zh_CN": "疑似升位深", "zh_TW": "疑似升位深", "en": "Bit depth upscaled",
                         "ja": "ビット深度の水増し疑い", "ko": "비트 심도 업스케일 의심",
                         "ru": "Апскейлинг разрядности", "fr": "Résolution augmentée", "de": "Bit-Tiefe hochskaliert"},
    "verdict_fake_dsd": {"zh_CN": "疑似假 DSD", "zh_TW": "疑似假 DSD", "en": "Suspected fake DSD",
                         "ja": "偽 DSD の疑い", "ko": "가짜 DSD 의심", "ru": "Подозрение на фейк DSD",
                         "fr": "Faux DSD suspecté", "de": "Verdacht auf Fake-DSD"},
    "verdict_unknown": {"zh_CN": "无法判定", "zh_TW": "無法判定", "en": "Unknown", "ja": "判定不能",
                        "ko": "판정 불가", "ru": "Не определено", "fr": "Inconnu", "de": "Unbekannt"},

    # 判定筛选
    "filter_all": {"zh_CN": "全部判定", "zh_TW": "全部判定", "en": "All verdicts", "ja": "すべて",
                   "ko": "전체", "ru": "Все", "fr": "Tous", "de": "Alle"},
    "filter_suspect": {"zh_CN": "可疑（升频/升位深/低码）", "zh_TW": "可疑（升頻/升位深/低碼）", "en": "Suspicious",
                       "ja": "疑わしい", "ko": "의심", "ru": "Подозрительные", "fr": "Suspects", "de": "Verdächtig"},
    "filter_any": {"zh_CN": "假无损 + 可疑", "zh_TW": "假無損 + 可疑", "en": "Fake + Suspect",
                   "ja": "偽物+疑い", "ko": "가짜+의심", "ru": "Фейк + подозрение", "fr": "Faux + suspects", "de": "Fake + Verdacht"},

    # 语言 / 主题菜单
    "menu_language": {"zh_CN": "语言", "zh_TW": "語言", "en": "Language", "ja": "言語",
                      "ko": "언어", "ru": "Язык", "fr": "Langue", "de": "Sprache"},
    "menu_theme": {"zh_CN": "主题", "zh_TW": "主題", "en": "Theme", "ja": "テーマ",
                   "ko": "테마", "ru": "Тема", "fr": "Thème", "de": "Design"},

    # 消息框标准按钮（Qt 自带按钮文字不随界面语言切换，这里统一接管）
    "msg_ok": {"zh_CN": "确定", "zh_TW": "確定", "en": "OK", "ja": "OK",
               "ko": "확인", "ru": "ОК", "fr": "OK", "de": "OK"},
    "msg_cancel": {"zh_CN": "取消", "zh_TW": "取消", "en": "Cancel", "ja": "キャンセル",
                   "ko": "취소", "ru": "Отмена", "fr": "Annuler", "de": "Abbrechen"},
    "msg_yes": {"zh_CN": "是", "zh_TW": "是", "en": "Yes", "ja": "はい",
                "ko": "예", "ru": "Да", "fr": "Oui", "de": "Ja"},
    "msg_no": {"zh_CN": "否", "zh_TW": "否", "en": "No", "ja": "いいえ",
               "ko": "아니요", "ru": "Нет", "fr": "Non", "de": "Nein"},
    "msg_close": {"zh_CN": "关闭", "zh_TW": "關閉", "en": "Close", "ja": "閉じる",
                  "ko": "닫기", "ru": "Закрыть", "fr": "Fermer", "de": "Schließen"},
    "msg_save": {"zh_CN": "保存", "zh_TW": "儲存", "en": "Save", "ja": "保存",
                 "ko": "저장", "ru": "Сохранить", "fr": "Enregistrer", "de": "Speichern"},
    "msg_discard": {"zh_CN": "不保存", "zh_TW": "不儲存", "en": "Discard", "ja": "保存しない",
                    "ko": "저장 안 함", "ru": "Не сохранять", "fr": "Ne pas enregistrer", "de": "Verwerfen"},
    "msg_apply": {"zh_CN": "应用", "zh_TW": "套用", "en": "Apply", "ja": "適用",
                  "ko": "적용", "ru": "Применить", "fr": "Appliquer", "de": "Anwenden"},
    "msg_reset": {"zh_CN": "重置", "zh_TW": "重設", "en": "Reset", "ja": "リセット",
                  "ko": "재설정", "ru": "Сбросить", "fr": "Réinitialiser", "de": "Zurücksetzen"},
}

#: 主题名也进入翻译（供主题菜单显示）
for _key, _names in {
    "深空": ("深空", "深空", "Deep Space", "深宇宙", "딥 스페이스", "Глубокий космос", "Espace profond", "Weltraum"),
    "春": ("春", "春", "Spring", "春", "봄", "Весна", "Printemps", "Frühling"),
    "夏": ("夏", "夏", "Summer", "夏", "여름", "Лето", "Été", "Sommer"),
    "秋": ("秋", "秋", "Autumn", "秋", "가을", "Осень", "Automne", "Herbst"),
    "冬": ("冬", "冬", "Winter", "冬", "겨울", "Зима", "Hiver", "Winter"),
    "太极": ("太极", "太極", "Taiji", "太極", "태극", "Тайцзи", "Taiji", "Taiji"),
    "黑胶": ("黑胶", "黑膠", "Vinyl", "レコード", "바이닐", "Винил", "Vinyle", "Vinyl"),
    "海岸": ("海岸", "海岸", "Coast", "海岸", "해안", "Побережье", "Côte", "Küste"),
    "暮光": ("暮光", "暮光", "Twilight", "トワイライト", "황혼", "Сумерки", "Crépuscule", "Dämmerung"),
    "纸白": ("纸白", "紙白", "Paper", "紙白", "종이색", "Бумага", "Papier", "Papier"),
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
        "疑似升位深": "verdict_upscaled", "疑似假 DSD": "verdict_fake_dsd",
        "无法判定": "verdict_unknown",
    }
    key = mapping.get(verdict)
    return tr(key) if key else verdict


def tr_theme(name: str) -> str:
    return tr(f"theme:{name}")


#: 重命名模板里的变量名（只影响显示，写入设置时仍用中文变量）
_RENAME_VARS = {
    "歌手": {"zh_TW": "歌手", "en": "artist", "ja": "アーティスト", "ko": "아티스트",
             "ru": "исполнитель", "fr": "artiste", "de": "Künstler"},
    "歌名": {"zh_TW": "歌名", "en": "title", "ja": "曲名", "ko": "곡명",
             "ru": "название", "fr": "titre", "de": "Titel"},
    "专辑": {"zh_TW": "專輯", "en": "album", "ja": "アルバム", "ko": "앨범",
             "ru": "альбом", "fr": "album", "de": "Album"},
    "年份": {"zh_TW": "年份", "en": "year", "ja": "年", "ko": "연도",
             "ru": "год", "fr": "année", "de": "Jahr"},
    "流派": {"zh_TW": "流派", "en": "genre", "ja": "ジャンル", "ko": "장르",
             "ru": "жанр", "fr": "genre", "de": "Genre"},
    "曲目": {"zh_TW": "曲目", "en": "track", "ja": "曲番号", "ko": "트랙",
             "ru": "трек", "fr": "piste", "de": "Titelnummer"},
    "碟号": {"zh_TW": "碟號", "en": "disc", "ja": "ディスク", "ko": "디스크",
             "ru": "диск", "fr": "disque", "de": "Disc"},
    "格式": {"zh_TW": "格式", "en": "format", "ja": "形式", "ko": "형식",
             "ru": "формат", "fr": "format", "de": "Format"},
}


def tr_template(text: str) -> str:
    """翻译重命名模板里的中文变量名（只用于展示）。"""
    if not text or _current == "zh_CN":
        return text
    for source, names in _RENAME_VARS.items():
        localized = names.get(_current)
        if localized:
            text = text.replace("{" + source, "{" + localized)
    return text


# ---------------------------------------------------------------- 全界面文本
#
# 键是简体中文原文，值是各语言译文。用原文当键的好处是：老代码里的
# `QLabel("曲目详情")` 不需要逐个改写成 key，`localize_tree()` 能直接
# 把控件树里的文案替换掉；新增文案也只要在这里补一行。

from .ui_text import TITLE_PREFIXES as _TITLE_PREFIXES
from .ui_text import UI_TEXT as _UI_TEXT
from .ui_text_html import (
    ABOUT_INTRO as _HTML_ABOUT,
    DISCLAIMER_TEXT as _HTML_DISCLAIMER,
    LICENSE_SUMMARY as _HTML_LICENSE,
    NOTICE as _HTML_NOTICE,
    THIRD_PARTY_INTRO as _HTML_THIRD_INTRO,
    THIRD_PARTY_NOTE as _HTML_THIRD_NOTE,
    THIRD_PARTY_PURPOSE as _HTML_PURPOSE,
)

_HTML_BLOCKS = {
    "about_intro": _HTML_ABOUT,
    "disclaimer": _HTML_DISCLAIMER,
    "license_summary": _HTML_LICENSE,
    "notice": _HTML_NOTICE,
    "third_party_intro": _HTML_THIRD_INTRO,
    "third_party_note": _HTML_THIRD_NOTE,
}


def tr_html(key: str, **kwargs) -> str:
    """取整段富文本（关于页/使用须知）并按主题色等占位符填充。"""
    entry = _HTML_BLOCKS.get(key, {})
    text = entry.get(_current) or entry.get("en") or entry.get("zh_CN") or ""
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text


def tr_purpose(text: str) -> str:
    """第三方组件列表里的用途说明。"""
    entry = _HTML_PURPOSE.get(text)
    if entry is None:
        return tr_text(text)
    return entry.get(_current) or entry.get("en") or text


_SOURCE_CACHE: dict[str, str] | None = None
_TEMPLATE_CACHE: dict[str | None, list[tuple[re.Pattern, str]]] | None = None

#: 匹配 {name} 占位符（含格式说明符 {name:02d}）
_PLACEHOLDER = re.compile(r"\{([^{}:]+)(?::([^{}]+))?\}")


def _compile_template(text: str) -> re.Pattern | None:
    """把「固定文案 + {占位符}」编译成可匹配已填充文案的正则。"""
    parts: list[str] = []
    names: list[str] = []
    last = 0
    for match in _PLACEHOLDER.finditer(text):
        name = match.group(1).strip()
        # 带格式说明符的多是「变量示例」文案，不参与动态匹配
        if match.group(2) or not name.isidentifier() or name in names:
            return None
        names.append(name)
        parts.append(re.escape(text[last:match.start()]))
        parts.append(f"(?P<{name}>.+?)")
        last = match.end()
    parts.append(re.escape(text[last:]))
    return re.compile("^" + "".join(parts) + "$", re.DOTALL)


def _template_index() -> dict[str | None, list[tuple[re.Pattern, str]]]:
    """所有含占位符词条（8 语言版本）-> 对应简体中文原文。

    只收「固定文案里有实词」的模板：像 ``{歌手} - {歌名}`` 这种纯变量拼接
    是重命名示例，不应参与动态匹配，否则会把用户歌名当成界面文案。
    按首字符分桶，表格里成千上万条用户数据不必逐条跑全部正则。
    """
    global _TEMPLATE_CACHE
    if _TEMPLATE_CACHE is not None:
        return _TEMPLATE_CACHE

    candidates: list[tuple[int, re.Pattern, str, str | None]] = []
    seen: set[str] = set()
    for source, entry in _UI_TEXT.items():
        if "{" not in source:
            continue
        for variant in entry.values():
            if "{" not in variant:
                continue
            literal = _PLACEHOLDER.sub("", variant)
            if not re.search(
                r"[A-Za-z\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", literal
            ):
                continue
            pattern = _compile_template(variant)
            if pattern is None or pattern.pattern in seen:
                continue
            seen.add(pattern.pattern)
            first = None if variant.startswith("{") else variant[0]
            candidates.append((len(literal), pattern, source, first))

    # 固定文案越长越具体，先匹配，避免短模板抢走整句
    candidates.sort(key=lambda item: item[0], reverse=True)
    buckets: dict[str | None, list[tuple[re.Pattern, str]]] = {}
    for _, pattern, source, first in candidates:
        buckets.setdefault(first, []).append((pattern, source))
    _TEMPLATE_CACHE = buckets
    return _TEMPLATE_CACHE


def _match_template(text: str) -> tuple[str, dict[str, str]] | None:
    index = _template_index()
    for bucket in (index.get(text[0]), index.get(None)):
        if not bucket:
            continue
        for pattern, source in bucket:
            match = pattern.match(text)
            if match:
                return source, match.groupdict()
    return None


def _source_map() -> dict[str, str]:
    """所有语言译文 -> 简体中文原文（首次调用后缓存）。"""
    global _SOURCE_CACHE
    if _SOURCE_CACHE is not None:
        return _SOURCE_CACHE
    mapping: dict[str, str] = {}
    for entry in _TRANSLATIONS.values():
        source = entry.get("zh_CN")
        if not source:
            continue
        for value in entry.values():
            mapping.setdefault(value, source)
    for source, entry in _UI_TEXT.items():
        for value in entry.values():
            mapping.setdefault(value, source)
    _SOURCE_CACHE = mapping
    return mapping


def tr_text(text: str) -> str:
    """按简体中文原文取当前语言译文；找不到时原样返回。"""
    if not text:
        return text
    source = _source_map().get(text, text)
    if _current == "zh_CN":
        return source
    entry = _UI_TEXT.get(source)
    if entry is not None:
        return entry.get(_current) or entry.get("en") or source
    for values in _TRANSLATIONS.values():
        if values.get("zh_CN") == source:
            return values.get(_current) or values.get("zh_CN") or source

    # 已经填充过数据的动态文案（如「共 4 首 · 无损 4 …」）
    matched = _match_template(text)
    if matched is not None:
        template, values = matched
        return tr_format(template, **values)
    return source


def tr_format(template: str, **kwargs) -> str:
    """翻译含占位符的文案；格式化失败时返回未格式化的译文。

    第一个参数**不能**叫 ``source``：界面文案里「来源」类占位符正好叫
    ``{source}``（歌词来源、封面来源、数据来源），调用方会写成
    ``tr_format("歌词来源：{source}", source=...)`` —— 位置参数与关键字参数同名，
    Python 直接抛 ``got multiple values for argument 'source'``，整个
    tooltip / 状态栏渲染中断。改名成 ``template`` 就不会再撞。
    """
    text = tr_text(template)
    try:
        return text.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return text


def tr_dynamic(text: str) -> str:
    """翻译带文件名的窗口标题等「固定前缀 + 用户数据」。"""
    if not text:
        return text
    exact = tr_text(text)
    if exact != text:
        return exact
    mapped = _source_map().get(text)
    if mapped is not None:
        return tr_text(mapped)
    for source, translations in _TITLE_PREFIXES:
        if text.startswith(source):
            translated = (
                translations.get(_current)
                or translations.get("en")
                or source
            )
            return translated + text[len(source):]
    return text


def install_localizer(app) -> None:
    """安装 Show 事件过滤器：对话框一显示就自动翻译其中的控件。"""
    if getattr(app, "_seqaudio_i18n_localizer", None) is not None:
        return
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QWidget

    class _Localizer(QObject):
        def eventFilter(self, obj, event):  # noqa: N802
            if (
                event.type() == QEvent.Show
                and isinstance(obj, QWidget)
                and obj.isWindow()
            ):
                localize_tree(obj)
            return False

    localizer = _Localizer(app)
    app._seqaudio_i18n_localizer = localizer
    app.installEventFilter(localizer)


def localize_tree(root) -> None:
    """把一棵控件树里能识别的中文文案替换成当前语言。"""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractButton,
        QAbstractSpinBox,
        QComboBox,
        QDialogButtonBox,
        QGroupBox,
        QLabel,
        QLineEdit,
        QListWidget,
        QMenu,
        QMessageBox,
        QPlainTextEdit,
        QTabWidget,
        QTableWidget,
        QTextEdit,
        QWidget,
    )

    protected = set(LOCALE_NAMES.values())

    def translate(value: str) -> str:
        """翻译时保留首尾空白（胶囊标签用空格做内边距）。"""
        if not value or value in protected:
            return value
        if value.strip() in ("—", "-", ""):
            return value
        stripped = value.strip()
        translated = tr_text(stripped)
        if translated == stripped:
            return value
        left = value[: len(value) - len(value.lstrip())]
        right = value[len(value.rstrip()):]
        return left + translated + right

    def apply_text(obj, getter, setter) -> None:
        try:
            current = getter()
            translated = translate(current)
            if translated != current:
                setter(translated)
        except (AttributeError, RuntimeError):
            pass

    for widget in [root] + root.findChildren(QWidget):
        if not isinstance(widget, QWidget):
            continue
        title = widget.windowTitle()
        if title:
            translated_title = tr_dynamic(title)
            if translated_title != title:
                widget.setWindowTitle(translated_title)
        apply_text(widget, widget.toolTip, widget.setToolTip)
        if isinstance(widget, QAbstractButton):
            apply_text(widget, widget.text, widget.setText)
        if isinstance(widget, QLabel):
            apply_text(widget, widget.text, widget.setText)
        if isinstance(widget, QLineEdit):
            apply_text(widget, widget.placeholderText, widget.setPlaceholderText)
        if isinstance(widget, QPlainTextEdit):
            apply_text(widget, widget.placeholderText, widget.setPlaceholderText)
        if isinstance(widget, QTextEdit):
            apply_text(widget, widget.placeholderText, widget.setPlaceholderText)
        if isinstance(widget, QGroupBox):
            apply_text(widget, widget.title, widget.setTitle)
        if isinstance(widget, QAbstractSpinBox):
            apply_text(widget, widget.suffix, widget.setSuffix)
        if isinstance(widget, QComboBox):
            for index in range(widget.count()):
                current = widget.itemText(index)
                translated = translate(current)
                if translated != current:
                    widget.setItemText(index, translated)
                tip = widget.itemData(index, Qt.ToolTipRole)
                if isinstance(tip, str) and tip:
                    translated_tip = translate(tip)
                    if translated_tip != tip:
                        widget.setItemData(index, translated_tip, Qt.ToolTipRole)
        if isinstance(widget, QTabWidget):
            for index in range(widget.count()):
                current = widget.tabText(index)
                translated = translate(current)
                if translated != current:
                    widget.setTabText(index, translated)
        if isinstance(widget, QTableWidget):
            for index in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(index)
                if item is not None:
                    current = item.text()
                    translated = translate(current)
                    if translated != current:
                        item.setText(translated)
        if isinstance(widget, QListWidget):
            for index in range(widget.count()):
                item = widget.item(index)
                if item is not None:
                    current = item.text()
                    translated = translate(current)
                    if translated != current:
                        item.setText(translated)
        if isinstance(widget, QMessageBox) or isinstance(widget, QDialogButtonBox):
            _localize_standard_buttons(widget)

    for menu in root.findChildren(QMenu):
        apply_text(menu, menu.title, menu.setTitle)
        for action in menu.actions():
            apply_text(action, action.text, action.setText)
            apply_text(action, action.toolTip, action.setToolTip)


def _localize_message_box(box) -> None:
    """兼容旧入口：消息框的标准按钮本地化。"""
    _localize_standard_buttons(box)


def _localize_standard_buttons(container) -> None:
    """Qt 标准按钮的文字不随语言变化，按当前语言重设。"""
    from PySide6.QtWidgets import QMessageBox

    mapping = {
        QMessageBox.Ok: "msg_ok",
        QMessageBox.Cancel: "msg_cancel",
        QMessageBox.Yes: "msg_yes",
        QMessageBox.No: "msg_no",
        QMessageBox.Close: "msg_close",
        QMessageBox.Save: "msg_save",
        QMessageBox.Discard: "msg_discard",
        QMessageBox.Apply: "msg_apply",
        QMessageBox.Reset: "msg_reset",
    }
    for button in container.buttons():
        key = mapping.get(container.standardButton(button))
        if key is not None:
            button.setText(tr(key))

