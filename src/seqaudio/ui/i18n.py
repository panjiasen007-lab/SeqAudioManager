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


# ---------------------------------------------------------------- 播放词条（v1.6）
#
# 键前缀按架构文档 10.2：play_*（控制）、pl_*（队列）、lyr_*（歌词）、
# sig_*（信号路径）、eq_*（均衡器）、dev_*（设备/后端）。
#
# 行格式：key, 简体中文, 繁體中文, English, 日本語, 한국어, Русский, Français,
# Deutsch —— 与 ui_text_main.py 的 ROWS 写法一致，八语言齐全，漏一列会被
# zip 截断而不是悄悄少一个语言。
#
# 注意：这里同时喂给 tr(key) 与 tr_text(简体原文) 两条通路 —— 后者靠
# _source_map() 反查（值→zh_CN），所以界面里直接写 QLabel("播放中") 也能跟着
# 换语言，不必逐个改写成 tr("play_state_playing")。

_PLAYBACK_ROWS: tuple[tuple[str, ...], ...] = (
    # ---- 播放控制 ----
    ("play_play", "播放", "播放", "Play", "再生", "재생", "Воспроизвести", "Lire", "Wiedergabe"),
    ("play_pause", "暂停", "暫停", "Pause", "一時停止", "일시정지", "Пауза", "Pause", "Pause"),
    ("play_stop", "停止", "停止", "Stop", "停止", "정지", "Стоп", "Arrêter", "Stopp"),
    ("play_prev", "上一首", "上一首", "Previous", "前へ", "이전 곡", "Предыдущий", "Précédent", "Vorheriger Titel"),
    ("play_next", "下一首", "下一首", "Next", "次へ", "다음 곡", "Следующий", "Suivant", "Nächster Titel"),
    ("play_volume", "音量", "音量", "Volume", "音量", "음량", "Громкость", "Volume", "Lautstärke"),
    ("play_mute", "静音", "靜音", "Mute", "ミュート", "음소거", "Без звука", "Muet", "Stumm"),
    ("play_seek", "进度", "進度", "Position", "再生位置", "재생 위치", "Позиция", "Position", "Position"),
    ("play_loading", "载入中…", "載入中…", "Loading…", "読み込み中…", "불러오는 중…", "Загрузка…", "Chargement…", "Wird geladen…"),
    ("play_buffering", "缓冲中…", "緩衝中…", "Buffering…", "バッファリング中…", "버퍼링 중…", "Буферизация…", "Mise en mémoire tampon…", "Puffern…"),
    ("play_reconnect", "重新连接", "重新連線", "Reconnect", "再接続", "다시 연결", "Переподключить", "Reconnecter", "Neu verbinden"),
    ("play_no_device", "无可用输出设备", "無可用的輸出裝置", "No output device", "利用可能な出力デバイスがありません", "사용 가능한 출력 장치 없음", "Нет доступного устройства вывода", "Aucun périphérique de sortie", "Kein Ausgabegerät verfügbar"),
    ("play_disabled", "播放功能已关闭", "播放功能已關閉", "Playback disabled", "再生機能は無効です", "재생 기능 꺼짐", "Воспроизведение отключено", "Lecture désactivée", "Wiedergabe deaktiviert"),

    # ---- 状态 ----
    ("play_state_idle", "空闲", "閒置", "Idle", "待機", "대기", "Ожидание", "Inactif", "Bereit"),
    ("play_state_loading", "载入中", "載入中", "Loading", "読み込み中", "불러오는 중", "Загрузка", "Chargement", "Lädt"),
    ("play_state_playing", "播放中", "播放中", "Playing", "再生中", "재생 중", "Играет", "Lecture", "Wiedergabe läuft"),
    ("play_state_paused", "已暂停", "已暫停", "Paused", "一時停止中", "일시정지됨", "Пауза", "En pause", "Angehalten"),
    ("play_state_error", "播放错误", "播放錯誤", "Playback error", "再生エラー", "재생 오류", "Ошибка воспроизведения", "Erreur de lecture", "Wiedergabefehler"),

    # ---- 队列 ----
    ("pl_queue", "队列", "佇列", "Queue", "キュー", "대기열", "Очередь", "File d’attente", "Warteschlange"),
    ("pl_queue_empty", "队列为空", "佇列為空", "Queue is empty", "キューは空です", "대기열이 비어 있음", "Очередь пуста", "La file est vide", "Warteschlange ist leer"),
    ("pl_total", "总时长", "總時長", "Total length", "合計時間", "총 길이", "Общая длительность", "Durée totale", "Gesamtdauer"),
    ("pl_add_next", "下一首播放", "下一首播放", "Play next", "次に再生", "다음에 재생", "Играть следующим", "Lire ensuite", "Als Nächstes"),
    ("pl_add_last", "加入队列", "加入佇列", "Add to queue", "キューに追加", "대기열에 추가", "Добавить в очередь", "Ajouter à la file", "Zur Warteschlange"),
    ("pl_remove", "从队列移除", "從佇列移除", "Remove from queue", "キューから削除", "대기열에서 제거", "Убрать из очереди", "Retirer de la file", "Aus Warteschlange entfernen"),
    ("pl_clear", "清空队列", "清空佇列", "Clear queue", "キューを空にする", "대기열 비우기", "Очистить очередь", "Vider la file", "Warteschlange leeren"),
    ("pl_mode_sequential", "顺序播放", "順序播放", "Sequential", "順番に再生", "순차 재생", "По порядку", "Séquentiel", "Der Reihe nach"),
    ("pl_mode_shuffle", "随机播放", "隨機播放", "Shuffle", "シャッフル", "무작위 재생", "Перемешать", "Aléatoire", "Zufällig"),
    ("pl_mode_repeat_one", "单曲循环", "單曲循環", "Repeat one", "1 曲リピート", "한 곡 반복", "Повтор одной", "Répéter le titre", "Titel wiederholen"),
    ("pl_mode_repeat_all", "列表循环", "清單循環", "Repeat all", "全曲リピート", "전체 반복", "Повтор списка", "Répéter la liste", "Liste wiederholen"),
    ("pl_locate_playing", "定位到正在播放", "定位到正在播放", "Locate playing track", "再生中の曲へ移動", "재생 중인 곡으로 이동", "Перейти к играющему треку", "Aller au titre en lecture", "Zum laufenden Titel springen"),
    ("pl_no_playing", "当前没有正在播放的歌曲", "目前沒有正在播放的歌曲", "Nothing is playing right now", "再生中の曲はありません", "지금 재생 중인 곡이 없습니다", "Сейчас ничего не играет", "Aucun titre en lecture", "Aktuell läuft nichts"),

    # ---- 歌词 ----
    ("lyr_panel", "歌词", "歌詞", "Lyrics", "歌詞", "가사", "Текст песни", "Paroles", "Liedtext"),
    ("lyr_empty", "暂无歌词", "暫無歌詞", "No lyrics", "歌詞がありません", "가사 없음", "Текста нет", "Aucune parole", "Kein Liedtext"),
    ("lyr_offset", "歌词偏移", "歌詞偏移", "Lyrics offset", "歌詞オフセット", "가사 오프셋", "Смещение текста", "Décalage des paroles", "Liedtext-Versatz"),

    # ---- 信号路径 ----
    ("sig_panel", "信号路径", "訊號路徑", "Signal path", "信号経路", "신호 경로", "Путь сигнала", "Chemin du signal", "Signalweg"),
    ("sig_stage_source", "源文件", "來源檔案", "Source file", "ソースファイル", "원본 파일", "Исходный файл", "Fichier source", "Quelldatei"),
    ("sig_stage_decode", "解码器", "解碼器", "Decoder", "デコーダー", "디코더", "Декодер", "Décodeur", "Decoder"),
    ("sig_stage_format", "样本格式", "樣本格式", "Sample format", "サンプル形式", "샘플 형식", "Формат отсчётов", "Format des échantillons", "Sampleformat"),
    ("sig_stage_gain", "音量", "音量", "Gain", "ゲイン", "게인", "Усиление", "Gain", "Verstärkung"),
    ("sig_stage_eq", "均衡器", "等化器", "Equalizer", "イコライザー", "이퀄라이저", "Эквалайзер", "Égaliseur", "Entzerrer"),
    ("sig_stage_fade", "淡变/无缝", "淡變/無縫", "Fade / gapless", "フェード/ギャップレス", "페이드/갭리스", "Затухание/без пауз", "Fondu / sans pause", "Überblendung/Lückenlos"),
    ("sig_stage_resample", "重采样/声道", "重採樣/聲道", "Resample / channels", "リサンプル/チャンネル", "리샘플/채널", "Ресемплинг/каналы", "Rééchantillonnage / canaux", "Resampling/Kanäle"),
    ("sig_stage_output", "输出后端", "輸出後端", "Output backend", "出力バックエンド", "출력 백엔드", "Бэкенд вывода", "Backend de sortie", "Ausgabe-Backend"),
    ("sig_stage_device", "设备上报格式", "裝置回報格式", "Device format", "デバイス報告形式", "장치 보고 형식", "Формат устройства", "Format du périphérique", "Geräteformat"),
    ("sig_status_passthrough", "原样通过", "原樣通過", "Passed through", "そのまま通過", "그대로 통과", "Без изменений", "Transmis tel quel", "Unverändert durchgereicht"),
    ("sig_status_changed", "已改变", "已改變", "Changed", "変更あり", "변경됨", "Изменено", "Modifié", "Verändert"),
    ("sig_status_bypassed", "已旁路", "已旁路", "Bypassed", "バイパス", "바이패스", "Обход", "Contourné", "Umgangen"),
    ("sig_status_fallback", "已回退", "已回退", "Fell back", "フォールバック", "대체됨", "Откат", "Repli", "Zurückgefallen"),
    ("sig_status_error", "错误", "錯誤", "Error", "エラー", "오류", "Ошибка", "Erreur", "Fehler"),
    ("sig_int_pipeline_unavailable", "整数通路不可用（DSP 已启用，改用浮点）", "整數通路不可用（DSP 已啟用，改用浮點）", "Integer path unavailable (DSP enabled, using float)", "整数パスは使用不可（DSP 有効のため浮動小数点）", "정수 경로 사용 불가(DSP 활성, 부동소수 사용)", "Целочисленный путь недоступен (DSP включён, используется float)", "Chemin entier indisponible (DSP actif, float utilisé)", "Ganzzahlpfad nicht verfügbar (DSP aktiv, Float verwendet)"),
    ("sig_summary_no_change", "未做软件改动", "未做軟體改動", "No software change", "ソフト側の変更なし", "소프트웨어 변경 없음", "Без программных изменений", "Aucune modification logicielle", "Keine Software-Änderung"),
    ("sig_fallback_rate", "已重采样 {src}→{dst}（设备不支持源采样率）", "已重採樣 {src}→{dst}（裝置不支援來源取樣率）", "Resampled {src}→{dst} (device cannot do the source rate)", "{src}→{dst} にリサンプル（デバイスが元のレート非対応）", "{src}→{dst} 리샘플(장치가 원본 레이트 미지원)", "Ресемплинг {src}→{dst} (устройство не поддерживает исходную частоту)", "Rééchantillonné {src}→{dst} (périphérique incompatible)", "{src}→{dst} neu abgetastet (Gerät unterstützt Quellrate nicht)"),
    ("sig_untouched", "未做软件改动", "未做軟體改動", "No software change", "ソフト側の変更なし", "소프트웨어 변경 없음", "Без программных изменений", "Aucune modification logicielle", "Keine Software-Änderung"),
    ("sig_underruns", "欠载", "欠載", "Underruns", "アンダーラン", "언더런", "Прерывания", "Sous-alimentations", "Unterläufe"),
    ("sig_gain_db", "增益", "增益", "Gain", "ゲイン", "게인", "Усиление", "Gain", "Verstärkung"),

    # ---- 均衡器 ----
    ("eq_title", "均衡器", "等化器", "Equalizer", "イコライザー", "이퀄라이저", "Эквалайзер", "Égaliseur", "Entzerrer"),
    ("eq_bypass", "旁路", "旁路", "Bypass", "バイパス", "바이패스", "Обход", "Contourner", "Umgehen"),
    ("eq_preset", "预设", "預設", "Preset", "プリセット", "프리셋", "Пресет", "Préréglage", "Voreinstellung"),
    ("eq_snapshot_a", "A 快照", "A 快照", "Snapshot A", "スナップショット A", "스냅샷 A", "Снимок A", "Instantané A", "Snapshot A"),
    ("eq_snapshot_b", "B 快照", "B 快照", "Snapshot B", "スナップショット B", "스냅샷 B", "Снимок B", "Instantané B", "Snapshot B"),
    ("eq_flat", "平坦", "平坦", "Flat", "フラット", "평탄", "Ровно", "Plat", "Flach"),

    # ---- 设备 / 后端 ----
    ("dev_backend", "输出后端", "輸出後端", "Output backend", "出力バックエンド", "출력 백엔드", "Бэкенд вывода", "Backend de sortie", "Ausgabe-Backend"),
    ("dev_device", "输出设备", "輸出裝置", "Output device", "出力デバイス", "출력 장치", "Устройство вывода", "Périphérique de sortie", "Ausgabegerät"),
    ("dev_exclusive", "独占", "獨佔", "Exclusive", "排他", "독점", "Монопольно", "Exclusif", "Exklusiv"),
    ("dev_exclusive_busy", "设备忙（独占被占用）", "裝置忙碌（獨佔被佔用）", "Device busy (exclusive in use)", "デバイス使用中（排他モード）", "장치 사용 중(독점)", "Устройство занято (монопольный режим)", "Périphérique occupé (exclusif)", "Gerät belegt (exklusiv)"),
    ("dev_samplerate", "采样率", "取樣率", "Sample rate", "サンプルレート", "샘플레이트", "Частота дискретизации", "Fréquence d’échantillonnage", "Abtastrate"),
    ("dev_latency", "延迟", "延遲", "Latency", "レイテンシー", "지연", "Задержка", "Latence", "Latenz"),
    ("dev_backend_wasapi", "WASAPI", "WASAPI", "WASAPI", "WASAPI", "WASAPI", "WASAPI", "WASAPI", "WASAPI"),
    ("dev_backend_directsound", "DirectSound", "DirectSound", "DirectSound", "DirectSound", "DirectSound", "DirectSound", "DirectSound", "DirectSound"),
    ("dev_backend_mme", "MME", "MME", "MME", "MME", "MME", "MME", "MME", "MME"),
    ("dev_backend_wdmks", "WDM-KS", "WDM-KS", "WDM-KS", "WDM-KS", "WDM-KS", "WDM-KS", "WDM-KS", "WDM-KS"),

    # ---- 位完美话术边界（v1.7 §7，L3 永久禁止）----
    ("sig_l0_untouched", "未做软件改动", "未做軟體改動", "No software change", "ソフト側の変更なし", "소프트웨어 변경 없음", "Без программных изменений", "Aucune modification logicielle", "Keine Software-Änderung"),
    ("sig_l1_bypass_mixer", "已绕开系统混音器（系统混音 {mix} Hz · 本流 {rate} Hz）", "已繞開系統混音器（系統混音 {mix} Hz · 本流 {rate} Hz）", "System mixer bypassed (mix {mix} Hz · stream {rate} Hz)", "システムミキサーを迂回（ミックス {mix} Hz · 本ストリーム {rate} Hz）", "시스템 믹서 우회(믹스 {mix} Hz · 스트림 {rate} Hz)", "Системный микшер обойдён (микс {mix} Hz · поток {rate} Hz)", "Mixeur système contourné (mix {mix} Hz · flux {rate} Hz)", "Systemmixer umgangen (Mix {mix} Hz · Stream {rate} Hz)"),
    ("sig_l2_no_resample", "本进程未重采样、未改采样率、未加软件增益（{container} 位容器 / 有效位深 {depth} 位）", "本行程未重採樣、未改取樣率、未加軟體增益（{container} 位容器 / 有效位深 {depth} 位）", "This process did not resample, change rate or add gain ({container}-bit container / {depth}-bit effective)", "本プロセスはリサンプル・レート変更・ゲイン付加なし（{container} bit コンテナ / 有効 {depth} bit）", "이 프로세스는 리샘플·레이트 변경·게인 없음({container}비트 컨테이너 / 유효 {depth}비트)", "Процесс не делал ресемплинг, не менял частоту и не добавлял усиление ({container}-битный контейнер / {depth} бит)", "Ce processus n’a ni rééchantillonné, ni changé la fréquence, ni ajouté de gain (conteneur {container} bits / {depth} bits effectifs)", "Dieser Prozess hat nicht neu abgetastet, die Rate nicht geändert und keinen Gain hinzugefügt ({container}-Bit-Container / {depth} Bit effektiv)"),

    # ---- 后端优劣提示（设置面板「播放」页用）----
    ("dev_hint_mme", "推荐：可跟随源采样率（44.1k/48k/96k/192k）", "推薦：可跟隨來源取樣率（44.1k/48k/96k/192k）", "Recommended: follows the source rate (44.1k/48k/96k/192k)", "推奨：ソースのサンプルレートに追従（44.1k/48k/96k/192k）", "권장: 원본 샘플레이트 추종(44.1k/48k/96k/192k)", "Рекомендуется: следует частоте источника (44.1k/48k/96k/192k)", "Recommandé : suit la fréquence source (44,1k/48k/96k/192k)", "Empfohlen: folgt der Quellrate (44,1k/48k/96k/192k)"),
    ("dev_hint_directsound", "可跟随源采样率，延迟略高于 MME", "可跟隨來源取樣率，延遲略高於 MME", "Follows the source rate, slightly higher latency than MME", "ソースレートに追従、MME よりやや高遅延", "원본 레이트 추종, MME보다 지연 약간 높음", "Следует частоте источника, задержка чуть выше MME", "Suit la fréquence source, latence un peu plus élevée que MME", "Folgt der Quellrate, etwas höhere Latenz als MME"),
    ("dev_hint_wasapi", "共享模式会被系统混音到混音格式并重采样；独占可跟随源采样率", "共享模式會被系統混音到混音格式並重採樣；獨佔可跟隨來源取樣率", "Shared mode is mixed to the system mix format and resampled; exclusive follows the source rate", "共有モードはミックス形式に変換・リサンプルされます。排他ならソースレートに追従", "공유 모드는 시스템 믹스 형식으로 믹스·리샘플됩니다. 독점은 원본 레이트 추종", "В общем режиме звук микшируется в формат системы с ресемплингом; монопольный режим следует частоте источника", "En mode partagé, le mixage système rééchantillonne ; le mode exclusif suit la fréquence source", "Im Shared-Modus wird auf das Systemformat gemischt und neu abgetastet; exklusiv folgt der Quellrate"),

    # ---- 独占输出（v1.7）----
    ("dev_mode_shared", "共享（系统混音）", "共享（系統混音）", "Shared (system mixer)", "共有（システムミキサー）", "공유(시스템 믹서)", "Общий (системный микшер)", "Partagé (mixeur système)", "Geteilt (Systemmixer)"),
    ("dev_mode_exclusive", "独占", "獨佔", "Exclusive", "排他", "독점", "Монопольный", "Exclusif", "Exklusiv"),
    ("dev_mode_exclusive_strict", "独占（严格：不转换样本格式）", "獨佔（嚴格：不轉換樣本格式）", "Exclusive (strict: no format conversion)", "排他（厳格：形式変換なし）", "독점(엄격: 형식 변환 없음)", "Монопольный (строгий: без преобразования формата)", "Exclusif (strict : sans conversion)", "Exklusiv (streng: keine Formatwandlung)"),
    # 别名：设计文档/能力矩阵里叫 "strict"，面板代码叫 exclusive_strict，两个名字都要能取到
    ("dev_mode_strict", "独占（严格：不转换样本格式）", "獨佔（嚴格：不轉換樣本格式）", "Exclusive (strict: no format conversion)", "排他（厳格：形式変換なし）", "독점(엄격: 형식 변환 없음)", "Монопольный (строгий: без преобразования формата)", "Exclusif (strict : sans conversion)", "Exklusiv (streng: keine Formatwandlung)"),
    ("dev_mode_current", "当前模式：{mode}", "目前模式：{mode}", "Current mode: {mode}", "現在のモード：{mode}", "현재 모드: {mode}", "Текущий режим: {mode}", "Mode actuel : {mode}", "Aktueller Modus: {mode}"),
    ("dev_strict_unsupported", "本设备不支持严格独占（实测该设备各采样率均不可用），建议用宽松独占", "本裝置不支援嚴格獨佔（實測各取樣率均不可用），建議用寬鬆獨佔", "This device does not support strict exclusive mode (measured: no rate works); use loose exclusive", "このデバイスは厳格排他に対応していません（実測で全レート不可）。通常の排他をご利用ください", "이 장치에서는 엄격 독점을 지원하지 않습니다(실측: 모든 레이트 불가). 일반 독점 사용 권장", "Устройство не поддерживает строгий монопольный режим (замер: ни одна частота). Используйте обычный монопольный", "Cet appareil ne gère pas l’exclusif strict (mesuré : aucune fréquence) ; utilisez l’exclusif simple", "Dieses Gerät unterstützt keinen strengen Exklusivmodus (gemessen: keine Rate); bitte einfachen Exklusivmodus nutzen"),
    ("dev_strict_auto_downgraded", "本设备不支持严格独占，已自动改为「独占」", "本裝置不支援嚴格獨佔，已自動改為「獨佔」", "Strict exclusive is unsupported here; switched to exclusive", "厳格排他は未対応のため「排他」に切り替えました", "엄격 독점 미지원이라 ‘독점’으로 전환했습니다", "Строгий режим не поддерживается — переключено на монопольный", "Exclusif strict non pris en charge : passé en exclusif", "Streng exklusiv nicht unterstützt – auf exklusiv umgestellt"),
    ("dev_cap_disclaimer", "能力矩阵为零出声探测结果；个别设备存在「探测通过、开流被拒」的情况，程序会在起播时自动降级并在信号路径注明", "能力矩陣為零出聲探測結果；個別裝置存在「探測通過、開流被拒」的情況，程式會在起播時自動降級並在訊號路徑註明", "The matrix comes from silent probing; a few devices answer OK but refuse to open. Playback then degrades automatically and says so in the signal path", "能力表は無音プローブの結果です。まれに「プローブ成功・開流拒否」のデバイスがあり、その場合は起動時に自動降格し信号経路に明記します", "능력표는 무음 프로브 결과입니다. 일부 장치는 ‘프로브 성공·개방 거부’가 있어 재생 시 자동 강등하고 신호 경로에 표시합니다", "Матрица получена тихой проверкой; некоторые устройства отвечают «можно», но отказывают при открытии — тогда воспроизведение автоматически понижается с пометкой в пути сигнала", "La matrice provient d’une sonde silencieuse ; certains appareils répondent OK mais refusent l’ouverture : la lecture se dégrade alors automatiquement, signalé dans le chemin du signal", "Die Matrix stammt aus stiller Prüfung; manche Geräte melden OK, verweigern aber das Öffnen – die Wiedergabe stuft dann automatisch zurück und vermerkt es im Signalweg"),
    ("exclusive_notice_title", "输出模式已改为独占", "輸出模式已改為獨佔", "Output mode changed to exclusive", "出力モードを排他に変更しました", "출력 모드가 독점으로 변경되었습니다", "Режим вывода изменён на монопольный", "Mode de sortie passé en exclusif", "Ausgabemodus auf exklusiv geändert"),
    ("exclusive_notice_text", "已切换到「独占输出」：音源不再被系统混音重采样。请注意两点——① 独占会绕开 Windows 系统音量，此时调系统音量滑块无效，请用播放条上的软件音量滑块调节；② 播放期间其它程序（浏览器、会议软件）可能没声音，停止或暂停会立刻释放设备。", "已切換到「獨佔輸出」：音源不再被系統混音重採樣。請注意兩點——① 獨佔會繞開 Windows 系統音量，此時調系統音量滑桿無效，請用播放條上的軟體音量滑桿調節；② 播放期間其他程式（瀏覽器、會議軟體）可能沒聲音，停止或暫停會立刻釋放裝置。", "Switched to exclusive output: audio is no longer resampled by the system mixer. Note: ① exclusive bypasses the Windows system volume, so the system volume slider has no effect — use the software volume slider on the playback bar; ② other apps (browser, meeting software) may be silent during playback; stopping or pausing releases the device immediately.", "「排他出力」に切り替えました。① 排他は Windows のシステム音量を迂回するため、システム音量スライダーは効きません。再生バーのソフト音量スライダーで調整してください。② 再生中は他のアプリが無音になる場合があります。停止・一時停止で即座に解放します。", "‘독점 출력’으로 전환했습니다. ① 독점은 Windows 시스템 볼륨을 우회하므로 시스템 볼륨 슬라이더가 듣지 않습니다. 재생 바의 소프트 볼륨 슬라이더로 조절하세요. ② 재생 중 브라우저·회의 앱이 무음일 수 있으며 정지·일시정지 시 즉시 반납합니다.", "Переключено на монопольный вывод. ① Монопольный режим обходит системную громкость Windows, поэтому системный ползунок не действует — используйте ползунок программной громкости на панели воспроизведения. ② Другие программы могут быть без звука; остановка или пауза сразу освобождают устройство.", "Passage en sortie exclusive. ① L’exclusif contourne le volume système Windows : le curseur système n’agit plus, utilisez le curseur de volume logiciel de la barre de lecture. ② Les autres applications peuvent être muettes pendant la lecture ; l’arrêt ou la pause libère immédiatement l’appareil.", "Wechsel in den exklusiven Modus. ① Exklusiv umgeht die Windows-Systemlautstärke, der Systemregler wirkt nicht — nutzen Sie den Software-Lautstärkeregler in der Wiedergabeleiste. ② Andere Programme können während der Wiedergabe stumm sein; Stopp oder Pause geben das Gerät sofort frei"),
    ("exclusive_notice_keep", "继续使用独占", "繼續使用獨佔", "Keep exclusive", "排他のまま", "독점 유지", "Оставить монопольный", "Garder l’exclusif", "Exklusiv behalten"),
    ("exclusive_notice_back", "改回共享（推荐给会议/多任务用户）", "改回共享（推薦給會議/多工使用者）", "Switch back to shared (recommended for meetings/multitasking)", "共有に戻す（会議・マルチタスク向け）", "공유로 되돌리기(회의·멀티태스킹 권장)", "Вернуть общий режим (для встреч и многозадачности)", "Revenir au partagé (recommandé pour les réunions)", "Zurück zu geteilt (empfohlen für Meetings)"),
    ("dev_mode_strict_hint", "严格模式要求整数通路（int16/int32），开启后软件音量与 EQ 强制旁路", "嚴格模式要求整數通路（int16/int32），開啟後軟體音量與 EQ 強制旁路", "Strict mode requires an integer path (int16/int32); software volume and EQ are forced to bypass", "厳格モードは整数パス（int16/int32）必須。ソフト音量と EQ は強制バイパス", "엄격 모드는 정수 경로(int16/int32) 필요. 소프트 볼륨·EQ 강제 바이패스", "Строгий режим требует целочисленного пути (int16/int32); громкость и EQ принудительно в обход", "Le mode strict exige un chemin entier (int16/int32) ; volume logiciel et EQ forcés en contournement", "Strenger Modus erfordert Ganzzahlpfad (int16/int32); Software-Lautstärke und EQ zwangsweise umgangen"),
    ("dev_mode_strict_int_hint", "严格独占必须使用整数通路（float32 会被设备拒绝）", "嚴格獨佔必須使用整數通路（float32 會被裝置拒絕）", "Strict exclusive requires the integer path (float32 is rejected by the device)", "厳格排他は整数パス必須（float32 はデバイスに拒否されます）", "엄격 독점은 정수 경로 필수(float32는 장치가 거부)", "Строгий монопольный режим требует целочисленного пути (float32 устройство отклоняет)", "L’exclusif strict exige le chemin entier (float32 refusé par le périphérique)", "Streng exklusiv erfordert den Ganzzahlpfad (float32 wird abgelehnt)"),
    ("dev_hint_exclusive_wasapi_only", "仅 WASAPI 支持独占；当前后端下独占不可选", "僅 WASAPI 支援獨佔；目前後端下獨佔不可選", "Only WASAPI supports exclusive mode; unavailable on the current backend", "排他は WASAPI のみ。現在のバックエンドでは選択できません", "독점은 WASAPI만 지원. 현재 백엔드에서는 선택 불가", "Монопольный режим поддерживает только WASAPI; на текущем бэкенде недоступен", "Seul WASAPI gère l’exclusif ; indisponible sur le backend actuel", "Nur WASAPI unterstützt exklusiv; im aktuellen Backend nicht verfügbar"),
    ("dev_cap_title", "设备能力（零出声探测，本机实测）", "裝置能力（零出聲探測，本機實測）", "Device capabilities (silent probe, measured on this machine)", "デバイス能力（無音プローブ・本機実測）", "장치 능력(무음 프로브, 본机 실측)", "Возможности устройства (тихая проверка, замер на этой машине)", "Capacités du périphérique (sonde silencieuse, mesurées ici)", "Gerätefähigkeiten (stille Prüfung, hier gemessen)"),
    ("dev_cap_rate", "采样率", "取樣率", "Sample rate", "サンプルレート", "샘플레이트", "Частота", "Fréquence", "Abtastrate"),
    ("dev_cap_shared", "共享", "共享", "Shared", "共有", "공유", "Общий", "Partagé", "Geteilt"),
    ("dev_cap_exclusive", "独占", "獨佔", "Exclusive", "排他", "독점", "Монопольный", "Exclusif", "Exklusiv"),
    ("dev_cap_strict", "严格", "嚴格", "Strict", "厳格", "엄격", "Строгий", "Strict", "Streng"),
    ("dev_cap_unlocked", "本设备实测：独占可解锁 {rates}（共享模式下不可用）", "本裝置實測：獨佔可解鎖 {rates} Hz（共享模式下不可用）", "Measured on this device: exclusive unlocks {rates} (unavailable in shared mode)", "本機実測：排他で {rates} Hz が利用可能（共有では不可）", "본 장치 실측: 독점에서 {rates} Hz 사용 가능(공유 불가)", "Замер на этом устройстве: монопольный режим открывает {rates} Hz (в общем недоступно)", "Mesuré ici : l’exclusif débloque {rates} Hz (indisponible en partagé)", "Hier gemessen: exklusiv schaltet {rates} Hz frei (geteilt nicht verfügbar)"),
    ("dev_cap_none_unlocked", "本设备没有「仅独占可用」的采样率", "本裝置沒有「僅獨佔可用」的取樣率", "No rates are exclusive-only on this device", "このデバイスに排他専用レートはありません", "이 장치에 독점 전용 레이트 없음", "На этом устройстве нет частот, доступных только монопольно", "Aucune fréquence réservée à l’exclusif ici", "Keine nur-exklusiven Raten auf diesem Gerät"),
    ("dev_cap_not_probed", "尚未探测；点「重新探测此设备」查看能力矩阵", "尚未探測；點「重新探測此裝置」查看能力矩陣", "Not probed yet; click “Probe again” to see the matrix", "未プローブ。「再プローブ」で能力を表示", "아직 프로브 안 됨. ‘다시 프로브’로 능력 확인", "Ещё не проверено; нажмите «Проверить снова»", "Pas encore sondé ; cliquez sur « Sonder à nouveau »", "Noch nicht geprüft; „Erneut prüfen“ klicken"),
    ("dev_probe_again", "重新探测此设备", "重新探測此裝置", "Probe again", "再プローブ", "다시 프로브", "Проверить снова", "Sonder à nouveau", "Erneut prüfen"),
    ("dev_test_device", "测试此设备（播 1 秒静音）", "測試此裝置（播 1 秒靜音）", "Test device (1 s of silence)", "デバイスをテスト（1 秒無音）", "장치 테스트(1초 무음)", "Проверить устройство (1 с тишины)", "Tester le périphérique (1 s de silence)", "Gerät testen (1 s Stille)"),
    ("dev_testing", "正在测试…", "正在測試…", "Testing…", "テスト中…", "테스트 중…", "Проверка…", "Test en cours…", "Wird getestet…"),
    ("dev_test_ok", "测试通过：实际以 {rate} Hz（{mode}）打开", "測試通過：實際以 {rate} Hz（{mode}）開啟", "OK: opened at {rate} Hz ({mode})", "成功：{rate} Hz（{mode}）で開流", "성공: {rate} Hz({mode})로 열림", "Успешно: открыто на {rate} Hz ({mode})", "OK : ouvert à {rate} Hz ({mode})", "OK: mit {rate} Hz geöffnet ({mode})"),
    ("dev_test_failed", "测试失败：{reason}", "測試失敗：{reason}", "Failed: {reason}", "失敗：{reason}", "실패: {reason}", "Ошибка: {reason}", "Échec : {reason}", "Fehlgeschlagen: {reason}"),
    ("dev_no_output_device", "没有可用的输出设备", "沒有可用的輸出裝置", "No output device available", "利用可能な出力デバイスがありません", "사용 가능한 출력 장치 없음", "Нет доступного устройства вывода", "Aucun périphérique de sortie", "Kein Ausgabegerät verfügbar"),
    ("dev_hint_asio", "ASIO（需 ASIO 驱动）", "ASIO（需 ASIO 驅動）", "ASIO (requires ASIO driver)", "ASIO（ASIO ドライバーが必要）", "ASIO(ASIO 드라이버 필요)", "ASIO (нужен драйвер ASIO)", "ASIO (pilote ASIO requis)", "ASIO (ASIO-Treiber nötig)"),
    ("dev_hint_asio_missing", "当前 PortAudio 构建不含 ASIO；本版未启用（需替换 DLL，见 v1.7 说明）", "目前 PortAudio 組建不含 ASIO；本版未啟用（需替換 DLL，見 v1.7 說明）", "This PortAudio build has no ASIO; disabled in this version (DLL replacement needed, see v1.7 notes)", "この PortAudio ビルドに ASIO は含まれません（本版では無効。DLL 差し替えが必要）", "현재 PortAudio 빌드에 ASIO 없음(이 버전 비활성, DLL 교체 필요)", "В этой сборке PortAudio нет ASIO; в этой версии отключено (нужна замена DLL)", "Cette build de PortAudio n’inclut pas ASIO ; désactivé ici (remplacement de DLL requis)", "Diese PortAudio-Build enthält kein ASIO; in dieser Version deaktiviert (DLL-Austausch nötig)"),
    ("dev_hint_wdmks_experimental", "实验性后端，本机可能不可用；不参与独占与位完美话术", "實驗性後端，本機可能不可用；不參與獨佔與位完美話術", "Experimental backend; may be unavailable, and never used for exclusive claims", "実験的バックエンド。利用不可の場合あり。排他の説明には使用しません", "실험적 백엔드. 사용 불가할 수 있으며 독점 설명에 쓰지 않음", "Экспериментальный бэкенд; может быть недоступен и не используется в описании монопольного режима", "Backend expérimental ; peut être indisponible et jamais utilisé pour l’exclusif", "Experimentelles Backend; ggf. nicht verfügbar und nie für Exklusiv-Aussagen genutzt"),
    ("dev_hint_wdmks", "实验性后端，兼容性不保证", "實驗性後端，相容性不保證", "Experimental backend, compatibility not guaranteed", "実験的バックエンド（互換性非保証）", "실험적 백엔드, 호환성 미보장", "Экспериментальный бэкенд, совместимость не гарантируется", "Backend expérimental, compatibilité non garantie", "Experimentelles Backend, Kompatibilität nicht garantiert"),
    ("dev_hint_device_scoped", "留空表示使用系统默认设备。列表只显示当前后端的设备，换后端会自动刷新。", "留空表示使用系統預設設備。清單只顯示目前後端的設備，換後端會自動更新。", "Leave empty to use the system default device. Only devices of the current backend are listed; switching backends refreshes them.", "空欄ならシステム既定デバイスを使用します。一覧は現在のバックエンドのデバイスのみ表示し、切り替えると自動更新されます。", "비워 두면 시스템 기본 장치를 사용합니다. 목록에는 현재 백엔드의 장치만 표시되며 백엔드를 바꾸면 자동으로 갱신됩니다.", "Пустое значение — системное устройство по умолчанию. Список содержит только устройства текущего бэкенда и обновляется при его смене.", "Laissez vide pour utiliser le périphérique système par défaut. La liste ne montre que les périphériques du backend actuel et se rafraîchit lors du changement.", "Leer lassen verwendet das System-Standardgerät. Die Liste zeigt nur Geräte des aktuellen Backends und aktualisiert sich beim Wechsel automatisch."),
    ("dev_hint_multichannel", "⚠ 该设备在 WASAPI 下上报 {channels} 声道：{source} 声道音频可能直接开不了流（共享与独占都一样）。若播放失败，请把输出后端改为 MME 或 DirectSound，或换一台立体声设备。", "⚠ 該裝置在 WASAPI 下回報 {channels} 聲道：{source} 聲道音訊可能直接開不了流（共享與獨佔都一樣）。若播放失敗，請把輸出後端改為 MME 或 DirectSound，或換一台立體聲裝置。", "⚠ This device reports {channels} channels under WASAPI: {source}-channel audio may fail to open a stream at all (both shared and exclusive). If playback fails, switch the output backend to MME or DirectSound, or pick a stereo device.", "⚠ このデバイスは WASAPI で {channels} チャンネルを報告します。{source} チャンネルの音声はストリームを開けない場合があります（共有・排他とも）。再生できない場合は出力バックエンドを MME か DirectSound に変更するか、ステレオデバイスを選んでください。", "⚠ 이 장치는 WASAPI에서 {channels} 채널로 보고됩니다. {source} 채널 오디오는 스트림이 열리지 않을 수 있습니다(공유·독점 모두). 재생이 안 되면 출력 백엔드를 MME 또는 DirectSound로 바꾸거나 스테레오 장치를 선택하세요.", "⚠ Это устройство в WASAPI сообщает {channels} каналов: аудио с {source} каналами может вообще не открыть поток (и в общем, и в монопольном режиме). Если воспроизведение не запускается, переключите бэкенд на MME или DirectSound либо выберите стереоустройство.", "⚠ Ce périphérique déclare {channels} canaux sous WASAPI : l’audio en {source} canaux peut ne pas ouvrir de flux du tout (partagé comme exclusif). En cas d’échec, passez le backend de sortie à MME ou DirectSound, ou choisissez un périphérique stéréo.", "⚠ Dieses Gerät meldet unter WASAPI {channels} Kanäle: Audio mit {source} Kanälen öffnet möglicherweise gar keinen Stream (shared wie exklusiv). Wenn die Wiedergabe fehlschlägt, stellen Sie das Ausgabe-Backend auf MME oder DirectSound um oder wählen Sie ein Stereo-Gerät."),

    # ---- 无损/位完美相关措辞（措辞铁律 R2：只说「未做软件改动」）----
    ("play_lossless", "无损", "無損", "Lossless", "ロスレス", "무손실", "Lossless", "Lossless", "Lossless"),
    ("play_bit_perfect", "位完美", "位完美", "Bit-perfect mode", "ビットパーフェクト", "비트 퍼펙트", "Бит-в-бит", "Bit-perfect", "Bit-genau"),
    ("play_hires", "高解析", "高解析", "Hi-res", "ハイレゾ", "고해상도", "Hi-Res", "Haute résolution", "Hi-Res"),
    # ---- 关闭行为 / 托盘 / 迷你播放条（v1.7.1）----
    ("ui_close_action", "关闭窗口时", "關閉視窗時", "When closing the window", "ウィンドウを閉じるとき", "창을 닫을 때", "При закрытии окна", "À la fermeture de la fenêtre", "Beim Schließen des Fensters"),
    ("ui_close_tray", "收缩到托盘（音乐继续播放）", "收縮到托盤（音樂繼續播放）", "Minimize to tray (keep playing)", "トレイに格納（再生は続行）", "트레이로 최소화(재생 계속)", "Свернуть в трей (воспроизведение продолжится)", "Réduire dans la barre d’état (lecture poursuivie)", "In den Tray minimieren (Wiedergabe läuft weiter)"),
    ("ui_close_quit", "直接退出（停止播放）", "直接結束（停止播放）", "Quit (stop playback)", "終了する（再生停止）", "종료(재생 중지)", "Выйти (остановить воспроизведение)", "Quitter (arrêter la lecture)", "Beenden (Wiedergabe stoppen)"),
    ("ui_tray_show", "显示主窗口", "顯示主視窗", "Show main window", "メインウィンドウを表示", "주 창 표시", "Показать главное окно", "Afficher la fenêtre principale", "Hauptfenster anzeigen"),
    ("ui_tray_playpause", "播放 / 暂停", "播放 / 暫停", "Play / Pause", "再生 / 一時停止", "재생 / 일시정지", "Воспроизвести / Пауза", "Lecture / Pause", "Wiedergabe / Pause"),
    ("ui_tray_quit", "退出音序管家", "結束音序管家", "Quit SeqAudio Manager", "音序管家を終了", "音序管家 종료", "Выйти из SeqAudio Manager", "Quitter SeqAudio Manager", "SeqAudio Manager beenden"),
    ("ui_view_mini", "切换到迷你播放条", "切換到迷你播放條", "Switch to mini player", "ミニプレイヤーに切り替え", "미니 플레이어로 전환", "Переключиться на мини-плеер", "Passer au mini-lecteur", "Zum Mini-Player wechseln"),
    ("ui_view_main", "返回主界面", "返回主介面", "Back to main window", "メイン画面に戻る", "주 화면으로 돌아가기", "Вернуться в главное окно", "Revenir à la fenêtre principale", "Zurück zum Hauptfenster"),
    ("ui_mini_title", "迷你播放条", "迷你播放條", "Mini player", "ミニプレイヤー", "미니 플레이어", "Мини-плеер", "Mini-lecteur", "Mini-Player"),
    ("lyr_mini_empty", "暂无歌词", "暫無歌詞", "No lyrics", "歌詞がありません", "가사 없음", "Текста нет", "Aucune parole", "Kein Liedtext"),
    ("ui_playlist", "播放列表", "播放清單", "Playlists", "プレイリスト", "재생목록", "Плейлисты", "Listes de lecture", "Wiedergabelisten"),
    # ---- 迷你播放条（v1.7.2 重构：窗口三键 / 透明度 / 模式）----
    ("ui_mini_minimize", "最小化", "最小化", "Minimize", "最小化", "최소화", "Свернуть", "Réduire", "Minimieren"),
    ("ui_mini_maximize", "展开主界面", "展開主介面", "Expand to main window", "メイン画面に展開", "주 화면으로 펼치기", "Развернуть в главное окно", "Développer vers la fenêtre principale", "Zum Hauptfenster erweitern"),
    ("ui_mini_close", "关闭", "關閉", "Close", "閉じる", "닫기", "Закрыть", "Fermer", "Schließen"),
    ("ui_mini_opacity", "透明度", "透明度", "Opacity", "不透明度", "투명도", "Прозрачность", "Opacité", "Deckkraft"),
    ("ui_mini_volume", "音量", "音量", "Volume", "音量", "볼륨", "Громкость", "Volume", "Lautstärke"),
    ("ui_mini_prev", "上一曲", "上一曲", "Previous track", "前の曲", "이전 곡", "Предыдущий трек", "Piste précédente", "Vorheriger Titel"),
    ("ui_mini_next", "下一曲", "下一曲", "Next track", "次の曲", "다음 곡", "Следующий трек", "Piste suivante", "Nächster Titel"),
    ("ui_mini_play", "播放", "播放", "Play", "再生", "재생", "Воспроизвести", "Lecture", "Wiedergabe"),
    ("ui_mini_pause", "暂停", "暫停", "Pause", "一時停止", "일시정지", "Пауза", "Pause", "Pause"),
    ("ui_mini_stop", "停止", "停止", "Stop", "停止", "정지", "Стоп", "Arrêter", "Stopp"),
    ("ui_mini_mode", "播放模式", "播放模式", "Play mode", "再生モード", "재생 모드", "Режим воспроизведения", "Mode de lecture", "Wiedergabemodus"),
    ("ui_mini_mode_sequential", "顺序播放", "順序播放", "Sequential", "順次再生", "순차 재생", "По порядку", "Lecture séquentielle", "Der Reihe nach"),
    ("ui_mini_mode_repeat_one", "单曲循环", "單曲循環", "Repeat one", "1曲リピート", "한 곡 반복", "Повтор одной", "Répéter le titre", "Titel wiederholen"),
    ("ui_mini_mode_repeat_all", "列表循环", "列表循環", "Repeat all", "全曲リピート", "전체 반복", "Повтор списка", "Répéter la liste", "Liste wiederholen"),
    ("ui_mini_mode_shuffle", "随机播放", "隨機播放", "Shuffle", "シャッフル", "셔플", "Перемешать", "Aléatoire", "Zufällig"),
    ("ui_restored_from_tray", "已回到主窗口，播放未中断", "已回到主視窗，播放未中斷", "Back to main window; playback was never interrupted", "メイン画面に復帰しました。再生は継続中です", "주 창으로 돌아왔습니다. 재생은 중단되지 않았습니다", "Возврат в главное окно; воспроизведение не прерывалось", "Retour à la fenêtre principale ; lecture jamais interrompue", "Zurück im Hauptfenster; Wiedergabe lief ununterbrochen"),

    # ---- ASIO / DoP / 热插拔（v1.8.1）----
    # 这几条的文案是**可被断言的事实陈述**，不是营销话术：ASIO 项置灰时说的
    # 是「SD_ENABLE_ASIO 没生效」——那正是用户唯一能动手修的地方（重启程序），
    # 而不是含糊的「不可用」。
    ("dev_asio_unavailable", "当前 PortAudio 未启用 ASIO（SD_ENABLE_ASIO 未生效）", "目前 PortAudio 未啟用 ASIO（SD_ENABLE_ASIO 未生效）", "ASIO is not enabled in this PortAudio build (SD_ENABLE_ASIO not in effect)", "この PortAudio では ASIO が有効になっていません（SD_ENABLE_ASIO が未適用）", "현재 PortAudio에서 ASIO가 활성화되지 않았습니다(SD_ENABLE_ASIO 미적용)", "В этой сборке PortAudio ASIO не включён (SD_ENABLE_ASIO не сработал)", "ASIO n’est pas activé dans ce PortAudio (SD_ENABLE_ASIO sans effet)", "ASIO ist in diesem PortAudio nicht aktiv (SD_ENABLE_ASIO ohne Wirkung)"),
    ("dev_asio_switched_off", "本次以 --no-asio 启动，ASIO 已关闭；去掉该参数重启即可启用", "本次以 --no-asio 啟動，ASIO 已關閉；移除該參數重啟即可啟用", "Started with --no-asio: ASIO is off. Restart without that flag to enable it", "--no-asio 付きで起動したため ASIO は無効です。この引数を外して再起動すると有効になります", "--no-asio 로 실행되어 ASIO가 꺼져 있습니다. 이 인수를 빼고 다시 실행하면 켜집니다", "Запуск с --no-asio: ASIO отключён. Уберите этот флаг и перезапустите", "Démarré avec --no-asio : ASIO est désactivé. Relancez sans ce paramètre pour l’activer", "Mit --no-asio gestartet: ASIO ist aus. Ohne diesen Schalter neu starten, um es zu aktivieren"),
    ("dev_asio_load_failed", "ASIO 驱动库加载失败（安装包可能缺 libportaudio64bit-asio.dll），本次运行已自动改用普通音频构建；WASAPI / MME 不受影响", "ASIO 驅動庫載入失敗（安裝包可能缺少 libportaudio64bit-asio.dll），本次執行已自動改用一般音訊組建；WASAPI / MME 不受影響", "The ASIO driver library failed to load (the package may lack libportaudio64bit-asio.dll). This run uses the plain audio build; WASAPI / MME are unaffected", "ASIO ドライバーライブラリの読み込みに失敗しました（libportaudio64bit-asio.dll が同梱されていない可能性）。今回は通常のオーディオビルドで動作し、WASAPI / MME は影響を受けません", "ASIO 드라이버 라이브러리를 불러오지 못했습니다(패키지에 libportaudio64bit-asio.dll 누락 가능). 이번 실행은 일반 오디오 빌드를 사용하며 WASAPI / MME는 영향 없습니다", "Не удалось загрузить библиотеку драйвера ASIO (возможно, в пакете нет libportaudio64bit-asio.dll). Запуск идёт на обычной сборке; WASAPI / MME работают", "Échec du chargement de la bibliothèque ASIO (le paquet n’inclut peut-être pas libportaudio64bit-asio.dll). Cette session utilise la version audio standard ; WASAPI / MME ne sont pas affectés", "ASIO-Treiberbibliothek konnte nicht geladen werden (im Paket fehlt evtl. libportaudio64bit-asio.dll). Dieser Lauf nutzt die normale Audio-Version; WASAPI / MME sind nicht betroffen"),
    ("dev_asio_probe_failed", "检测到 ASIO 驱动，但没有可通过探测的 ASIO 设备（可能被其它程序独占）；关闭占用它的程序后会自动恢复", "偵測到 ASIO 驅動，但沒有可通過探測的 ASIO 裝置（可能被其它程式獨佔）；關閉佔用它的程式後會自動恢復", "An ASIO driver was found, but no ASIO device passed the probe (it may be held by another program); it recovers automatically once that program is closed", "ASIO ドライバーは検出されましたが、プローブを通過する ASIO デバイスがありません（他のプログラムが占有中の可能性）。占有を解除すると自動的に復帰します", "ASIO 드라이버는 감지되었으나 프로브를 통과하는 ASIO 장치가 없습니다(다른 프로그램이 점유 중일 수 있음). 점유를 해제하면 자동 복구됩니다", "Обнаружен драйвер ASIO, но ни одно устройство не прошло проверку (возможно, занято другой программой); восстановится автоматически после её закрытия", "Un pilote ASIO a été détecté, mais aucun périphérique ASIO n’a passé la vérification (peut-être occupé par un autre programme) ; récupération automatique une fois fermé", "Ein ASIO-Treiber wurde gefunden, aber kein ASIO-Gerät hat die Prüfung bestanden (evtl. von einem anderen Programm belegt); erholt sich nach dem Schließen automatisch"),
    ("dev_asio_mode_note", "ASIO 驱动独占设备：输出模式固定为「独占」，无需选择三档", "ASIO 驅動獨佔裝置：輸出模式固定為「獨佔」，無需選擇三檔", "ASIO drivers own the device: the mode is always exclusive, no three-way choice", "ASIO ドライバーはデバイスを占有します。出力モードは常に排他で、三択は不要です", "ASIO 드라이버는 장치를 독점합니다. 출력 모드는 항상 독점이며 세 가지 선택이 필요 없습니다", "Драйвер ASIO монопольно владеет устройством: режим всегда монопольный, выбор из трёх не нужен", "Les pilotes ASIO possèdent l’appareil : le mode est toujours exclusif, pas de choix à trois", "ASIO-Treiber besitzen das Gerät: Modus immer exklusiv, keine Drei-Wege-Wahl"),
    ("dev_asio_detected", "已检测到 ASIO 驱动（{driver}）。未自动切换后端，需要时请在「参数 → 输出后端」里选 ASIO", "已偵測到 ASIO 驅動（{driver}）。未自動切換後端，需要時請在「參數 → 輸出後端」選擇 ASIO", "ASIO driver detected ({driver}). The backend was not switched automatically — pick ASIO under Options → Output backend if you want it", "ASIO ドライバーを検出しました（{driver}）。バックエンドは自動変更していません。必要なら「設定 → 出力バックエンド」で ASIO を選んでください", "ASIO 드라이버를 감지했습니다({driver}). 백엔드는 자동 전환하지 않았습니다. 필요하면 ‘옵션 → 출력 백엔드’에서 ASIO를 선택하세요", "Обнаружен драйвер ASIO ({driver}). Бэкенд не переключён автоматически — при необходимости выберите ASIO в «Параметры → Бэкенд вывода»", "Pilote ASIO détecté ({driver}). Le backend n’a pas été changé automatiquement — choisissez ASIO dans « Options → Backend de sortie » si besoin", "ASIO-Treiber erkannt ({driver}). Das Backend wurde nicht automatisch umgestellt – bei Bedarf unter „Optionen → Ausgabe-Backend“ ASIO wählen"),
    ("dev_dop_switch", "DSD 直出（DoP，需设备支持 176.4/352.8 kHz 载体）", "DSD 直出（DoP，需裝置支援 176.4/352.8 kHz 載波）", "Direct DSD (DoP; needs a 176.4/352.8 kHz carrier)", "DSD ダイレクト出力（DoP。176.4/352.8 kHz のキャリアが必要）", "DSD 직접 출력(DoP, 176.4/352.8 kHz 캐리어 필요)", "Прямой DSD (DoP; нужен носитель 176,4/352,8 кГц)", "DSD direct (DoP ; nécessite une porteuse 176,4/352,8 kHz)", "Direktes DSD (DoP; benötigt 176,4/352,8 kHz Träger)"),
    ("dev_dop_volume_note", "音量由驱动控制（DoP 期间软件音量与 EQ 已旁路）", "音量由驅動控制（DoP 期間軟體音量與 EQ 已旁路）", "Volume is controlled by the driver (software volume and EQ are bypassed during DoP)", "音量はドライバーが制御します（DoP 中はソフト音量と EQ をバイパス）", "볼륨은 드라이버가 제어합니다(DoP 중 소프트 볼륨과 EQ는 바이패스)", "Громкостью управляет драйвер (во время DoP программная громкость и EQ обходятся)", "Le volume est contrôlé par le pilote (volume logiciel et EQ contournés pendant DoP)", "Lautstärke steuert der Treiber (während DoP sind Software-Lautstärke und EQ umgangen)"),
    ("dev_dop_fallback", "载体率不可用或直出未启用时自动回退 PCM，并在信号路径标注（不静默降级）", "載波率不可用或直出未啟用時自動回退 PCM，並在訊號路徑標註（不靜默降級）", "Falls back to PCM when the carrier rate is unavailable or direct output is off — always noted in the signal path", "キャリアレートが使えない、または直接出力が無効なときは PCM にフォールバックし、信号経路に明記します", "캐리어 레이트를 쓸 수 없거나 직접 출력이 꺼져 있으면 PCM으로 대체하며 신호 경로에 표시합니다", "При недоступной частоте носителя или выключенном прямом выводе — откат на PCM с пометкой в пути сигнала", "Repli sur PCM si la porteuse est indisponible ou la sortie directe désactivée, toujours signalé dans le chemin du signal", "Rückfall auf PCM, wenn die Trägerrate fehlt oder Direktausgabe aus ist – stets im Signalweg vermerkt"),
    ("dev_lost_notice", "输出设备已断开，已回落系统默认设备", "輸出裝置已斷開，已回落系統預設裝置", "The output device was disconnected; falling back to the system default device", "出力デバイスが切断されました。システム既定のデバイスに戻します", "출력 장치가 끊겼습니다. 시스템 기본 장치로 대체합니다", "Устройство вывода отключено — переключаюсь на системное по умолчанию", "Périphérique de sortie déconnecté ; retour au périphérique système par défaut", "Ausgabegerät getrennt; Rückfall auf das System-Standardgerät"),
    ("decode_dop_detail", "DSD{dsd} → DoP{dop} @{rate} Hz（{ch} ch）", "DSD{dsd} → DoP{dop} @{rate} Hz（{ch} ch）", "DSD{dsd} → DoP{dop} @{rate} Hz ({ch} ch)", "DSD{dsd} → DoP{dop} @{rate} Hz（{ch} ch）", "DSD{dsd} → DoP{dop} @{rate} Hz({ch} ch)", "DSD{dsd} → DoP{dop} @{rate} Гц ({ch} кан.)", "DSD{dsd} → DoP{dop} @{rate} Hz ({ch} canaux)", "DSD{dsd} → DoP{dop} @{rate} Hz ({ch} Kanäle)"),
    ("status_badge_dsd", "DSD", "DSD", "DSD", "DSD", "DSD", "DSD", "DSD", "DSD"),
    ("status_badge_dsd_pcm", "DSD→PCM", "DSD→PCM", "DSD→PCM", "DSD→PCM", "DSD→PCM", "DSD→PCM", "DSD→PCM", "DSD→PCM"),
)

for _row in _PLAYBACK_ROWS:
    _TRANSLATIONS[_row[0]] = dict(zip(LOCALE_NAMES, _row[1:]))



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
    # 两种登记方式都要支持：v1.6 用「简体中文原文」当键（界面里直接写中文），
    # v1.7 起新词条用显式 key（`sig_l1_bypass_mixer` 这类，中文里带占位符的
    # 长句当 key 太脆）。先按键查，查不到再按原文查。
    text = tr(template) if template in _TRANSLATIONS else tr_text(template)
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

