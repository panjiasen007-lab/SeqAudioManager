"""标签水印清理（需求 3.4）。

明确边界
--------
* 本模块只处理**元数据文字水印**（comment/备注、标题与专辑里的推广后缀、编码者字段），
  100% 安全，不触碰音频流；
* 音频流里的人声水印属降噪范畴，需求标注为 P2，需单独模块处理，此处不做任何承诺。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from .model import Tags

#: 默认强匹配词库：命中即认为整段是站点广告
DEFAULT_STRONG_PATTERNS: tuple[str, ...] = (
    r"www\.[\w\-]+\.(com|net|cn|org|cc|top|xyz|info)",
    r"https?://",
    r"[\w\-]+\.(com|net|cn|org|cc|top|xyz)(/|$)",
    r"微信公众号",
    r"公众号[：:]",
    r"QQ群",
    r"微信[：:]",
    r"交流群",
    r"更多(音乐|歌曲|资源)",
    r"音乐(网|站|论坛|社区)",
    r"(下载|转载|首发|整理|制作|压制|抓轨|翻录)[\s]*[：:]",
    r"仅供(学习|试听|交流)",
    r"请于?24小时(内)?删除",
    r"版权归(原)?(作者|唱片|公司)",
    r"无损音乐(下载|网|站)",
    r"(下载|获取|访问)[\s]*[A-Za-z0-9\-\.]{3,}",
    r"(整理|制作|压制|抓轨|翻录|首发|转载)(版)?$",
    r"歌词.{0,12}(提供|制作|下载|编辑)",
    # ---- 联系方式 / 社交账号（常见于 comment 与名字后缀）----
    r"[\w.\-]+@[\w\-]+\.(com|net|cn|org)",
    r"微博[：:\s]",
    r"@[\w\u4e00-\u9fff]{2,}",
    r"(微信|QQ|扣扣|电话|手机|联系|微)[：:\s]*1[3-9]\d{9}",
    r"QQ[：:\s]*\d{5,}",
    r"(关注|订阅|点赞|转发|分享)[：:]",
    r"(百度云|网盘|蓝奏云|阿里云盘|夸克|迅雷|磁力)[：:\s]*",
    r"(加群|进群|入群|粉丝群|交流群)",
    r"vx[：:]",
)

#: 片段匹配词库：出现在括号里时删除该括号片段（用于标题/专辑/流派清理）
DEFAULT_SEGMENT_PATTERNS: tuple[str, ...] = (
    r"www\.",
    r"https?://",
    r"[\w\-]+\.(com|net|cn|org|cc|top|xyz)",
    r"[\w.\-]+@[\w\-]+\.",
    r"微博",
    r"公众号",
    r"QQ[：:]?",
    r"微信",
    r"关注",
    r"订阅",
    r"网盘",
    r"高音质",
    r"无损音质",
    r"免费下载",
    r"音乐下载",
    r"更多音乐",
    r"整理版",
    r"精校版",
    r"转载",
)

#: 括号包裹形式
_BRACKETS = [
    (re.compile(r"[\(\（]([^\)\）]{0,80})[\)\）]"), 0),
    (re.compile(r"[\[\【]([^\]\】]{0,80})[\]\】]"), 0),
    (re.compile(r"[<《]([^>》]{0,80})[>》]"), 0),
]

#: 尾部水印后缀：``歌手 - 某某制作`` / ``歌手_www.xxx.com``
_TAIL_SEP = re.compile(r"\s*[-_|/–—]\s*([^\-_|/–—]{2,40})$")


@dataclass
class WatermarkReport:
    """清理结果明细。"""

    changed_fields: list[str] = field(default_factory=list)
    details: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changed_fields)


def compile_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            continue
    return compiled


def is_watermark(text: str, strong: list[re.Pattern[str]]) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in strong)


def _strip_bracket_segments(text: str, segment: list[re.Pattern[str]]) -> str:
    """删除含水印关键词的括号片段，保留其余括号内容。"""

    def _replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        if any(p.search(inner) for p in segment):
            return ""
        return match.group(0)

    result = text
    for regex, _ in _BRACKETS:
        result = regex.sub(_replace, result)
    return result


def _strip_tail_suffix(text: str, strong: list[re.Pattern[str]]) -> str:
    match = _TAIL_SEP.search(text)
    if not match:
        return text
    tail = match.group(1)
    if is_watermark(tail, strong):
        return text[: match.start()].rstrip()
    return text


def clean_text(
    text: str,
    strong: list[re.Pattern[str]],
    segment: list[re.Pattern[str]],
    *,
    allow_wipe: bool = True,
) -> str:
    """对单个字段做水印剔除。

    ``allow_wipe`` 控制「整段命中强规则时是否清空」：comment 类字段允许整段清空；
    标题/专辑等识别字段不允许清空（否则会丢歌名），只做片段删除。
    """
    if not text:
        return text
    candidate = text.strip()
    if allow_wipe and is_watermark(candidate, strong):
        # 整段都是广告（例如 comment 里就写了一行站点地址）
        return ""
    candidate = _strip_bracket_segments(candidate, segment)
    candidate = _strip_tail_suffix(candidate, strong)
    candidate = re.sub(r"\s{2,}", " ", candidate).strip(" -_|/–—\t")
    return candidate


def clean_tags(
    tags: Tags,
    *,
    strong_patterns: Iterable[str] = DEFAULT_STRONG_PATTERNS,
    segment_patterns: Iterable[str] = DEFAULT_SEGMENT_PATTERNS,
    custom_words: Iterable[str] = (),
    clean_title: bool = True,
    clean_albumartist: bool = True,
) -> WatermarkReport:
    """就地清理 ``tags``，返回明细。"""
    report = WatermarkReport()
    strong = compile_patterns(strong_patterns)
    segment = compile_patterns(segment_patterns)
    # 自定义屏蔽词只作片段删除，避免把正常字段整段清空
    for word in custom_words:
        word = (word or "").strip()
        if word:
            segment.append(re.compile(re.escape(word), re.IGNORECASE))

    # comment / 备注：整段命中即清空（需求 3.4.1）
    if tags.comment:
        cleaned = clean_text(tags.comment, strong, segment, allow_wipe=True)
        if cleaned != tags.comment:
            report.details.append(f"comment: {tags.comment[:40]!r} -> {cleaned[:40]!r}")
            tags.comment = cleaned
            report.changed_fields.append("comment")

    if clean_title and tags.title:
        cleaned = clean_text(tags.title, strong, segment, allow_wipe=False)
        if cleaned and cleaned != tags.title:
            report.details.append(f"title: {tags.title!r} -> {cleaned!r}")
            tags.title = cleaned
            report.changed_fields.append("title")
        elif not cleaned and tags.title:
            # 标题全是水印，不能清空，保留原值并提示人工处理
            report.details.append(f"title 疑似整段为水印，已保留原值：{tags.title!r}")

    if clean_albumartist and tags.albumartist:
        cleaned = clean_text(tags.albumartist, strong, segment, allow_wipe=False)
        if cleaned and cleaned != tags.albumartist:
            report.details.append(f"albumartist: {tags.albumartist!r} -> {cleaned!r}")
            tags.albumartist = cleaned
            report.changed_fields.append("albumartist")

    for field_name in ("album", "artist", "genre"):
        value = tags.get(field_name)
        if not value:
            continue
        cleaned = clean_text(value, strong, segment, allow_wipe=False)
        if cleaned and cleaned != value:
            report.details.append(f"{field_name}: {value!r} -> {cleaned!r}")
            tags.set(field_name, cleaned)
            report.changed_fields.append(field_name)

    return report


def cover_looks_watermarked(cover: bytes) -> bool:
    """封面水印粗判：仅当存在 Pillow 时做边缘色带检测，否则一律返回 False（不误删）。

    真正的封面水印处理走「用 Cover Art Archive 的干净封面替换」这一可靠路径，
    不做像素级修复（需求 3.4 封面水印清理）。
    """
    if not cover:
        return False
    try:
        from io import BytesIO

        from PIL import Image  # type: ignore
    except Exception:
        return False
    try:
        with Image.open(BytesIO(cover)) as img:
            img = img.convert("RGB")
            width, height = img.size
            if height < 60 or width < 60:
                return False
            # 底部 8% 区域若是近乎纯色的横条，多半是站点水印条
            strip = img.crop((0, int(height * 0.92), width, height))
            colors = strip.getcolors(maxcolors=100000) or []
            if not colors:
                return False
            total = sum(count for count, _ in colors)
            dominant = max(colors, key=lambda c: c[0])
            return total > 0 and dominant[0] / total > 0.97
    except Exception:
        return False
