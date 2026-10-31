"""标签读写适配层：抹平 VorbisComment / ID3 / APEv2 / MP4 原子 四套体系的差异。

设计要点
--------
1. **只改标签区块**：全部通过 mutagen 的 ``save()`` 完成，绝不触碰音频流；
2. **族系分派**：按 mutagen 解析出的 tag 对象类型决定读写路径，避免按扩展名猜测；
3. **不支持写入的格式显式拒绝**：如 DSDIFF（DFF）目前只能读 ID3，写入会返回
   ``Writable=False`` 并让上层标记为「只读」，绝不冒险破坏母带文件。
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Optional

import mutagen
from mutagen import File as MutagenFile

from .model import Tags

# 文本字段 -> 各族系键名
_VORBIS_KEYS = {
    "title": "TITLE",
    "artist": "ARTIST",
    "album": "ALBUM",
    "albumartist": "ALBUMARTIST",
    "date": "DATE",
    "genre": "GENRE",
    "track": "TRACKNUMBER",
    "disc": "DISCNUMBER",
    "comment": "COMMENT",
}

_ID3_KEYS = {
    "title": "TIT2",
    "artist": "TPE1",
    "album": "TALB",
    "albumartist": "TPE2",
    "date": "TDRC",
    "genre": "TCON",
    "track": "TRCK",
    "disc": "TPOS",
    "comment": "COMM",
}

_APE_KEYS = {
    "title": "Title",
    "artist": "Artist",
    "album": "Album",
    "albumartist": "Album Artist",
    "date": "Year",
    "genre": "Genre",
    "track": "Track",
    "disc": "Disc",
    "comment": "Comment",
}

_MP4_KEYS = {
    "title": "\xa9nam",
    "artist": "\xa9ART",
    "album": "\xa9alb",
    "albumartist": "aART",
    "date": "\xa9day",
    "genre": "\xa9gen",
    "comment": "\xa9cmt",
}

# DFF 只读：mutagen 能解析但 save() 未实现
_READ_ONLY_MODULES = {"dsdiff"}


def _module_name(obj: Any) -> str:
    return type(obj).__module__.rsplit(".", 1)[-1]


def is_writable(obj: Any) -> bool:
    """判断该文件是否支持安全写回。"""
    if obj is None:
        return False
    if _module_name(obj) in _READ_ONLY_MODULES:
        return False
    # 部分格式暴露 save() 但实际抛 NotImplementedError
    try:
        return callable(getattr(obj, "save", None))
    except Exception:
        return False


def open_audio(path: Path | str) -> Optional[Any]:
    """打开音频文件（流式，不加载音频数据）。"""
    try:
        return MutagenFile(str(path))
    except Exception:
        return None


def _first(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        if not value:
            return ""
        value = value[0]
    if value is None:
        return ""
    return str(value).strip()


# ---------------------------------------------------------------- 读取


def read_tags(path: Path | str, audio: Any = None) -> Tags:
    """读取统一标签视图。``audio`` 可传入已打开的 mutagen 对象以省一次 IO。"""
    obj = audio if audio is not None else open_audio(path)
    tags = Tags()
    if obj is None:
        return tags
    raw = obj.tags

    if raw is None:
        return tags

    family = _family(obj)
    if family == "vorbis":
        for field, key in _VORBIS_KEYS.items():
            setattr(tags, field, _first(raw.get(key)))
        tags.lyrics = _first(raw.get("LYRICS") or raw.get("UNSYNCEDLYRICS") or raw.get("LYRIC"))
        tags.cover, tags.cover_mime = _read_vorbis_cover(obj, raw)
    elif family == "id3":
        for field, key in _ID3_KEYS.items():
            frame = raw.get(key)
            if frame is not None:
                setattr(tags, field, _first(getattr(frame, "text", None)))
        tags.lyrics = _read_id3_lyrics(raw)
        tags.cover, tags.cover_mime = _read_id3_cover(raw)
    elif family == "ape":
        for field, key in _APE_KEYS.items():
            setattr(tags, field, _first(raw.get(key)))
        tags.lyrics = _first(raw.get("Lyrics"))
        tags.cover, tags.cover_mime = _read_ape_cover(raw)
    elif family == "mp4":
        for field, key in _MP4_KEYS.items():
            setattr(tags, field, _first(raw.get(key)))
        track = raw.get("trkn")
        if track:
            num = track[0][0] if track[0] else 0
            total = track[0][1] if len(track[0]) > 1 else 0
            tags.track = f"{num}/{total}" if total else (str(num) if num else "")
        disk = raw.get("disk")
        if disk:
            num = disk[0][0] if disk[0] else 0
            total = disk[0][1] if len(disk[0]) > 1 else 0
            tags.disc = f"{num}/{total}" if total else (str(num) if num else "")
        tags.lyrics = _first(raw.get("\xa9lyr"))
        tags.cover, tags.cover_mime = _read_mp4_cover(raw)
    elif family == "asf":
        for field, key in (
            ("title", "Title"), ("artist", "Author"), ("album", "WM/AlbumTitle"),
            ("genre", "WM/Genre"), ("date", "WM/Year"), ("track", "WM/TrackNumber"),
        ):
            setattr(tags, field, _first(raw.get(key)))

    return tags


def _family(obj: Any) -> str:
    """根据 tag 对象所属模块判定标签族系。"""
    mod = _module_name(obj)
    if mod in ("flac", "oggvorbis", "oggopus", "oggflac", "oggspeex", "oggtheora"):
        return "vorbis"
    if mod in ("mp3", "wave", "aiff", "trueaudio", "dsf", "dsdiff", "aac", "ac3"):
        return "id3"
    if mod in ("monkeysaudio", "wavpack", "musepack", "optimfrog", "tak", "mpc", "apev2"):
        return "ape"
    if mod == "mp4":
        return "mp4"
    if mod == "asf":
        return "asf"
    # 兜底：看 tags 类型
    tags = getattr(obj, "tags", None)
    if tags is not None:
        tmod = _module_name(tags)
        if tmod == "id3":
            return "id3"
        if tmod == "apev2":
            return "ape"
        if tmod in ("mp4",):
            return "mp4"
    return "unknown"


# ---------------------------------------------------------------- 封面读取


def _read_vorbis_cover(obj: Any, raw: Any) -> tuple[Optional[bytes], str]:
    try:
        pictures = getattr(obj, "pictures", None)
        if pictures:
            # 优先封面类型（type 3），否则第一张
            for pic in pictures:
                if getattr(pic, "type", 0) == 3:
                    return bytes(pic.data), pic.mime or _sniff_mime(pic.data)
            return bytes(pictures[0].data), pictures[0].mime or _sniff_mime(pictures[0].data)
    except Exception:
        pass
    # OGG 常用 metadata_block_picture
    try:
        blocks = raw.get("metadata_block_picture")
        if blocks:
            from mutagen.flac import Picture

            data = base64.b64decode(_first_block(blocks))
            pic = Picture(data)
            return bytes(pic.data), pic.mime or _sniff_mime(pic.data)
    except Exception:
        pass
    return None, ""


def _first_block(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return value[0]
    return value


def _read_id3_cover(raw: Any) -> tuple[Optional[bytes], str]:
    try:
        frames = raw.getall("APIC")
        if frames:
            chosen = next((f for f in frames if getattr(f, "type", 0) == 3), frames[0])
            return bytes(chosen.data), chosen.mime or _sniff_mime(chosen.data)
    except Exception:
        pass
    return None, ""


def _read_ape_cover(raw: Any) -> tuple[Optional[bytes], str]:
    try:
        item = raw.get("Cover Art (Front)")
        if item is not None:
            payload = bytes(item.value)
            if b"\x00" in payload:
                _, data = payload.split(b"\x00", 1)
                return data, _sniff_mime(data)
            return payload, _sniff_mime(payload)
    except Exception:
        pass
    return None, ""


def _read_mp4_cover(raw: Any) -> tuple[Optional[bytes], str]:
    try:
        covers = raw.get("covr")
        if covers:
            data = bytes(covers[0])
            fmt = getattr(covers[0], "imageformat", None)
            mime = "image/png" if fmt == 14 else "image/jpeg"
            return data, mime
    except Exception:
        pass
    return None, ""


def _read_id3_lyrics(raw: Any) -> str:
    try:
        frames = raw.getall("USLT")
        if frames:
            return str(frames[0].text or "").strip()
    except Exception:
        pass
    return ""


def _sniff_mime(data: Optional[bytes]) -> str:
    if not data:
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"GIF8":
        return "image/gif"
    if data[:4] == b"RIFF":
        return "image/webp"
    return "image/jpeg"


# ---------------------------------------------------------------- 写入


class TagWriteResult:
    def __init__(self, ok: bool, message: str = "", written: list[str] | None = None):
        self.ok = ok
        self.message = message
        self.written = written or []


def write_tags(
    path: Path | str,
    tags: Tags,
    *,
    id3_version: int = 4,
    compat_mode: bool = False,
    clear_cover_when_none: bool = False,
) -> TagWriteResult:
    """把 ``tags`` 写入文件标签区（不重编码音频）。

    ``compat_mode``：老播放器兼容 —— ID3 统一降为 v2.3 + UTF-16 编码。
    """
    path = Path(path)
    obj = open_audio(path)
    if obj is None:
        return TagWriteResult(False, "无法解析该文件（格式不支持或文件损坏）")
    if not is_writable(obj):
        return TagWriteResult(False, f"{type(obj).__name__} 标签为只读格式，已跳过写入")

    family = _family(obj)
    written: list[str] = []
    try:
        if family == "vorbis":
            _write_vorbis(obj, tags, written, clear_cover_when_none)
        elif family == "id3":
            _write_id3(obj, tags, written, id3_version, compat_mode, clear_cover_when_none)
        elif family == "ape":
            _write_ape(obj, tags, written, clear_cover_when_none)
        elif family == "mp4":
            _write_mp4(obj, tags, written, clear_cover_when_none)
        elif family == "asf":
            _write_asf(obj, tags, written)
        else:
            return TagWriteResult(False, "未知标签体系，已跳过写入")

        obj.save()
        return TagWriteResult(True, "标签写入成功", written)
    except Exception as exc:  # noqa: BLE001 - 需要把底层异常原样上报到日志
        return TagWriteResult(False, f"写入失败：{exc}", written)


def _write_vorbis(obj: Any, tags: Tags, written: list[str], clear_cover: bool) -> None:
    if obj.tags is None:
        obj.add_tags()
    raw = obj.tags
    for field, key in _VORBIS_KEYS.items():
        value = tags.get(field)
        if field == "comment" and not value:
            continue
        if value:
            raw[key] = [value]
            written.append(field)
        elif key in raw and field != "comment":
            del raw[key]

    lyrics = (tags.lyrics or "").strip()
    if lyrics:
        raw["LYRICS"] = [lyrics]
        written.append("lyrics")

    if tags.cover or clear_cover:
        try:
            from mutagen.flac import Picture

            if hasattr(obj, "clear_pictures") and hasattr(obj, "add_picture"):
                obj.clear_pictures()
                if tags.cover:
                    pic = Picture()
                    pic.type = 3
                    pic.mime = tags.cover_mime or _sniff_mime(tags.cover)
                    pic.desc = ""
                    pic.data = tags.cover
                    obj.add_picture(pic)
                    written.append("cover")
            else:
                # OGG 系列走 metadata_block_picture
                if tags.cover:
                    pic = Picture()
                    pic.type = 3
                    pic.mime = tags.cover_mime or _sniff_mime(tags.cover)
                    pic.desc = ""
                    pic.data = tags.cover
                    raw["metadata_block_picture"] = [
                        base64.b64encode(pic.write()).decode("ascii")
                    ]
                    written.append("cover")
                elif "metadata_block_picture" in raw:
                    del raw["metadata_block_picture"]
        except Exception:
            pass


def _id3_text_encoding(compat_mode: bool) -> int:
    from mutagen.id3 import Encoding

    return Encoding.UTF16 if compat_mode else Encoding.UTF8


def _write_id3(
    obj: Any,
    tags: Tags,
    written: list[str],
    id3_version: int,
    compat_mode: bool,
    clear_cover: bool,
) -> None:
    from mutagen.id3 import (
        APIC,
        COMM,
        TALB,
        TCON,
        TDRC,
        TIT2,
        TPE1,
        TPE2,
        TPOS,
        TRCK,
        USLT,
    )

    if obj.tags is None:
        obj.add_tags()
    raw = obj.tags
    # v2.3 不支持 UTF-8 文本帧，强制 UTF-16
    try:
        current_version = int(getattr(raw, "version", (2, 4, 0))[0:2][0])
    except Exception:
        current_version = 4
    enc = _id3_text_encoding(compat_mode or current_version < 4)

    frame_map = {
        "title": TIT2,
        "artist": TPE1,
        "album": TALB,
        "albumartist": TPE2,
        "date": TDRC,
        "genre": TCON,
        "track": TRCK,
        "disc": TPOS,
    }
    for field, frame_cls in frame_map.items():
        value = tags.get(field)
        key = _ID3_KEYS[field]
        if value:
            raw.delall(key)
            raw.add(frame_cls(encoding=enc, text=[value]))
            written.append(field)
        elif key in raw:
            raw.delall(key)

    comment = (tags.comment or "").strip()
    raw.delall("COMM")
    if comment:
        raw.add(COMM(encoding=enc, lang="XXX", desc="", text=[comment]))
        written.append("comment")

    lyrics = (tags.lyrics or "").strip()
    raw.delall("USLT")
    if lyrics:
        raw.add(USLT(encoding=enc, lang="chi", desc="", text=lyrics))
        written.append("lyrics")

    if tags.cover or clear_cover:
        raw.delall("APIC")
        if tags.cover:
            raw.add(
                APIC(
                    encoding=3,
                    mime=tags.cover_mime or _sniff_mime(tags.cover),
                    type=3,
                    desc="",
                    data=tags.cover,
                )
            )
            written.append("cover")

    # 版本与编码设置
    try:
        target = 3 if compat_mode else (id3_version or 4)
        raw.update_to_v23() if target == 3 else raw.update_to_v24()
    except Exception:
        pass


def _write_ape(obj: Any, tags: Tags, written: list[str], clear_cover: bool) -> None:
    from mutagen.apev2 import APEValue, BINARY

    if obj.tags is None:
        obj.add_tags()
    raw = obj.tags
    for field, key in _APE_KEYS.items():
        value = tags.get(field)
        if value:
            raw[key] = value
            written.append(field)
        elif key in raw:
            del raw[key]

    lyrics = (tags.lyrics or "").strip()
    if lyrics:
        raw["Lyrics"] = lyrics
        written.append("lyrics")

    if tags.cover or clear_cover:
        if "Cover Art (Front)" in raw:
            del raw["Cover Art (Front)"]
        if tags.cover:
            ext = "png" if (tags.cover_mime or "").endswith("png") else "jpg"
            raw["Cover Art (Front)"] = APEValue(
                f"cover.{ext}".encode("utf-8") + b"\x00" + tags.cover, BINARY
            )
            written.append("cover")


def _write_mp4(obj: Any, tags: Tags, written: list[str], clear_cover: bool) -> None:
    from mutagen.mp4 import MP4Cover

    if obj.tags is None:
        obj.add_tags()
    raw = obj.tags
    for field, key in _MP4_KEYS.items():
        value = tags.get(field)
        if value:
            raw[key] = [value]
            written.append(field)
        elif key in raw:
            del raw[key]

    def _pair(text: str) -> list[tuple[int, int]]:
        num, _, total = (text or "").partition("/")
        try:
            n = int(num) if num.strip() else 0
            t = int(total) if total.strip() else 0
        except ValueError:
            n, t = 0, 0
        return [(n, t)]

    if tags.track:
        raw["trkn"] = _pair(tags.track)
        written.append("track")
    if tags.disc:
        raw["disk"] = _pair(tags.disc)
        written.append("disc")

    lyrics = (tags.lyrics or "").strip()
    if lyrics:
        raw["\xa9lyr"] = [lyrics]
        written.append("lyrics")

    if tags.cover or clear_cover:
        if "covr" in raw:
            del raw["covr"]
        if tags.cover:
            fmt = (
                MP4Cover.FORMAT_PNG
                if (tags.cover_mime or "").endswith("png")
                else MP4Cover.FORMAT_JPEG
            )
            raw["covr"] = [MP4Cover(tags.cover, imageformat=fmt)]
            written.append("cover")


def _write_asf(obj: Any, tags: Tags, written: list[str]) -> None:
    if obj.tags is None:
        obj.add_tags()
    raw = obj.tags
    mapping = {
        "title": "Title",
        "artist": "Author",
        "album": "WM/AlbumTitle",
        "genre": "WM/Genre",
        "date": "WM/Year",
        "track": "WM/TrackNumber",
        "comment": "Description",
        "lyrics": "WM/Lyrics",
    }
    for field, key in mapping.items():
        value = tags.get(field)
        if value:
            raw[key] = value
            written.append(field)
        elif key in raw:
            del raw[key]
