"""关于页 / 首次使用须知的 8 语言富文本。

这里放的是整段 HTML，按语言键取用；占位符 {fg} {dim} {mute} {accent} {app}
由调用方在拼装时用当前主题色填充。
"""

from __future__ import annotations

#: 关于页「概览」正文
ABOUT_INTRO = {
    "zh_CN": """
<p><b style="color:{fg}">这个软件做什么</b><br/>
把本地曲库里散乱的音频文件整理成干净可用的元数据：匹配专辑信息、批量清理标签里的站点水印、内嵌或生成 LRC 歌词、按模板重命名，并判断「无损」到底是真无损还是 MP3 转码伪装的。</p>

<p><b style="color:{fg}">怎么做到不损坏文件</b><br/>
写入走三重保护：先备份 → 写影子副本 → 写后校验时长/采样率/声道 → 原子替换。任何一步失败都会退回原文件。DFF 这类容器只追加 ID3 区块，音频数据一字节不动。</p>

<p><b style="color:{fg}">数据去哪了</b><br/>
全部留在本机。联网只用于查询 MusicBrainz / Cover Art Archive / LRCLIB 三个公开数据源，不上传任何音频或个人信息。离线模式可完全断开网络。</p>

<p><b style="color:{fg}">字体</b><br/>
界面用 Noto Sans SC、数据列用 JetBrains Mono，均为 SIL OFL 1.1 授权并随包分发，不需要你安装任何字体。</p>

<p style="color:{mute}; margin-top:14px">{disclaimer}</p>
""",
    "zh_TW": """
<p><b style="color:{fg}">這個軟體做什麼</b><br/>
把本機曲庫裡散亂的音訊檔整理成乾淨可用的中繼資料：比對專輯資訊、批次清理標籤裡的站點浮水印、內嵌或產生 LRC 歌詞、依範本重新命名，並判斷「無損」到底是真無損還是 MP3 轉檔偽裝的。</p>

<p><b style="color:{fg}">怎麼做到不損壞檔案</b><br/>
寫入採三重保護：先備份 → 寫影子副本 → 寫後校驗時長/取樣率/聲道 → 原子替換。任何一步失敗都會退回原檔案。DFF 這類容器只追加 ID3 區塊，音訊資料一個位元組都不動。</p>

<p><b style="color:{fg}">資料去哪裡了</b><br/>
全部留在本機。連網只用於查詢 MusicBrainz / Cover Art Archive / LRCLIB 三個公開資料來源，不上傳任何音訊或個人資訊。離線模式可完全斷開網路。</p>

<p><b style="color:{fg}">字型</b><br/>
介面用 Noto Sans SC、資料欄用 JetBrains Mono，均為 SIL OFL 1.1 授權並隨包散布，不需要你安裝任何字型。</p>

<p style="color:{mute}; margin-top:14px">{disclaimer}</p>
""",
    "en": """
<p><b style="color:{fg}">What this app does</b><br/>
Turns scattered audio files in a local library into clean, usable metadata: album matching, watermark cleanup in tags, embedded or sidecar LRC lyrics, template-based renaming, and checking whether a “lossless” file is truly lossless or just a transcoded MP3.</p>

<p><b style="color:{fg}">How files are protected</b><br/>
Writes use three safeguards: back up → write a shadow copy → verify duration/sample rate/channels → atomic replace. Any failure rolls back to the original. Containers such as DFF only get an appended ID3 block; the audio stream is not touched at all.</p>

<p><b style="color:{fg}">Where data goes</b><br/>
Everything stays on this machine. Network access is only used to query three public sources — MusicBrainz, Cover Art Archive and LRCLIB — and never uploads audio or personal data. Offline mode can cut the network entirely.</p>

<p><b style="color:{fg}">Fonts</b><br/>
The interface uses Noto Sans SC and data columns use JetBrains Mono, both SIL OFL 1.1 licensed and bundled, so no font installation is required.</p>

<p style="color:{mute}; margin-top:14px">{disclaimer}</p>
""",
    "ja": """
<p><b style="color:{fg}">このソフトの役割</b><br/>
ローカル曲庫の散らかった音声ファイルを、整理されたメタデータに仕上げます。アルバム情報の照合、タグ内のサイト透かし除去、LRC 歌詞の埋め込み／生成、テンプレートによる名前変更、そして「ロスレス」が本当に可逆か MP3 変換の偽装かを判定します。</p>

<p><b style="color:{fg}">ファイルを壊さない仕組み</b><br/>
書き込みは三重保護：バックアップ → シャドウコピーへ書き込み → 長さ/サンプルレート/チャンネルを検証 → アトミック置換。どこかで失敗すれば元ファイルへ戻します。DFF などのコンテナは ID3 ブロックを追加するだけで、音声データには一切触れません。</p>

<p><b style="color:{fg}">データの行き先</b><br/>
すべてこのマシン内に留まります。通信は MusicBrainz / Cover Art Archive / LRCLIB の 3 つの公開ソースへの照会のみで、音声や個人情報を送信しません。オフラインモードでは完全に遮断できます。</p>

<p><b style="color:{fg}">フォント</b><br/>
UI は Noto Sans SC、データ列は JetBrains Mono を使用。いずれも SIL OFL 1.1 で同梱配布されるため、フォントのインストールは不要です。</p>

<p style="color:{mute}; margin-top:14px">{disclaimer}</p>
""",
    "ko": """
<p><b style="color:{fg}">이 프로그램이 하는 일</b><br/>
로컬 라이브러리의 흩어진 오디오 파일을 깔끔한 메타데이터로 정리합니다. 앨범 정보 매칭, 태그 내 사이트 워터마크 정리, LRC 가사 내장/생성, 템플릿 기반 이름 변경, 그리고 ‘무손실’이 실제 무손실인지 MP3 변환 위장인지 판정합니다.</p>

<p><b style="color:{fg}">파일을 손상시키지 않는 방법</b><br/>
쓰기는 3중 보호: 백업 → 그림자 사본에 기록 → 길이/샘플레이트/채널 검증 → 원자적 교체. 어느 단계든 실패하면 원본으로 되돌립니다. DFF 같은 컨테이너는 ID3 블록만 추가하며 오디오 데이터는 전혀 건드리지 않습니다.</p>

<p><b style="color:{fg}">데이터가 가는 곳</b><br/>
모든 데이터는 이 컴퓨터에 남습니다. 네트워크는 MusicBrainz / Cover Art Archive / LRCLIB 세 공개 소스 조회에만 사용하며 오디오나 개인정보를 업로드하지 않습니다. 오프라인 모드에서는 완전히 차단할 수 있습니다.</p>

<p><b style="color:{fg}">글꼴</b><br/>
인터페이스는 Noto Sans SC, 데이터 열은 JetBrains Mono를 사용하며 모두 SIL OFL 1.1 라이선스로 동봉되므로 별도 설치가 필요 없습니다.</p>

<p style="color:{mute}; margin-top:14px">{disclaimer}</p>
""",
    "ru": """
<p><b style="color:{fg}">Что делает программа</b><br/>
Приводит разрозненные аудиофайлы локальной библиотеки в аккуратные метаданные: сопоставление альбомов, очистка водяных знаков в тегах, встраивание или создание LRC-текстов, пакетное переименование по шаблону и проверка того, действительно ли «lossless» является lossless, а не перекодированным MP3.</p>

<p><b style="color:{fg}">Как защищаются файлы</b><br/>
Запись идёт в три шага: резервная копия → запись в теневую копию → проверка длительности/частоты/каналов → атомарная замена. При любой ошибке файл возвращается к исходному. В контейнерах вроде DFF добавляется только блок ID3, аудиоданные не меняются ни на байт.</p>

<p><b style="color:{fg}">Куда уходят данные</b><br/>
Всё остаётся на этом компьютере. Сеть используется только для запросов к трём открытым источникам — MusicBrainz, Cover Art Archive и LRCLIB; аудио и личные данные не загружаются. Офлайн-режим полностью отключает сеть.</p>

<p><b style="color:{fg}">Шрифты</b><br/>
Интерфейс использует Noto Sans SC, столбцы данных — JetBrains Mono; оба под SIL OFL 1.1 и поставляются вместе с программой, устанавливать шрифты не нужно.</p>

<p style="color:{mute}; margin-top:14px">{disclaimer}</p>
""",
    "fr": """
<p><b style="color:{fg}">Ce que fait ce logiciel</b><br/>
Transformer les fichiers audio épars d’une bibliothèque locale en métadonnées propres : correspondance d’albums, nettoyage des filigranes dans les étiquettes, paroles LRC intégrées ou générées, renommage par modèle et vérification qu’un fichier « lossless » l’est vraiment ou n’est qu’un MP3 re-encodé.</p>

<p><b style="color:{fg}">Comment les fichiers sont protégés</b><br/>
L’écriture suit trois garde-fous : sauvegarde → écriture d’une copie fantôme → vérification durée/fréquence/canaux → remplacement atomique. Toute erreur restaure l’original. Les conteneurs comme DFF ne reçoivent qu’un bloc ID3 ajouté ; le flux audio n’est pas modifié d’un octet.</p>

<p><b style="color:{fg}">Où vont les données</b><br/>
Tout reste sur cette machine. Le réseau sert uniquement à interroger trois sources publiques — MusicBrainz, Cover Art Archive et LRCLIB — sans jamais envoyer d’audio ni de données personnelles. Le mode hors ligne peut couper totalement le réseau.</p>

<p><b style="color:{fg}">Polices</b><br/>
L’interface utilise Noto Sans SC et les colonnes de données JetBrains Mono, toutes deux sous SIL OFL 1.1 et fournies avec le programme : aucune installation nécessaire.</p>

<p style="color:{mute}; margin-top:14px">{disclaimer}</p>
""",
    "de": """
<p><b style="color:{fg}">Was dieses Programm tut</b><br/>
Es verwandelt verstreute Audiodateien einer lokalen Bibliothek in saubere Metadaten: Album-Abgleich, Entfernen von Website-Wasserzeichen aus Tags, eingebettete oder erzeugte LRC-Songtexte, Umbenennen nach Vorlage und die Prüfung, ob „Lossless“ wirklich verlustfrei oder nur ein umkodiertes MP3 ist.</p>

<p><b style="color:{fg}">Wie Dateien geschützt werden</b><br/>
Schreiben läuft über drei Sicherungen: Sicherung → Schattenkopie schreiben → Dauer/Abtastrate/Kanäle prüfen → atomarer Austausch. Bei jedem Fehler wird die Originaldatei wiederhergestellt. Container wie DFF erhalten nur einen angehängten ID3-Block; die Audiospur bleibt bytegenau unverändert.</p>

<p><b style="color:{fg}">Wohin die Daten gehen</b><br/>
Alles bleibt auf diesem Rechner. Das Netz wird nur für drei öffentliche Quellen genutzt — MusicBrainz, Cover Art Archive und LRCLIB — und es werden weder Audio noch persönliche Daten hochgeladen. Der Offline-Modus kann das Netz vollständig trennen.</p>

<p><b style="color:{fg}">Schriften</b><br/>
Die Oberfläche nutzt Noto Sans SC, Datenspalten JetBrains Mono; beide unter SIL OFL 1.1 und mitgeliefert — eine Installation ist nicht nötig.</p>

<p style="color:{mute}; margin-top:14px">{disclaimer}</p>
""",
}

