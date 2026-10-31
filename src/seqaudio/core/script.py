"""书写体（简/繁/拉丁等）检测与繁→简转换（需求：艺术家名字书写体统一）。

中文曲库最常见的混搭是**简繁混搭**：MusicBrainz 返回繁体（陳奕迅），iTunes 与本地
标签多为简体（陈奕迅）。逐首写回就会「一张专辑两种写法」。

这里提供：

* ``to_simplified`` —— 繁体 → 简体（仅替换映射表内的字）；
* ``script_of`` —— 判断字符串的主导书写体（简体中文 / 繁体中文 / 拉丁 / 日文 / 韩文 / 其他）；
* ``has_traditional`` —— 是否含繁体字。

只做**繁→简**这一方向：繁体→简体是确定映射、零歧义；反方向（简→繁）存在
一对多歧义（周→週/周、发→發/髮、后→後/后），在歌手名里风险高，故不提供。
"""

from __future__ import annotations

#: 繁体 -> 简体（覆盖音乐标签 / 歌手名 / 常见音乐家高频字）。
#: MusicBrainz 中文条目绝大多数用繁体，不做归一会导致相似度偏低、版本标记失配。
_T2S_SRC = """
陳陈 張张 劉刘 黃黄 楊杨 趙赵 吳吴 鄭郑 許许 鄧邓 謝谢 羅罗 蕭萧 盧卢 葉叶 蘇苏
呂吕 蔣蒋 韓韩 賈贾 馮冯 陸陆 週周 龔龚 譚谭 鍾钟 鐘钟 嚴严 歐欧 賴赖 溫温 萬万
麥麦 語语 樂乐 風风 學学 國国 愛爱 夢梦 說说 時时 間间 現现 場场 會会 聲声 聽听
無无 為为 這这 個个 們们 來来 對对 開开 關关 後后 麼么 樣样 過过 還还 經经 給给
讓让 從从 問问 題题 東东 車车 門门 見见 電电 話话 頭头 長长 與与 點点 陽阳 雲云
續续 龍龙 鳳凤 蘭兰 華华 歡欢 氣气 遠远 錢钱 錯错 難难 變变 邊边 歲岁 歸归 誰谁
傷伤 淚泪 離离 記记 憶忆 願愿 約约 親亲 獨独 藍蓝 綠绿 紅红 銀银 鐵铁 嗎吗 麗丽
裡里 著着 於于 僅仅 妳你 牠它 際际 傳传 統统 順顺 檢检 視视 覺觉 傑杰 倫伦 儀仪
優优 偉伟 側侧 債债 島岛 達达 貝贝 倉仓 澤泽 嵐岚 濱滨 謙谦 騰腾 馬马 喬乔 藝艺
齊齐 廣广 縣县 屆届 處处 團团 隊队 選选 調调 讀读 講讲 試试 詩诗 詞词 設设 認认
談谈 課课 請请 論论 諧谐 鈴铃 誠诚 誤误 誌志 賓宾 隨随 雖虽 雙双 雜杂 靈灵 靜静
顔颜 顯显 頌颂 領领 類类 顧顾 飛飞 飯饭 餘余 駐驻 駕驾 驕骄 驗验 驚惊 體体 髮发
魚鱼 鳥鸟 齒齿 龜龟
"""

_T2S: dict[int, str] = {}
for _pair in _T2S_SRC.split():
    if len(_pair) == 2:
        _T2S[ord(_pair[0])] = _pair[1]


def to_simplified(text: str) -> str:
    """繁体 → 简体（仅替换映射表内的字）。"""
    if not text:
        return text
    return text.translate(_T2S)


def has_traditional(text: str) -> bool:
    return bool(text) and to_simplified(text) != text


def script_of(text: str) -> str:
    """返回字符串主导书写体：simplified / traditional / latin / japanese / korean / other。"""
    if not text or not text.strip():
        return "other"
    if to_simplified(text) != text:
        return "traditional"

    cjk = 0
    latin = 0
    kana = 0
    hangul = 0
    for ch in text:
        code = ord(ch)
        if 0x4E00 <= code <= 0x9FFF:
            cjk += 1
        elif ("A" <= ch <= "Z") or ("a" <= ch <= "z"):
            latin += 1
        elif 0x3040 <= code <= 0x30FF:
            kana += 1
        elif 0xAC00 <= code <= 0xD7AF:
            hangul += 1
    if kana:
        return "japanese"
    if hangul:
        return "korean"
    if cjk:
        return "simplified"
    if latin:
        return "latin"
    return "other"
