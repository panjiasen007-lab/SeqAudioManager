"""字段清洗：去序号前缀、去多余空格。

为什么单独做一个模块
------------------
「歌名里去序号」看似简单，其实边界很多：

* ``01. 七里香`` / ``01 七里香`` / ``01-七里香`` / ``01、七里香`` 是最常见的；
* 还有 ``[01] 七里香``、``(01) 七里香``、``【01】七里香`` 这类带括号的；
* 但 ``1997 - 往事`` 不能当序号删（4 位是年份），``1+1=2`` 也不能删。

规则因此收得很紧：只认 **1~2 位纯数字**（3 位可选，为 100+ 轨的合集留口子），
后面必须跟着一个明确的「序号分隔符」（点 / 顿号 / 连字符 / 空格 / 括号闭合）。
其它一律不动 —— 宁可漏删，不能误删歌名本身。
"""

from __future__ import annotations

import re

#: 1~2 位数字（可选开 3 位），后跟分隔符。分隔符里不含「.」到英文缩写的情况，
#: 因为 "U2. Beautiful Day" 这种「数字+点」也可能是歌名的一部分，交给上层按需开启
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # [01] / (01) / 【01】/ 〔01〕 开头
    re.compile(r"^\s*[\[\(（【〔]\s*(\d{1,3})\s*[\]\)）】〕]\s*"),
    # 01. / 01、/ 01· / 01- / 01— / 01_ / 01: 开头
    re.compile(r"^\s*(\d{1,3})\s*[.、·\-—_–:：]\s*"),
    # 两位及以上数字 + 空格（抓轨软件最常见的零填充序号：01 / 12 / 123）
    re.compile(r"^\s*(\d{2,3})\s+(?=\S)"),
    # 单数字 + 空格，仅当后面跟中文时才认作序号 ——
    # 否则 "3 Doors Down" 的 3 会被误当成音轨号
    re.compile(r"^\s*(\d)\s+(?=[\u4e00-\u9fff])"),
)


def strip_track_number(title: str, *, allow_three_digit: bool = False) -> tuple[str, bool]:
    """去掉标题开头的曲目序号，返回 (新标题, 是否改动)。

    只处理 1~2 位数字（``allow_three_digit=True`` 时到 3 位）；数字前若还有
    别的可见字符（例如歌手名）则直接返回原样 —— 序号只能出现在最开头。
    """
    original = title
    text = (title or "").strip()
    if not text:
        return original, False

    max_digits = 3 if allow_three_digit else 2
    for pattern in _PATTERNS:
        match = pattern.match(text)
        if not match:
            continue
        digits = match.group(1)
        if len(digits) > max_digits:
            continue
        # 4 位及以上一定是年份或编号，不是曲目序号
        stripped = text[match.end():].strip()
        if not stripped:
            # 「01」删完就空了，保留原值（没有正文的歌名不能清空）
            return original, False
        return stripped, True
    return original, False


def clean_title(title: str, *, strip_number: bool = True) -> tuple[str, list[str]]:
    """标题级清洗：去序号 + 合并多余空格。返回 (新标题, 变更说明)。"""
    changes: list[str] = []
    text = (title or "").strip()
    if strip_number:
        text, stripped = strip_track_number(text)
        if stripped:
            changes.append("去序号")
    # 多个连续空格 → 单个；顺带清掉标题首尾的悬空分隔符
    collapsed = re.sub(r"\s{2,}", " ", text).strip(" -_|·,、.")
    if collapsed and collapsed != text:
        text = collapsed
        changes.append("去空格")
    return text or (title or ""), changes