#: 关于页底部的免责声明
DISCLAIMER_TEXT = {
    "zh_CN": "本软件只修改音频文件的标签区块，不重新编码音频流，并按你的设置生成备份。但任何批量写操作都存在风险，请先用小样本验证，重要母带请自行另存。",
    "zh_TW": "本軟體只修改音訊檔的標籤區塊，不重新編碼音訊流，並依你的設定產生備份。但任何批次寫入操作都存在風險，請先用小樣本驗證，重要母帶請自行另存。",
    "en": "This app only modifies the tag block of audio files, never re-encodes the audio stream, and creates backups according to your settings. Any batch write carries risk: verify on a small sample first and keep your own copy of important masters.",
    "ja": "本ソフトは音声ファイルのタグ領域のみを変更し、音声ストリームを再エンコードしません。バックアップは設定に従って作成します。ただし一括書き込みには常にリスクがあるため、まず少数で検証し、重要なマスターは別に保存してください。",
    "ko": "이 프로그램은 오디오 파일의 태그 영역만 수정하며 오디오 스트림을 다시 인코딩하지 않고, 설정에 따라 백업을 만듭니다. 다만 일괄 쓰기에는 항상 위험이 있으니 먼저 소량으로 검증하고 중요한 원본은 별도로 보관하세요.",
    "ru": "Программа изменяет только блок тегов аудиофайлов, не перекодирует аудиопоток и создаёт резервные копии согласно настройкам. Любая пакетная запись связана с риском: сначала проверьте на небольшой выборке, а важные мастер-копии храните отдельно.",
    "fr": "Ce logiciel ne modifie que le bloc d’étiquettes des fichiers audio, ne ré-encode jamais le flux audio et crée des sauvegardes selon vos réglages. Toute écriture par lots comporte un risque : testez d’abord sur un petit échantillon et conservez votre propre copie des masters importants.",
    "de": "Dieses Programm ändert nur den Tag-Bereich von Audiodateien, kodiert den Audiostream nie neu und legt Sicherungen gemäß Ihren Einstellungen an. Jedes Schreiben in Stapeln ist riskant: zuerst an einer kleinen Auswahl prüfen und wichtige Master separat aufbewahren.",
}

