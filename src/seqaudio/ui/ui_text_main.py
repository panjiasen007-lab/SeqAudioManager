"""主界面 / 设置 / 检查器 / 表格的 8 语言文案。

行格式：简体中文原文、繁體中文、English、日本語、한국어、Русский、Français、Deutsch。
"""

from __future__ import annotations

LANGS = ("zh_CN", "zh_TW", "en", "ja", "ko", "ru", "fr", "de")

ROWS: list[tuple[str, str, str, str, str, str, str, str]] = [
    # 窗口 / 品牌 / 状态
    ("音序管家 · 音乐元数据批量处理", "音序管家 · 音樂中繼資料批次處理", "SeqAudio Manager · Music Metadata Batch Processing", "SeqAudio Manager · 音楽メタデータ一括処理", "음서관리 · 음악 메타데이터 일괄 처리", "SeqAudio Manager · пакетная обработка метаданных", "SeqAudio Manager · traitement par lots des métadonnées", "SeqAudio Manager · Metadaten-Stapelverarbeitung"),
    ("音序管家", "音序管家", "SeqAudio Manager", "SeqAudio Manager", "음서관리", "SeqAudio Manager", "SeqAudio Manager", "SeqAudio Manager"),
    ("就绪", "就緒", "Ready", "準備完了", "준비됨", "Готово", "Prêt", "Bereit"),
    ("正在扫描…", "正在掃描…", "Scanning…", "スキャン中…", "스캔 중…", "Сканирование…", "Analyse…", "Scannen…"),
    ("正在扫描… 已发现 {n} 个音频", "正在掃描… 已發現 {n} 個音訊", "Scanning… {n} audio files found", "スキャン中… {n} 件の音声を検出", "스캔 중… 오디오 {n}개 발견", "Сканирование… найдено аудиофайлов: {n}", "Analyse… {n} fichiers audio trouvés", "Scannen… {n} Audiodateien gefunden"),
    ("导入失败：{message}", "匯入失敗：{message}", "Import failed: {message}", "読み込み失敗：{message}", "가져오기 실패: {message}", "Ошибка импорта: {message}", "Échec de l’import : {message}", "Import fehlgeschlagen: {message}"),
    ("没有找到可导入的音频文件", "找不到可匯入的音訊檔", "No importable audio files found", "読み込み可能な音声ファイルがありません", "가져올 수 있는 오디오 파일이 없습니다", "Не найдено аудиофайлов для импорта", "Aucun fichier audio importable", "Keine importierbaren Audiodateien gefunden"),
    ("正在重新读取原始标签…", "正在重新讀取原始標籤…", "Re-reading original tags…", "元のタグを再読み込み中…", "원본 태그를 다시 읽는 중…", "Повторное чтение исходных тегов…", "Relecture des étiquettes d’origine…", "Original-Tags werden neu gelesen…"),
    ("已恢复 {updated} 个文件的原始标签", "已還原 {updated} 個檔案的原始標籤", "Restored original tags for {updated} files", "{updated} 件のファイルの元タグを復元しました", "{updated}개 파일의 원본 태그를 복원했습니다", "Исходные теги восстановлены для файлов: {updated}", "Étiquettes d’origine restaurées pour {updated} fichiers", "Original-Tags für {updated} Dateien wiederhergestellt"),
    ("正在停止…", "正在停止…", "Stopping…", "停止中…", "중지 중…", "Остановка…", "Arrêt…", "Wird gestoppt…"),
    ("处理中 {index} / {total}", "處理中 {index} / {total}", "Processing {index} / {total}", "処理中 {index} / {total}", "처리 중 {index} / {total}", "Обработка {index} / {total}", "Traitement {index} / {total}", "Verarbeitung {index} / {total}"),
    ("写入标签 {index} / {total}", "寫入標籤 {index} / {total}", "Writing tags {index} / {total}", "タグ書き込み {index} / {total}", "태그 쓰는 중 {index} / {total}", "Запись тегов {index} / {total}", "Écriture des étiquettes {index} / {total}", "Tags schreiben {index} / {total}"),
    ("生成文件夹封面 {index} / {total}", "產生資料夾封面 {index} / {total}", "Generating folder covers {index} / {total}", "フォルダーカバー生成 {index} / {total}", "폴더 표지 생성 {index} / {total}", "Создание обложек папок {index} / {total}", "Génération des pochettes de dossier {index} / {total}", "Ordnercover erzeugen {index} / {total}"),
    ("已撤销 {count} 首的标签编辑", "已復原 {count} 首的標籤編輯", "Undid tag edits on {count} tracks", "{count} 曲のタグ編集を元に戻しました", "{count}곡의 태그 편집을 되돌렸습니다", "Изменения тегов отменены для треков: {count}", "Modifications d’étiquettes annulées pour {count} pistes", "Tag-Änderungen für {count} Titel rückgängig gemacht"),
    ("已复制路径：{name}", "已複製路徑：{name}", "Path copied: {name}", "パスをコピー：{name}", "경로 복사됨: {name}", "Путь скопирован: {name}", "Chemin copié : {name}", "Pfad kopiert: {name}"),
    ("已选 {count} 首", "已選 {count} 首", "{count} selected", "{count} 曲を選択", "{count}곡 선택", "Выбрано: {count}", "{count} sélectionné(s)", "{count} ausgewählt"),
    ("去序号：{count} 个标题已更新", "去序號：{count} 個標題已更新", "Track numbers removed: {count} titles updated", "曲番号を削除：{count} 件のタイトルを更新", "곡 번호 제거: {count}개 제목 업데이트", "Номера удалены: обновлено заголовков: {count}", "Numéros retirés : {count} titres mis à jour", "Titelnummern entfernt: {count} Titel aktualisiert"),
    ("鉴定中 {index} / {total}", "鑑定中 {index} / {total}", "Analysing {index} / {total}", "判定中 {index} / {total}", "검사 중 {index} / {total}", "Анализ {index} / {total}", "Analyse {index} / {total}", "Analyse {index} / {total}"),
    ("分轨导出 {index} / {total}", "分軌匯出 {index} / {total}", "Splitting {index} / {total}", "トラック分割 {index} / {total}", "트랙 분할 {index} / {total}", "Разделение {index} / {total}", "Séparation {index} / {total}", "Trennen {index} / {total}"),
    ("字体已预览，点击「保存参数」后生效", "字型已預覽，點「儲存參數」後生效", "Font previewed; click Save Options to apply", "フォントをプレビュー中。「設定を保存」で適用", "글꼴 미리보기 중. ‘설정 저장’을 누르면 적용됩니다", "Шрифт предварительно показан; нажмите «Сохранить», чтобы применить", "Police prévisualisée ; cliquez sur Enregistrer pour appliquer", "Schriftart als Vorschau; zum Anwenden „Optionen speichern“ klicken"),
    ("参数已修改，点击右侧「保存参数」才会生效", "參數已修改，點右側「儲存參數」才會生效", "Options changed; click Save Options on the right to apply", "設定を変更しました。右側の「設定を保存」で適用されます", "설정이 변경되었습니다. 오른쪽 ‘설정 저장’을 눌러 적용하세요", "Параметры изменены; нажмите «Сохранить» справа", "Options modifiées ; cliquez sur Enregistrer à droite", "Optionen geändert; rechts auf „Optionen speichern“ klicken"),
    ("参数已保存", "參數已儲存", "Options saved", "設定を保存しました", "설정이 저장되었습니다", "Параметры сохранены", "Options enregistrées", "Optionen gespeichert"),
    ("拖入音频文件，或点击「添加文件夹」", "拖入音訊檔，或點「加入資料夾」", "Drop audio files here, or click Add Folder", "音声ファイルをドロップするか、「フォルダー追加」をクリック", "오디오 파일을 끌어오거나 ‘폴더 추가’를 클릭하세요", "Перетащите аудиофайлы или нажмите «Добавить папку»", "Déposez des fichiers audio ou cliquez sur Ajouter un dossier", "Audiodateien hierher ziehen oder „Ordner hinzufügen“ klicken"),
    ("参数面板有未保存的修改，退出前要保存吗？", "參數面板有未儲存的修改，退出前要儲存嗎？", "The options panel has unsaved changes. Save before exiting?", "設定パネルに未保存の変更があります。終了前に保存しますか？", "설정 패널에 저장되지 않은 변경이 있습니다. 종료 전에 저장할까요?", "В панели параметров есть несохранённые изменения. Сохранить перед выходом?", "Le panneau d’options contient des modifications non enregistrées. Enregistrer avant de quitter ?", "Im Optionsbereich gibt es ungespeicherte Änderungen. Vor dem Beenden speichern?"),
    ("界面语言已切换：{language}", "介面語言已切換：{language}", "Interface language changed: {language}", "表示言語を変更しました：{language}", "인터페이스 언어 변경: {language}", "Язык интерфейса изменён: {language}", "Langue de l’interface changée : {language}", "Oberflächensprache geändert: {language}"),

    # 工具栏 tooltip
    ("清空当前列表（不删除磁盘文件）", "清空目前清單（不刪除磁碟檔案）", "Clear the current list (disk files are not deleted)", "現在のリストを消去（ディスク上のファイルは削除しません）", "현재 목록 지우기 (디스크 파일은 삭제하지 않음)", "Очистить список (файлы на диске не удаляются)", "Vider la liste actuelle (les fichiers ne sont pas supprimés)", "Aktuelle Liste leeren (Dateien auf der Festplatte bleiben erhalten)"),
    ("匹配 → 补全 → 清理水印 → 写标签 → 歌词（按当前参数）", "比對 → 補全 → 清理浮水印 → 寫標籤 → 歌詞（依目前參數）", "Match → Fill → Clean watermarks → Write tags → Lyrics (current options)", "マッチ → 補完 → 透かし除去 → タグ書き込み → 歌詞（現在の設定）", "매치 → 채우기 → 워터마크 정리 → 태그 쓰기 → 가사 (현재 설정)", "Сопоставление → заполнение → очистка водяных знаков → теги → текст песни (текущие параметры)", "Correspondance → compléter → nettoyer les filigranes → écrire les étiquettes → paroles (options actuelles)", "Abgleich → ergänzen → Wasserzeichen entfernen → Tags schreiben → Songtext (aktuelle Optionen)"),
    ("把列表里所有未写入的标签变更确认并保存到磁盘（会二次确认）", "把清單裡所有未寫入的標籤變更確認並儲存到磁碟（會再次確認）", "Confirm and write all pending tag changes to disk (asks again)", "未書き込みのタグ変更を確認してディスクに保存（再確認あり）", "목록의 미저장 태그 변경을 확인해 디스크에 저장 (다시 확인)", "Подтвердить и записать все несохранённые изменения тегов на диск (с повторным подтверждением)", "Confirmer et écrire toutes les modifications en attente sur le disque (nouvelle confirmation)", "Alle ausstehenden Tag-Änderungen bestätigen und speichern (mit erneuter Rückfrage)"),
    ("不联网：清理水印、补全空白字段、写标签、重命名", "不連網：清理浮水印、補全空白欄位、寫標籤、重新命名", "Offline: clean watermarks, fill empty fields, write tags, rename", "オフライン：透かし除去、空欄補完、タグ書き込み、名前変更", "오프라인: 워터마크 정리, 빈 필드 채우기, 태그 쓰기, 이름 변경", "Без сети: очистка водяных знаков, заполнение пустых полей, теги, переименование", "Hors ligne : nettoyer les filigranes, remplir les champs vides, écrire les étiquettes, renommer", "Offline: Wasserzeichen entfernen, leere Felder füllen, Tags schreiben, umbenennen"),
    ("频谱分析判定是否真无损（需解码，速度约 0.3~1 秒/首）", "頻譜分析判定是否真無損（需解碼，速度約 0.3~1 秒/首）", "Spectrum analysis for lossless authenticity (decodes; about 0.3–1 s per track)", "スペクトル分析でロスレス判定（デコードが必要、約 0.3～1 秒/曲）", "스펙트럼 분석으로 무손실 판정 (디코딩 필요, 곡당 약 0.3~1초)", "Спектральный анализ подлинности lossless (с декодированием, около 0,3–1 с на трек)", "Analyse spectrale de l’authenticité lossless (décodage ; environ 0,3 à 1 s par piste)", "Spektralanalyse der Lossless-Echtheit (mit Dekodierung, ca. 0,3–1 s pro Titel)"),
    ("把判定为假无损/可疑的文件移入回收站或隔离区（Ctrl+Shift+D）", "把判定為假無損/可疑的檔案移入回收站或隔離區（Ctrl+Shift+D）", "Move fake/suspicious files to the recycle bin or quarantine (Ctrl+Shift+D)", "偽ロスレス/疑わしいファイルをごみ箱または隔離所へ移動（Ctrl+Shift+D）", "가짜/의심 파일을 휴지통 또는 격리소로 이동 (Ctrl+Shift+D)", "Переместить фейковые/подозрительные файлы в корзину или карантин (Ctrl+Shift+D)", "Déplacer les fichiers faux/suspects vers la corbeille ou la quarantaine (Ctrl+Shift+D)", "Fake-/verdächtige Dateien in Papierkorb oder Quarantäne verschieben (Ctrl+Shift+D)"),
    ("重命名预览、导出报表、隔离区、关于", "重新命名預覽、匯出報表、隔離區、關於", "Rename preview, export report, quarantine, about", "名前変更プレビュー、レポート出力、隔離所、情報", "이름 변경 미리보기, 보고서 내보내기, 격리소, 정보", "Предпросмотр переименования, экспорт отчёта, карантин, о программе", "Aperçu du renommage, exporter le rapport, quarantaine, à propos", "Umbenennungsvorschau, Bericht exportieren, Quarantäne, Info"),
    ("显示 / 隐藏右侧参数面板（Ctrl+P）", "顯示 / 隱藏右側參數面板（Ctrl+P）", "Show / hide the options panel (Ctrl+P)", "右側の設定パネルを表示 / 非表示（Ctrl+P）", "오른쪽 설정 패널 표시 / 숨기기 (Ctrl+P)", "Показать / скрыть панель параметров (Ctrl+P)", "Afficher / masquer le panneau d’options (Ctrl+P)", "Optionsbereich rechts ein-/ausblenden (Ctrl+P)"),

    # 页签 / 设置分组
    ("详情", "詳情", "Details", "詳細", "상세", "Детали", "Détails", "Details"),
    ("参数", "參數", "Options", "設定", "설정", "Параметры", "Options", "Optionen"),
    ("匹配", "比對", "Matching", "マッチング", "매칭", "Сопоставление", "Correspondance", "Abgleich"),
    ("歌词", "歌詞", "Lyrics", "歌詞", "가사", "Текст песни", "Paroles", "Songtext"),
    ("写入", "寫入", "Writing", "書き込み", "쓰기", "Запись", "Écriture", "Schreiben"),
    ("外观", "外觀", "Appearance", "外観", "모양", "Оформление", "Apparence", "Darstellung"),
    ("字体与外观", "字型與外觀", "Fonts & Appearance", "フォントと外観", "글꼴 및 모양", "Шрифты и оформление", "Polices et apparence", "Schrift & Darstellung"),
    ("随包开源字体", "隨包開源字型", "Bundled open-source fonts", "同梱のオープンソースフォント", "내장 오픈소스 글꼴", "Встроенные шрифты", "Polices open source incluses", "Mitgelieferte Open-Source-Schriften"),
    ("界面字体", "介面字型", "Interface font", "UI フォント", "인터페이스 글꼴", "Шрифт интерфейса", "Police de l’interface", "Oberflächenschrift"),
    ("数据字体", "資料字型", "Data font", "データフォント", "데이터 글꼴", "Шрифт данных", "Police des données", "Daten-Schrift"),
    ("字号", "字級", "Font size", "文字サイズ", "글자 크기", "Размер шрифта", "Taille de police", "Schriftgröße"),
    ("界面与清理", "介面與清理", "Interface & Cleanup", "UI とクリーンアップ", "인터페이스 및 정리", "Интерфейс и очистка", "Interface et nettoyage", "Oberfläche & Bereinigung"),
    ("写入与备份", "寫入與備份", "Writing & Backup", "書き込みとバックアップ", "쓰기 및 백업", "Запись и резервные копии", "Écriture et sauvegarde", "Schreiben & Sicherung"),
    ("扫描与过滤", "掃描與過濾", "Scanning & Filtering", "スキャンとフィルター", "스캔 및 필터", "Сканирование и фильтры", "Analyse et filtres", "Scannen & Filtern"),
    ("匹配与覆盖", "比對與覆蓋", "Matching & Overrides", "マッチングと上書き", "매칭 및 덮어쓰기", "Сопоставление и перезапись", "Correspondance et écrasement", "Abgleich & Überschreiben"),
    ("联网数据源", "連網資料來源", "Online sources", "オンラインソース", "온라인 소스", "Сетевые источники", "Sources en ligne", "Online-Quellen"),
    ("自定义屏蔽词（每行一个）", "自訂封鎖詞（每行一個）", "Custom blocked words (one per line)", "カスタム除外語（1 行に 1 つ）", "사용자 차단 단어 (한 줄에 하나)", "Пользовательские стоп-слова (по одному в строке)", "Mots masqués personnalisés (un par ligne)", "Eigene Sperrwörter (eines pro Zeile)"),
    ("批量重命名", "批次重新命名", "Batch rename", "一括名前変更", "일괄 이름 변경", "Пакетное переименование", "Renommage par lots", "Stapel-Umbenennung"),
    ("变量：{歌手} {歌名} {专辑} {年份} {流派} {曲目} {碟号} {格式}", "變數：{歌手} {歌名} {專輯} {年份} {流派} {曲目} {碟號} {格式}", "Variables: {artist} {title} {album} {year} {genre} {track} {disc} {format}", "変数：{アーティスト} {曲名} {アルバム} {年} {ジャンル} {曲番号} {ディスク} {形式}", "변수: {아티스트} {곡명} {앨범} {연도} {장르} {트랙} {디스크} {형식}", "Переменные: {исполнитель} {название} {альбом} {год} {жанр} {трек} {диск} {формат}", "Variables : {artiste} {titre} {album} {année} {genre} {piste} {disque} {format}", "Variablen: {Künstler} {Titel} {Album} {Jahr} {Genre} {Titelnummer} {Disc} {Format}"),
    ("补零 {曲目:02d}，用 / 建立子文件夹", "補零 {曲目:02d}，用 / 建立子資料夾", "Zero-pad with {track:02d}; use / to create subfolders", "{曲目:02d} でゼロ埋め、/ でサブフォルダー作成", "{트랙:02d}로 0 채움, /로 하위 폴더 생성", "Дополнение нулями: {трек:02d}; / создаёт подпапки", "Compléter avec {piste:02d} ; / crée des sous-dossiers", "Nullauffüllung: {Titelnummer:02d}; / erzeugt Unterordner"),
    ("最短时长", "最短時長", "Minimum duration", "最短時間", "최소 길이", "Мин. длительность", "Durée minimale", "Mindestdauer"),
    ("输出方式", "輸出方式", "Output mode", "出力方法", "출력 방식", "Способ вывода", "Mode de sortie", "Ausgabemodus"),
    ("自动剔除歌词内广告与水印", "自動剔除歌詞內廣告與浮水印", "Strip ads and watermarks from lyrics", "歌詞内の広告と透かしを自動除去", "가사 내 광고와 워터마크 자동 제거", "Автоудаление рекламы и водяных знаков из текста", "Supprimer les publicités et filigranes des paroles", "Werbung und Wasserzeichen aus Songtexten entfernen"),
    ("已有标签", "現有標籤", "Existing tags", "既存タグ", "기존 태그", "Существующие теги", "Étiquettes existantes", "Vorhandene Tags"),
    ("置信度阈值", "置信度閾值", "Confidence threshold", "信頼度しきい値", "신뢰도 임계값", "Порог уверенности", "Seuil de confiance", "Konfidenzschwelle"),
    ("用 CUE 信息补全整轨标签", "用 CUE 資訊補全整軌標籤", "Fill whole-disc tags from CUE", "CUE 情報で全体タグを補完", "CUE 정보로 전체 트랙 태그 채우기", "Заполнять теги целого трека из CUE", "Compléter les étiquettes du disque avec le CUE", "Ganzscheiben-Tags aus CUE ergänzen"),
    ("并发", "並行數", "Concurrency", "並列数", "동시 처리", "Параллельность", "Parallélisme", "Parallelität"),
    ("超时", "逾時", "Timeout", "タイムアウト", "시간 초과", "Тайм-аут", "Délai d’attente", "Zeitlimit"),
    ("代理", "代理", "Proxy", "プロキシ", "프록시", "Прокси", "Proxy", "Proxy"),
    ("留空使用系统代理", "留空使用系統代理", "Leave empty to use the system proxy", "空欄ならシステムプロキシを使用", "비워 두면 시스템 프록시 사용", "Пусто — использовать системный прокси", "Laisser vide pour le proxy système", "Leer lassen für System-Proxy"),
    ("还原", "還原", "Revert", "元に戻す", "되돌리기", "Сбросить", "Rétablir", "Zurücksetzen"),
    ("放弃本次未保存的修改，恢复为已保存的参数", "放棄本次未儲存的修改，還原為已儲存的參數", "Discard unsaved changes and restore saved options", "未保存の変更を破棄し、保存済み設定に戻す", "저장되지 않은 변경을 버리고 저장된 설정으로 복원", "Отменить несохранённые изменения и вернуть сохранённые параметры", "Abandonner les modifications non enregistrées et restaurer les options enregistrées", "Ungespeicherte Änderungen verwerfen und gespeicherte Optionen wiederherstellen"),
    ("保存参数", "儲存參數", "Save Options", "設定を保存", "설정 저장", "Сохранить параметры", "Enregistrer", "Optionen speichern"),
    ("把当前参数写入并持久化（Ctrl+S）", "把目前參數寫入並持久化（Ctrl+S）", "Write and persist the current options (Ctrl+S)", "現在の設定を保存して永続化（Ctrl+S）", "현재 설정을 저장하고 유지 (Ctrl+S)", "Сохранить текущие параметры (Ctrl+S)", "Enregistrer et conserver les options actuelles (Ctrl+S)", "Aktuelle Optionen speichern und dauerhaft sichern (Ctrl+S)"),

    # 检查器
    ("曲目详情", "曲目詳情", "Track details", "曲の詳細", "트랙 상세", "Сведения о треке", "Détails de la piste", "Titeldetails"),
    ("选中一首曲目，查看封面、标签与鉴定详情", "選中一首曲目，查看封面、標籤與鑑定詳情", "Select a track to see cover, tags and analysis", "曲を選択するとカバー・タグ・判定詳細を表示", "트랙을 선택하면 표지, 태그, 판정 정보를 표시", "Выберите трек, чтобы увидеть обложку, теги и анализ", "Sélectionnez une piste pour voir la pochette, les étiquettes et l’analyse", "Titel auswählen, um Cover, Tags und Analyse zu sehen"),
    ("格式", "格式", "Format", "形式", "형식", "Формат", "Format", "Format"),
    ("规格", "規格", "Spec", "仕様", "사양", "Параметры", "Spécifications", "Spezifikation"),
    ("时长", "時長", "Duration", "長さ", "길이", "Длительность", "Durée", "Dauer"),
    ("码率", "位元率", "Bitrate", "ビットレート", "비트레이트", "Битрейт", "Débit", "Bitrate"),
    ("大小", "大小", "Size", "サイズ", "크기", "Размер", "Taille", "Größe"),
    ("路径", "路徑", "Path", "パス", "경로", "Путь", "Chemin", "Pfad"),
    ("尚未进行真假无损鉴定", "尚未進行真假無損鑑定", "Not analysed for lossless authenticity yet", "ロスレス判定は未実施", "무손실 판정 전", "Проверка lossless ещё не выполнена", "Authenticité lossless non analysée", "Lossless-Echtheit noch nicht geprüft"),
    ("编辑标签", "編輯標籤", "Edit tags", "タグ編集", "태그 편집", "Изменить теги", "Modifier les étiquettes", "Tags bearbeiten"),
    ("查看频谱", "查看頻譜", "View spectrum", "スペクトル表示", "스펙트럼 보기", "Показать спектр", "Voir le spectre", "Spektrum anzeigen"),

    # 搜索 / 筛选
    ("搜索歌名、歌手、专辑或文件名…", "搜尋歌名、歌手、專輯或檔名…", "Search title, artist, album or filename…", "曲名・アーティスト・アルバム・ファイル名を検索…", "곡명, 아티스트, 앨범, 파일명 검색…", "Поиск по названию, исполнителю, альбому или файлу…", "Rechercher un titre, artiste, album ou fichier…", "Titel, Künstler, Album oder Dateiname suchen…"),
    ("只看无损", "只看無損", "Lossless only", "ロスレスのみ", "무손실만", "Только lossless", "Lossless uniquement", "Nur Lossless"),
    ("只看问题", "只看問題", "Issues only", "問題のみ", "문제만", "Только проблемы", "Problèmes uniquement", "Nur Probleme"),
    ("全部规格", "全部規格", "All specs", "すべての仕様", "전체 사양", "Все параметры", "Toutes les spécifications", "Alle Spezifikationen"),
    ("鉴定", "鑑定", "Analyse", "判定", "검사", "Анализ", "Analyser", "Analysieren"),
    ("清理", "清理", "Clean", "クリーン", "정리", "Очистить", "Nettoyer", "Bereinigen"),
    ("取消选择", "取消選擇", "Clear selection", "選択解除", "선택 해제", "Снять выделение", "Annuler la sélection", "Auswahl aufheben"),
    ("运行日志", "執行日誌", "Run log", "実行ログ", "실행 로그", "Журнал выполнения", "Journal d’exécution", "Ausführungsprotokoll"),
    ("识别失败的具体原因都在这里", "識別失敗的具體原因都在這裡", "Detailed failure reasons appear here", "失敗の詳しい原因はここに表示されます", "인식 실패 원인이 여기에 표시됩니다", "Подробные причины ошибок отображаются здесь", "Les raisons détaillées des échecs apparaissent ici", "Detaillierte Fehlerursachen erscheinen hier"),
    ("收起", "收合", "Collapse", "折りたたむ", "접기", "Свернуть", "Réduire", "Einklappen"),
    ("展开", "展開", "Expand", "展開", "펼치기", "Развернуть", "Développer", "Ausklappen"),
    ("清空", "清空", "Clear", "クリア", "지우기", "Очистить", "Effacer", "Leeren"),

    # 表格列
    ("状态", "狀態", "Status", "状態", "상태", "Статус", "État", "Status"),
    ("歌名", "歌名", "Title", "曲名", "곡명", "Название", "Titre", "Titel"),
    ("歌手", "歌手", "Artist", "アーティスト", "아티스트", "Исполнитель", "Artiste", "Künstler"),
    ("专辑", "專輯", "Album", "アルバム", "앨범", "Альбом", "Album", "Album"),
    ("年份", "年份", "Year", "年", "연도", "Год", "Année", "Jahr"),
    ("无损", "無損", "Lossless", "ロスレス", "무손실", "Lossless", "Lossless", "Lossless"),
    ("置信度", "置信度", "Confidence", "信頼度", "신뢰도", "Уверенность", "Confiance", "Konfidenz"),
    ("鉴定", "鑑定", "Verdict", "判定", "판정", "Вердикт", "Verdict", "Urteil"),
    ("来源", "來源", "Source", "ソース", "출처", "Источник", "Source", "Quelle"),
    ("文件名", "檔名", "Filename", "ファイル名", "파일명", "Файл", "Fichier", "Dateiname"),
    ("说明", "說明", "Notes", "備考", "비고", "Примечание", "Notes", "Hinweis"),
    ("有损", "有損", "Lossy", "非可逆", "손실", "Lossy", "Lossy", "Lossy"),
    ("待处理", "待處理", "Pending", "待機", "대기", "В очереди", "En attente", "Ausstehend"),
    ("读取中", "讀取中", "Reading", "読み込み中", "읽는 중", "Чтение", "Lecture", "Wird gelesen"),
    ("已匹配", "已匹配", "Matched", "マッチ済み", "매칭됨", "Сопоставлено", "Correspondance trouvée", "Abgeglichen"),
    ("待人工确认", "待人工確認", "Needs review", "要確認", "검토 필요", "Требует проверки", "À vérifier", "Zu prüfen"),
    ("写入中", "寫入中", "Writing", "書き込み中", "쓰는 중", "Запись", "Écriture", "Wird geschrieben"),
    ("完成", "完成", "Done", "完了", "완료", "Готово", "Terminé", "Fertig"),
    ("已跳过", "已跳過", "Skipped", "スキップ", "건너뜀", "Пропущено", "Ignoré", "Übersprungen"),
    ("失败", "失敗", "Failed", "失敗", "실패", "Ошибка", "Échec", "Fehler"),
    ("不支持", "不支援", "Unsupported", "非対応", "지원 안 함", "Не поддерживается", "Non pris en charge", "Nicht unterstützt"),
    ("网易云", "網易雲", "NetEase Cloud", "NetEase Cloud", "NetEase Cloud", "NetEase Cloud", "NetEase Cloud", "NetEase Cloud"),
    ("QQ音乐", "QQ 音樂", "QQ Music", "QQ Music", "QQ Music", "QQ Music", "QQ Music", "QQ Music"),

    # 菜单
    ("重命名预览…", "重新命名預覽…", "Rename preview…", "名前変更プレビュー…", "이름 변경 미리보기…", "Предпросмотр переименования…", "Aperçu du renommage…", "Umbenennungsvorschau…"),
    ("导出处理报表…", "匯出處理報表…", "Export report…", "処理レポートを出力…", "처리 보고서 내보내기…", "Экспорт отчёта…", "Exporter le rapport…", "Bericht exportieren…"),
    ("曲库体检…", "曲庫健檢…", "Library health…", "ライブラリ診断…", "라이브러리 점검…", "Проверка библиотеки…", "Bilan de la bibliothèque…", "Bibliotheksprüfung…"),
    ("批量导出封面…", "批次匯出封面…", "Batch export covers…", "カバー一括出力…", "표지 일괄 내보내기…", "Пакетный экспорт обложек…", "Exporter les pochettes par lots…", "Cover stapelweise exportieren…"),
    ("生成文件夹封面", "產生資料夾封面", "Generate folder covers", "フォルダーカバーを生成", "폴더 표지 생성", "Создать обложки папок", "Générer les pochettes de dossier", "Ordnercover erzeugen"),
    ("去除选中标题的序号", "去除選中標題的序號", "Remove track numbers from selected titles", "選択したタイトルの曲番号を削除", "선택한 제목의 곡 번호 제거", "Удалить номера из выбранных названий", "Retirer les numéros des titres sélectionnés", "Titelnummern aus ausgewählten Titeln entfernen"),
    ("隔离区管理…", "隔離區管理…", "Quarantine…", "隔離所の管理…", "격리소 관리…", "Карантин…", "Gestion de la quarantaine…", "Quarantäne verwalten…"),
    ("查看频谱…", "查看頻譜…", "View spectrum…", "スペクトル表示…", "스펙트럼 보기…", "Показать спектр…", "Voir le spectre…", "Spektrum anzeigen…"),
    ("命令面板…", "命令面板…", "Command palette…", "コマンドパレット…", "명령 팔레트…", "Палитра команд…", "Palette de commandes…", "Befehlspalette…"),
    ("打开日志目录", "開啟日誌目錄", "Open log folder", "ログフォルダーを開く", "로그 폴더 열기", "Открыть папку журнала", "Ouvrir le dossier des journaux", "Protokollordner öffnen"),
    ("关于音序管家…", "關於音序管家…", "About SeqAudio Manager…", "SeqAudio Manager について…", "음서관리 정보…", "О SeqAudio Manager…", "À propos de SeqAudio Manager…", "Über SeqAudio Manager…"),
]

