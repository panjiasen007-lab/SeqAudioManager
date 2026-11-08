"""文本归一化与相似度。

匹配评分（``pipeline/matcher``）和多源候选校验（``net/aggregator``）共用同一套
规则 —— 两处各写一份的话，很容易出现「评分认为不匹配、候选校验认为匹配」的
自相矛盾，排查起来非常费劲。
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .script import to_simplified

#: 归一化时要抹掉的标点与空白
_PUNCT = re.compile(r"[\s\-_·、,，.。!！?？'\"“”‘’()（）\[\]【】~～]+")


def normalize(text: str) -> str:
    """NFKC + 转简体 + 去标点空白，用于宽松比较。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    text = to_simplified(text).lower()
    return _PUNCT.sub("", text)


def similarity(a: str, b: str) -> float:
    """0~1 的相似度，专为歌名/歌手这类短文本调过。

    带一个「同长度中文给 0.85 下限」的兜底，用于**评分**：宁可让评分偏高，
    也别把库里的繁体条目判成不匹配。
    """
    ratio = similarity_strict(a, b)
    if ratio <= 0.0 or ratio >= 1.0:
        return ratio
    na, nb = normalize(a), normalize(b)
    # 简繁混用（陈奕迅 / 陳奕迅）时逐字比对会偏低，但同长度中文串几乎总是同一实体
    if len(na) == len(nb) and ratio < 0.85:
        return 0.85
    return ratio


def similarity_strict(a: str, b: str) -> float:
    """不带兜底的相似度，用于**校验数据源候选**。

    评分可以宽容，选源不能：``similarity`` 的同长度兜底会把「旧朋友」和
    「给朋友」（只差一个字，却是完全不同的两首歌）抬到 0.85，于是搜错的源
    被当成命中，正确的源反而轮不到。这里只认真实的字符重合度。
    """
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        # 包含关系不能无条件给高分：搜「Chase」时，「汪洋与浩渺 (A Sky to Chase)」
        # 归一化后确实包含 "chase"，但那是完全不同的歌。要求短串至少占长串的
        # 55%，否则退回真实字符重合度。
        short, long = (na, nb) if len(na) <= len(nb) else (nb, na)
        if len(short) >= len(long) * 0.55:
            return 0.92
    return SequenceMatcher(None, na, nb).ratio()


def _is_ascii(text: str) -> bool:
    return all(ord(ch) < 128 for ch in (text or ""))


def artist_match(expected: str, actual: str) -> float:
    """歌手名相似度，对「中文名 ↔ 英文/拼音名」做容错。

    同一批文件里同一个人的写法经常不统一：标签写「杜德伟」、源里写「Alex To」，
    或者标签写「赵传」、源里写「Zhao Chuan」。字符层面比是 0 分，于是明明搜对了
    也被判成「歌手不符」。

    这里无法真的验证译名对应关系（那需要一张艺人别名表），所以采取保守处理：
    **一侧纯 ASCII、另一侧含中文**时给中性分 0.6 —— 不当成匹配的证据，也不当成
    反证。真正的中文 vs 中文不同人（容祖儿 vs 樊桐舟）仍然是 0 分。
    """
    left, right = (expected or "").strip(), (actual or "").strip()
    if not left or not right:
        return 1.0  # 没有可比信息，不奖不罚
    score = similarity_strict(left, right)
    if score >= 0.5:
        return score
    if _is_ascii(left) != _is_ascii(right):
        return 0.6
    return score