#: 关于页「概览」里显示的 GPL 摘要
LICENSE_SUMMARY = {
    "zh_CN": """
<p style="line-height:170%">
<b>音序管家（{app}）</b> 是自由软件：你可以依据自由软件基金会发布的
<b>GNU 通用公共许可证第 3 版</b>（或你选择的任何更新版本）的条款，
再分发和／或修改它。</p>

<p style="line-height:170%">本程序分发时希望它有用，但<b>不提供任何担保</b>，
甚至不包含适销性或特定用途适用性的默示担保。详见 GNU 通用公共许可证。</p>

<p style="line-height:170%">你应当已经收到本程序的 GNU 通用公共许可证副本；
如果没有，请访问
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a>。</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
本程序不提供任何形式的保证。这是开源软件，欢迎在遵守 GPLv3 的前提下
自由使用、修改与再分发。<br/><br/>
GPLv3 的核心是「四大自由」：<br/>
· 为任何目的运行本程序的自由；<br/>
· 研究并修改本程序的自由（前提是能拿到源代码）；<br/>
· 再分发副本的自由；<br/>
· 改进本程序并向公众发布改进的自由。<br/><br/>
作为交换，任何分发本程序（或修改版）的人，都必须同样以 GPLv3 提供源代码。
</p>
""",
    "zh_TW": """
<p style="line-height:170%">
<b>音序管家（{app}）</b> 是自由軟體：你可以依自由軟體基金會發布的
<b>GNU 通用公共授權第 3 版</b>（或你選擇的任何更新版本）的條款，
再散布和／或修改它。</p>

<p style="line-height:170%">本程式散布時希望它有用，但<b>不提供任何擔保</b>，
甚至不包含適售性或特定用途適用性的默示擔保。詳見 GNU 通用公共授權。</p>

<p style="line-height:170%">你應當已經收到本程式的 GNU 通用公共授權副本；
如果沒有，請造訪
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a>。</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
本程式不提供任何形式的保證。這是開源軟體，歡迎在遵守 GPLv3 的前提下
自由使用、修改與再散布。<br/><br/>
GPLv3 的核心是「四大自由」：<br/>
· 為任何目的執行本程式的自由；<br/>
· 研究並修改本程式的自由（前提是能拿到原始碼）；<br/>
· 再散布副本的自由；<br/>
· 改進本程式並向公眾發布改進的自由。<br/><br/>
作為交換，任何散布本程式（或修改版）的人，都必須同樣以 GPLv3 提供原始碼。
</p>
""",
    "en": """
<p style="line-height:170%">
<b>SeqAudio Manager</b> is free software: you can redistribute it and/or modify it
under the terms of the <b>GNU General Public License version 3</b>, as published by the
Free Software Foundation, or any later version you choose.</p>

<p style="line-height:170%">It is distributed in the hope that it will be useful, but
<b>WITHOUT ANY WARRANTY</b>, not even the implied warranty of merchantability or fitness
for a particular purpose. See the GNU GPL for details.</p>

<p style="line-height:170%">You should have received a copy of the GNU GPL with this program;
if not, visit
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a>.</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
This program comes with no warranty of any kind. It is open-source; you may freely use,
modify and redistribute it under GPLv3.<br/><br/>
GPLv3 is built on four freedoms:<br/>
· to run the program for any purpose;<br/>
· to study and modify it (with access to the source);<br/>
· to redistribute copies;<br/>
· to improve it and release improvements to the public.<br/><br/>
In exchange, anyone distributing this program or a modified version must also provide
the source under GPLv3.
</p>
""",
    "ja": """
<p style="line-height:170%">
<b>SeqAudio Manager</b> は自由ソフトウェアです。Free Software Foundation が公表した
<b>GNU 一般公衆利用許諾契約書 第 3 版</b>（またはそれ以降の任意の版）の条件に従って、
再配布および／または改変ができます。</p>

<p style="line-height:170%">本プログラムは有用であることを願って配布されますが、
<b>いかなる保証もありません</b>。商品性や特定目的への適合性の黙示的保証も含みません。
詳細は GNU GPL を参照してください。</p>

<p style="line-height:170%">本プログラムと共に GNU GPL の写しを受け取っているはずです。
受け取っていない場合は
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a> をご覧ください。</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
本プログラムはいかなる保証も提供しません。オープンソースであり、GPLv3 の条件のもとで
自由な使用・改変・再配布が認められます。<br/><br/>
GPLv3 の核は「四つの自由」です：<br/>
· あらゆる目的で本プログラムを実行する自由；<br/>
· ソースを入手して研究・改変する自由；<br/>
· 複製物を再配布する自由；<br/>
· 改良し、その改良を公開する自由。<br/><br/>
その代わり、本プログラム（または改変版）を配布する者は、同じく GPLv3 で
ソースを提供しなければなりません。
</p>
""",
    "ko": """
<p style="line-height:170%">
<b>음서관리({app})</b>는 자유 소프트웨어입니다. 자유 소프트웨어 재단이 공표한
<b>GNU 일반 공중 사용 허가서 제3판</b>(또는 이후의 어떤 판)의 조건에 따라
재배포하거나 수정할 수 있습니다.</p>

<p style="line-height:170%">이 프로그램은 유용하기를 바라며 배포되지만,
<b>어떤 보증도 제공하지 않습니다</b>. 상품성이나 특정 목적 적합성에 대한 묵시적
보증도 포함하지 않습니다. 자세한 내용은 GNU GPL을 참고하십시오.</p>

<p style="line-height:170%">이 프로그램과 함께 GNU GPL 사본을 받았어야 합니다.
받지 못했다면
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a>을 방문하십시오.</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
이 프로그램은 어떤 형태의 보증도 제공하지 않습니다. 오픈 소스이며 GPLv3를
준수하는 한 자유롭게 사용, 수정, 재배포할 수 있습니다.<br/><br/>
GPLv3의 핵심은 ‘네 가지 자유’입니다:<br/>
· 어떤 목적으로든 프로그램을 실행할 자유;<br/>
· 소스를 확보해 연구하고 수정할 자유;<br/>
· 복제본을 재배포할 자유;<br/>
· 개선하여 대중에게 공개할 자유.<br/><br/>
그 대가로 이 프로그램(또는 수정판)을 배포하는 사람은 GPLv3로 소스를
함께 제공해야 합니다.
</p>
""",
    "ru": """
<p style="line-height:170%">
<b>SeqAudio Manager</b> — свободное программное обеспечение: вы можете
распространять и/или изменять его на условиях <b>GNU General Public License версии 3</b>,
опубликованной Free Software Foundation, или любой более новой версии.</p>

<p style="line-height:170%">Программа распространяется в надежде, что она будет полезна, но
<b>БЕЗ КАКИХ-ЛИБО ГАРАНТИЙ</b>, включая подразумеваемые гарантии товарной пригодности
или пригодности для конкретной цели. Подробности — в GNU GPL.</p>

<p style="line-height:170%">Вместе с программой вы должны были получить копию GNU GPL;
если нет, см.
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a>.</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
Программа не предоставляет никаких гарантий. Это открытый код: вы можете свободно
использовать, изменять и распространять её при соблюдении GPLv3.<br/><br/>
В основе GPLv3 — четыре свободы:<br/>
· запускать программу для любых целей;<br/>
· изучать и изменять её (имея доступ к исходному коду);<br/>
· распространять копии;<br/>
· улучшать программу и публиковать улучшения.<br/><br/>
Взамен любой, кто распространяет программу или её изменённую версию, обязан
предоставить исходный код на условиях GPLv3.
</p>
""",
    "fr": """
<p style="line-height:170%">
<b>SeqAudio Manager</b> est un logiciel libre : vous pouvez le redistribuer et/ou
le modifier selon les termes de la <b>GNU General Public License version 3</b>, telle que
publiée par la Free Software Foundation, ou toute version ultérieure.</p>

<p style="line-height:170%">Il est distribué dans l’espoir qu’il sera utile, mais
<b>SANS AUCUNE GARANTIE</b>, même la garantie implicite de qualité marchande ou
d’adéquation à un usage particulier. Voir la GNU GPL pour les détails.</p>

<p style="line-height:170%">Vous devriez avoir reçu une copie de la GNU GPL avec ce programme ;
sinon, consultez
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a>.</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
Ce programme est fourni sans garantie d’aucune sorte. Il est open source : vous pouvez
l’utiliser, le modifier et le redistribuer librement sous GPLv3.<br/><br/>
GPLv3 repose sur quatre libertés :<br/>
· exécuter le programme pour tout usage ;<br/>
· l’étudier et le modifier (avec accès au code source) ;<br/>
· redistribuer des copies ;<br/>
· l’améliorer et publier les améliorations.<br/><br/>
En échange, quiconque distribue ce programme ou une version modifiée doit aussi
fournir le code source sous GPLv3.
</p>
""",
    "de": """
<p style="line-height:170%">
<b>SeqAudio Manager</b> ist freie Software: Sie können es unter den Bedingungen der
<b>GNU General Public License Version 3</b>, wie von der Free Software Foundation
veröffentlicht, oder einer beliebigen späteren Version weitergeben und/oder verändern.</p>

<p style="line-height:170%">Es wird in der Hoffnung verteilt, nützlich zu sein, jedoch
<b>OHNE JEDE GEWÄHRLEISTUNG</b>, auch ohne die implizite Gewährleistung der Marktgängigkeit
oder Eignung für einen bestimmten Zweck. Einzelheiten in der GNU GPL.</p>

<p style="line-height:170%">Sie sollten mit diesem Programm eine Kopie der GNU GPL erhalten
haben; falls nicht, siehe
<a href="https://www.gnu.org/licenses/gpl-3.0.html" style="color:{accent}">
https://www.gnu.org/licenses/gpl-3.0.html</a>.</p>

<p style="line-height:170%; color:{dim}">
Copyright (C) 2026 SeqAudio Manager Contributors<br/>
Dieses Programm kommt ohne jegliche Gewährleistung. Es ist Open Source; Nutzung,
Änderung und Weitergabe sind unter GPLv3 frei gestattet.<br/><br/>
GPLv3 beruht auf vier Freiheiten:<br/>
· das Programm für jeden Zweck auszuführen;<br/>
· es zu untersuchen und zu ändern (mit Zugriff auf den Quellcode);<br/>
· Kopien weiterzugeben;<br/>
· es zu verbessern und Verbesserungen zu veröffentlichen.<br/><br/>
Im Gegenzug muss jeder, der das Programm oder eine veränderte Version weitergibt,
den Quellcode ebenfalls unter GPLv3 bereitstellen.
</p>
""",
}