UI_TEXT = {
    row[0]: dict(zip(LANGS, row))
    for row in ROWS
}

TITLE_PREFIXES = [
    ("编辑标签 — ", {"zh_CN": "编辑标签 — ", "zh_TW": "編輯標籤 — ", "en": "Edit Tags — ", "ja": "タグ編集 — ", "ko": "태그 편집 — ", "ru": "Изменить теги — ", "fr": "Modifier les étiquettes — ", "de": "Tags bearbeiten — "}),
    ("批量编辑标签 — ", {"zh_CN": "批量编辑标签 — ", "zh_TW": "批次編輯標籤 — ", "en": "Batch Edit Tags — ", "ja": "タグ一括編集 — ", "ko": "태그 일괄 편집 — ", "ru": "Пакетное изменение тегов — ", "fr": "Modification groupée des étiquettes — ", "de": "Tags stapelweise bearbeiten — "}),
    ("选择匹配结果 — ", {"zh_CN": "选择匹配结果 — ", "zh_TW": "選擇匹配結果 — ", "en": "Choose Match — ", "ja": "マッチ結果を選択 — ", "ko": "매칭 결과 선택 — ", "ru": "Выбор совпадения — ", "fr": "Choisir la correspondance — ", "de": "Treffer auswählen — "}),
    ("重命名预览 — ", {"zh_CN": "重命名预览 — ", "zh_TW": "重新命名預覽 — ", "en": "Rename Preview — ", "ja": "名前変更プレビュー — ", "ko": "이름 변경 미리보기 — ", "ru": "Предпросмотр переименования — ", "fr": "Aperçu du renommage — ", "de": "Umbenennungsvorschau — "}),
    ("频谱分析 — ", {"zh_CN": "频谱分析 — ", "zh_TW": "頻譜分析 — ", "en": "Spectrum — ", "ja": "スペクトル分析 — ", "ko": "스펙트럼 분석 — ", "ru": "Спектр — ", "fr": "Spectre — ", "de": "Spektrum — "}),
]