#: 第三方组件页的说明
THIRD_PARTY_INTRO = {
    "zh_CN": "本软件建立在以下开源项目之上，感谢它们的作者。各处授权文本随安装包一并分发。",
    "zh_TW": "本軟體建立在以下開源專案之上，感謝它們的作者。各處授權文本隨安裝包一併散布。",
    "en": "This app is built on the open-source projects below; thanks to their authors. Their license texts are bundled with the installer.",
    "ja": "本ソフトは以下のオープンソースプロジェクトの上に成り立っています。作者の皆様に感謝します。各ライセンス文はインストーラーに同梱されています。",
    "ko": "이 프로그램은 아래 오픈소스 프로젝트 위에 만들어졌습니다. 제작자들에게 감사드립니다. 각 라이선스 문서는 설치 파일에 포함되어 있습니다.",
    "ru": "Программа построена на перечисленных ниже открытых проектах; спасибо их авторам. Тексты лицензий поставляются вместе с установщиком.",
    "fr": "Ce logiciel repose sur les projets open source ci-dessous ; merci à leurs auteurs. Les textes de licence sont fournis avec l’installateur.",
    "de": "Dieses Programm baut auf den folgenden Open-Source-Projekten auf; Dank an ihre Autoren. Die Lizenztexte liegen dem Installer bei.",
}

THIRD_PARTY_NOTE = {
    "zh_CN": "说明：PySide6 以 LGPLv3 授权动态链接使用；FFmpeg 二进制来自 imageio-ffmpeg（LGPL 构建），仅用于解码分析，不做再编码。",
    "zh_TW": "說明：PySide6 以 LGPLv3 授權動態連結使用；FFmpeg 二進位來自 imageio-ffmpeg（LGPL 建置），僅用於解碼分析，不做再編碼。",
    "en": "Note: PySide6 is used as a dynamically linked library under LGPLv3; the FFmpeg binary comes from imageio-ffmpeg (LGPL build) and is used only for decoding analysis, never re-encoding.",
    "ja": "注：PySide6 は LGPLv3 に基づき動的リンクで使用しています。FFmpeg バイナリは imageio-ffmpeg（LGPL ビルド）由来で、デコード解析のみに使用し再エンコードは行いません。",
    "ko": "참고: PySide6는 LGPLv3에 따라 동적 링크로 사용합니다. FFmpeg 바이너리는 imageio-ffmpeg(LGPL 빌드)에서 오며 디코딩 분석에만 사용하고 재인코딩하지 않습니다.",
    "ru": "Примечание: PySide6 используется как динамически связанная библиотека по LGPLv3; двоичный файл FFmpeg из imageio-ffmpeg (сборка LGPL) применяется только для декодирования при анализе, без перекодирования.",
    "fr": "Note : PySide6 est utilisé en liaison dynamique sous LGPLv3 ; le binaire FFmpeg provient d’imageio-ffmpeg (build LGPL) et sert uniquement au décodage pour l’analyse, jamais au ré-encodage.",
    "de": "Hinweis: PySide6 wird als dynamisch gelinkte Bibliothek unter LGPLv3 verwendet; die FFmpeg-Binärdatei stammt von imageio-ffmpeg (LGPL-Build) und dient nur zur Dekodierung für die Analyse, nie zum Neukodieren.",
}

#: 第三方组件用途
THIRD_PARTY_PURPOSE = {
    "界面框架（Qt for Python）": {
        "zh_TW": "介面框架（Qt for Python）", "en": "UI framework (Qt for Python)",
        "ja": "UI フレームワーク（Qt for Python）", "ko": "UI 프레임워크 (Qt for Python)",
        "ru": "Фреймворк интерфейса (Qt for Python)", "fr": "Framework d’interface (Qt for Python)",
        "de": "UI-Framework (Qt for Python)",
    },
    "音频标签读写（FLAC/ID3/APEv2/MP4/DSDIFF）": {
        "zh_TW": "音訊標籤讀寫（FLAC/ID3/APEv2/MP4/DSDIFF）", "en": "Audio tag reading/writing (FLAC/ID3/APEv2/MP4/DSDIFF)",
        "ja": "音声タグの読み書き（FLAC/ID3/APEv2/MP4/DSDIFF）", "ko": "오디오 태그 읽기/쓰기 (FLAC/ID3/APEv2/MP4/DSDIFF)",
        "ru": "Чтение и запись аудиотегов (FLAC/ID3/APEv2/MP4/DSDIFF)", "fr": "Lecture/écriture des étiquettes audio (FLAC/ID3/APEv2/MP4/DSDIFF)",
        "de": "Audio-Tags lesen/schreiben (FLAC/ID3/APEv2/MP4/DSDIFF)",
    },
    "频谱分析数值计算": {
        "zh_TW": "頻譜分析數值計算", "en": "Numerical computation for spectrum analysis",
        "ja": "スペクトル分析の数値計算", "ko": "스펙트럼 분석 수치 계산",
        "ru": "Численные расчёты для спектрального анализа", "fr": "Calculs numériques pour l’analyse spectrale",
        "de": "Numerische Berechnung für die Spektralanalyse",
    },
    "解码 DSD/APE/WV/FLAC 做频谱分析": {
        "zh_TW": "解碼 DSD/APE/WV/FLAC 做頻譜分析", "en": "Decode DSD/APE/WV/FLAC for spectrum analysis",
        "ja": "DSD/APE/WV/FLAC をデコードしてスペクトル分析", "ko": "DSD/APE/WV/FLAC 디코딩 후 스펙트럼 분석",
        "ru": "Декодирование DSD/APE/WV/FLAC для спектрального анализа", "fr": "Décodage DSD/APE/WV/FLAC pour l’analyse spectrale",
        "de": "DSD/APE/WV/FLAC für die Spektralanalyse dekodieren",
    },
    "随包分发的 ffmpeg 二进制": {
        "zh_TW": "隨包散布的 ffmpeg 二進位", "en": "Bundled ffmpeg binary",
        "ja": "同梱の ffmpeg バイナリ", "ko": "동봉 ffmpeg 바이너리",
        "ru": "Поставляемый ffmpeg-бинарник", "fr": "Binaire ffmpeg fourni",
        "de": "Mitgelieferte ffmpeg-Binärdatei",
    },
    "界面中文字体（随包分发）": {
        "zh_TW": "介面中文字型（隨包散布）", "en": "Chinese UI font (bundled)",
        "ja": "中国語 UI フォント（同梱）", "ko": "중국어 UI 글꼴 (동봉)",
        "ru": "Китайский шрифт интерфейса (в комплекте)", "fr": "Police chinoise de l’interface (fournie)",
        "de": "Chinesische UI-Schrift (mitgeliefert)",
    },
    "数据列等宽字体（随包分发）": {
        "zh_TW": "資料欄等寬字型（隨包散布）", "en": "Monospace data font (bundled)",
        "ja": "データ列用等幅フォント（同梱）", "ko": "데이터 열 고정폭 글꼴 (동봉)",
        "ru": "Моноширинный шрифт данных (в комплекте)", "fr": "Police monospace des données (fournie)",
        "de": "Monospace-Schrift für Daten (mitgeliefert)",
    },
    "安装包与便携版压缩（仅构建期）": {
        "zh_TW": "安裝包與可攜版壓縮（僅建置期）", "en": "Installer and portable archive compression (build time only)",
        "ja": "インストーラーとポータブル版の圧縮（ビルド時のみ）", "ko": "설치 파일 및 포터블 압축 (빌드 시에만)",
        "ru": "Сжатие установщика и портативной версии (только при сборке)", "fr": "Compression de l’installateur et de la version portable (à la compilation uniquement)",
        "de": "Komprimierung von Installer und Portable-Version (nur zur Buildzeit)",
    },
}

#: 首次使用须知
NOTICE = {
    "zh_CN": """
<h3>使用前须知</h3>
<p><b>1. 版权声明</b><br>
本软件仅用于整理<b>您本人合法拥有或已获得授权</b>的音频文件。请勿用于盗版资源抓取、
分发或任何商业侵权行为。因使用不当产生的法律责任由使用者自行承担。</p>

<p><b>2. 数据来源</b><br>
歌曲信息与封面来自 MusicBrainz、Cover Art Archive、LRCLIB 等开源平台，
遵循其各自的授权协议；歌词版权归原词曲权利人所有，本软件<b>不存储、不分发</b>歌词内容，
仅在您的本机写入您自己的音频文件。</p>

<p><b>3. 隐私承诺</b><br>
本软件<b>绝不上传您的音频文件本体</b>，不捆绑任何广告与推广。
联网时仅发送用于检索的文本关键词（歌名/歌手/专辑/时长）。</p>

<p><b>4. 写入安全</b><br>
标签写入只修改标签区块，<b>不重新编码音频</b>；默认在写入前备份原文件，
写入失败自动回滚。仍建议您对重要曲库先自行留一份副本。</p>

<p><b>5. 功能边界</b><br>
不支持 ncm / qmc / kgm 等加密格式的解析与解密（会直接跳过）；
无法去除音频流内的人声水印，只能清理标签文字水印与封面图水印。</p>

<p style="color:#8b909a">继续使用即表示您已阅读并同意以上条款。</p>
""",
    "zh_TW": """
<h3>使用前須知</h3>
<p><b>1. 版權聲明</b><br>
本軟體僅用於整理<b>您本人合法擁有或已取得授權</b>的音訊檔。請勿用於盜版資源抓取、
散布或任何商業侵權行為。因使用不當產生的法律責任由使用者自行承擔。</p>

<p><b>2. 資料來源</b><br>
歌曲資訊與封面來自 MusicBrainz、Cover Art Archive、LRCLIB 等開源平台，
遵循其各自的授權協議；歌詞版權歸原詞曲權利人所有，本軟體<b>不儲存、不散布</b>歌詞內容，
僅在您的本機寫入您自己的音訊檔。</p>

<p><b>3. 隱私承諾</b><br>
本軟體<b>絕不上傳您的音訊檔本體</b>，不捆綁任何廣告與推廣。
連網時僅傳送用於檢索的文字關鍵詞（歌名/歌手/專輯/時長）。</p>

<p><b>4. 寫入安全</b><br>
標籤寫入只修改標籤區塊，<b>不重新編碼音訊</b>；預設在寫入前備份原檔案，
寫入失敗自動回滾。仍建議您對重要曲庫先自行留一份副本。</p>

<p><b>5. 功能邊界</b><br>
不支援 ncm / qmc / kgm 等加密格式的解析與解密（會直接跳過）；
無法去除音訊流內的人聲浮水印，只能清理標籤文字浮水印與封面圖浮水印。</p>

<p style="color:#8b909a">繼續使用即表示您已閱讀並同意以上條款。</p>
""",
    "en": """
<h3>Before you start</h3>
<p><b>1. Copyright</b><br>
This app is only for organizing audio files <b>you legally own or are authorized to use</b>.
Do not use it to scrape, distribute or otherwise infringe copyright. You are responsible
for any legal consequences of misuse.</p>

<p><b>2. Data sources</b><br>
Track information and covers come from open platforms such as MusicBrainz, Cover Art
Archive and LRCLIB, under their respective licenses. Lyric copyright remains with the
rights holders; this app <b>does not store or distribute</b> lyrics and only writes them
into your own local audio files.</p>

<p><b>3. Privacy</b><br>
This app <b>never uploads your audio files</b> and bundles no ads or promotions. When
online, it only sends text keywords needed for lookup (title/artist/album/duration).</p>

<p><b>4. Write safety</b><br>
Tag writing only changes the tag block and <b>never re-encodes audio</b>; files are backed
up before writing by default and failed writes roll back automatically. Still, keep your
own copy of important libraries.</p>

<p><b>5. Limits</b><br>
Encrypted formats such as ncm / qmc / kgm are not parsed or decrypted (they are skipped);
human-voice watermarks inside the audio stream cannot be removed — only text watermarks
in tags and cover-image watermarks can be cleaned.</p>

<p style="color:#8b909a">Continuing means you have read and accept these terms.</p>
""",
    "ja": """
<h3>ご利用にあたって</h3>
<p><b>1. 著作権</b><br>
本ソフトは、<b>ご自身が合法的に所有または許諾を得ている</b>音声ファイルの整理のみを
目的とします。海賊版の取得・配布や商業的な権利侵害には使用しないでください。
不適切な使用による法的責任は利用者が負います。</p>

<p><b>2. データの出所</b><br>
曲情報とカバーは MusicBrainz、Cover Art Archive、LRCLIB などのオープンプラットフォームから
各ライセンスに従って取得します。歌詞の著作権は権利者に帰属し、本ソフトは歌詞を
<b>保存も配布もせず</b>、お使いのローカル音声ファイルに書き込むだけです。</p>

<p><b>3. プライバシー</b><br>
本ソフトは<b>音声ファイル本体をアップロードしません</b>。広告やプロモーションもありません。
通信時は検索用のテキスト（曲名/アーティスト/アルバム/長さ）のみを送信します。</p>

<p><b>4. 書き込みの安全性</b><br>
タグ書き込みはタグ領域のみを変更し、<b>音声を再エンコードしません</b>。既定で書き込み前に
バックアップし、失敗時は自動で戻します。それでも重要なライブラリは別途コピーを。</p>

<p><b>5. 機能の限界</b><br>
ncm / qmc / kgm などの暗号化形式の解析・復号には対応しません（スキップします）。
音声ストリーム内の肉声透かしは除去できず、タグの文字透かしとカバー画像の透かしのみ
清掃できます。</p>

<p style="color:#8b909a">継続して使用すると、上記条項を読み同意したものとみなします。</p>
""",
    "ko": """
<h3>사용 전 안내</h3>
<p><b>1. 저작권</b><br>
이 프로그램은 <b>본인이 합법적으로 소유하거나 허가받은</b> 오디오 파일 정리에만 사용해야 합니다.
불법 자료 수집, 배포 또는 상업적 침해에 사용하지 마십시오. 부적절한 사용으로 인한
법적 책임은 사용자에게 있습니다.</p>

<p><b>2. 데이터 출처</b><br>
곡 정보와 표지는 MusicBrainz, Cover Art Archive, LRCLIB 등 오픈 플랫폼에서 각 라이선스에
따라 가져옵니다. 가사 저작권은 권리자에게 있으며, 이 프로그램은 가사를 <b>저장하거나
배포하지 않고</b> 사용자의 로컬 오디오 파일에만 기록합니다.</p>

<p><b>3. 개인정보</b><br>
이 프로그램은 <b>오디오 파일 자체를 절대 업로드하지 않으며</b> 광고나 프로모션을 포함하지
않습니다. 온라인 시 검색용 텍스트 키워드(곡명/아티스트/앨범/길이)만 전송합니다.</p>

<p><b>4. 쓰기 안전성</b><br>
태그 쓰기는 태그 영역만 수정하고 <b>오디오를 재인코딩하지 않습니다</b>. 기본적으로 쓰기 전
백업하며 실패 시 자동으로 되돌립니다. 그래도 중요한 라이브러리는 별도 사본을 보관하세요.</p>

<p><b>5. 기능 범위</b><br>
ncm / qmc / kgm 등 암호화 형식은 해석·복호화하지 않습니다(건너뜀). 오디오 스트림 내
육성 워터마크는 제거할 수 없으며, 태그의 텍스트 워터마크와 표지 이미지 워터마크만
정리할 수 있습니다.</p>

<p style="color:#8b909a">계속 사용하면 위 약관을 읽고 동의한 것으로 간주합니다.</p>
""",
    "ru": """
<h3>Перед началом работы</h3>
<p><b>1. Авторские права</b><br>
Программа предназначена только для упорядочивания аудиофайлов, <b>которыми вы законно
владеете или на которые имеете разрешение</b>. Не используйте её для извлечения,
распространения пиратских материалов или иных нарушений прав. Ответственность за
неправомерное использование несёт пользователь.</p>

<p><b>2. Источники данных</b><br>
Информация о треках и обложки берутся из открытых платформ MusicBrainz, Cover Art
Archive и LRCLIB на их условиях. Права на тексты песен принадлежат правообладателям;
программа <b>не хранит и не распространяет</b> тексты, а только записывает их в ваши
локальные аудиофайлы.</p>

<p><b>3. Конфиденциальность</b><br>
Программа <b>никогда не загружает ваши аудиофайлы</b> и не содержит рекламы.
В сети передаются только текстовые ключевые слова для поиска (название/исполнитель/
альбом/длительность).</p>

<p><b>4. Безопасность записи</b><br>
Запись тегов меняет только блок тегов и <b>не перекодирует аудио</b>; по умолчанию
перед записью создаётся резервная копия, а при ошибке выполняется откат. Всё равно
храните собственную копию важной библиотеки.</p>

<p><b>5. Ограничения</b><br>
Шифрованные форматы ncm / qmc / kgm не разбираются и не расшифровываются (пропускаются).
Водяные знаки в аудиопотоке удалить нельзя — очищаются только текстовые водяные знаки
в тегах и на изображениях обложек.</p>

<p style="color:#8b909a">Продолжая, вы подтверждаете, что прочитали и принимаете эти условия.</p>
""",
    "fr": """
<h3>Avant de commencer</h3>
<p><b>1. Droit d’auteur</b><br>
Ce logiciel sert uniquement à organiser des fichiers audio <b>que vous possédez légalement
ou que vous êtes autorisé à utiliser</b>. Ne l’utilisez pas pour extraire, distribuer des
contenus piratés ni pour aucune atteinte commerciale. Vous êtes responsable des conséquences
juridiques d’un mauvais usage.</p>

<p><b>2. Sources de données</b><br>
Les informations de pistes et les pochettes proviennent de plateformes ouvertes telles que
MusicBrainz, Cover Art Archive et LRCLIB, selon leurs licences respectives. Le droit des
paroles reste aux ayants droit ; ce logiciel <b>ne stocke ni ne distribue</b> de paroles et
les écrit seulement dans vos fichiers audio locaux.</p>

<p><b>3. Confidentialité</b><br>
Ce logiciel <b>ne téléverse jamais vos fichiers audio</b> et n’intègre ni publicité ni
promotion. En ligne, seuls des mots-clés textuels de recherche sont envoyés
(titre/artiste/album/durée).</p>

<p><b>4. Sécurité d’écriture</b><br>
L’écriture des étiquettes ne modifie que le bloc d’étiquettes et <b>ne ré-encode pas
l’audio</b> ; une sauvegarde est faite avant écriture par défaut et un échec annule
automatiquement. Conservez néanmoins votre propre copie des bibliothèques importantes.</p>

<p><b>5. Limites</b><br>
Les formats chiffrés ncm / qmc / kgm ne sont ni analysés ni déchiffrés (ils sont ignorés) ;
les filigranes vocaux dans le flux audio ne peuvent pas être retirés — seuls les filigranes
textuels des étiquettes et des images de pochette peuvent être nettoyés.</p>

<p style="color:#8b909a">Continuer signifie que vous avez lu et accepté ces conditions.</p>
""",
    "de": """
<h3>Vor der ersten Nutzung</h3>
<p><b>1. Urheberrecht</b><br>
Dieses Programm dient nur zum Ordnen von Audiodateien, <b>die Sie rechtmäßig besitzen oder
für die Sie eine Erlaubnis haben</b>. Nutzen Sie es nicht zum Abgreifen oder Verbreiten
raubkopierter Inhalte oder für kommerzielle Rechtsverletzungen. Die Verantwortung für
missbräuchliche Nutzung liegt beim Anwender.</p>

<p><b>2. Datenquellen</b><br>
Titelinformationen und Cover stammen von offenen Plattformen wie MusicBrainz, Cover Art
Archive und LRCLIB nach deren jeweiligen Lizenzen. Die Rechte an Songtexten liegen bei den
Rechteinhabern; dieses Programm <b>speichert und verbreitet</b> keine Songtexte, sondern
schreibt sie nur in Ihre lokalen Audiodateien.</p>

<p><b>3. Datenschutz</b><br>
Dieses Programm <b>lädt niemals Ihre Audiodateien hoch</b> und enthält keine Werbung.
Online werden nur Textsuchbegriffe gesendet (Titel/Künstler/Album/Dauer).</p>

<p><b>4. Schreibsicherheit</b><br>
Tag-Schreiben ändert nur den Tag-Bereich und <b>kodiert Audio nicht neu</b>; standardmäßig
wird vorher gesichert und ein Fehlschlag automatisch zurückgerollt. Bewahren Sie trotzdem
eine eigene Kopie wichtiger Bibliotheken auf.</p>

<p><b>5. Grenzen</b><br>
Verschlüsselte Formate wie ncm / qmc / kgm werden nicht analysiert oder entschlüsselt
(sie werden übersprungen); Stimmen-Wasserzeichen im Audiostream lassen sich nicht entfernen
— nur Text-Wasserzeichen in Tags und Coverbildern können bereinigt werden.</p>

<p style="color:#8b909a">Mit der weiteren Nutzung bestätigen Sie, diese Bedingungen gelesen und akzeptiert zu haben.</p>
""",
}
