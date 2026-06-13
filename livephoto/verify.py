"""Verify that a still + MOV pair forms a valid iOS Live Photo."""

from __future__ import annotations

import struct
from typing import Optional

import piexif

from . import ffmpeg
from .mp4 import parse


def _parse_keys(keys_raw: bytes):
    """Return ordered list of key strings from a 'keys' box payload bytes.

    keys box: size(4) 'keys' version+flags(4) count(4) [ entry... ]
    entry: size(4) namespace(4) keyvalue(size-8)
    """
    # keys_raw is the serialised box; skip 8 byte header + 4 ver/flags.
    pos = 12
    if len(keys_raw) < 16:
        return []
    count = struct.unpack(">I", keys_raw[pos:pos + 4])[0]
    pos += 4
    out = []
    for _ in range(count):
        if pos + 8 > len(keys_raw):
            break
        size = struct.unpack(">I", keys_raw[pos:pos + 4])[0]
        value = keys_raw[pos + 8:pos + size]
        out.append(value)
        pos += size
    return out


def _parse_ilst(ilst_raw: bytes):
    """Return {key_index: value_bytes} from a serialised 'ilst' box."""
    pos = 8  # skip 'ilst' box header
    result = {}
    while pos + 8 <= len(ilst_raw):
        item_size = struct.unpack(">I", ilst_raw[pos:pos + 4])[0]
        key_index = struct.unpack(">I", ilst_raw[pos + 4:pos + 8])[0]
        item = ilst_raw[pos:pos + item_size]
        didx = item.find(b"data")
        if didx >= 4:
            dlen = struct.unpack(">I", item[didx - 4:didx])[0]
            value = item[didx + 12:didx - 4 + dlen]
            result[key_index] = value
        pos += item_size if item_size else len(ilst_raw)
    return result


def _mov_content_identifier(mov_bytes: bytes) -> Optional[str]:
    root = parse(mov_bytes)
    moov = next((b for b in root.children if b.type == b"moov"), None)
    metas = []
    if moov is not None:
        metas = moov.find_all(b"meta")
    metas += [b for b in root.children if b.type == b"meta"]

    target = b"com.apple.quicktime.content.identifier"
    for meta in metas:
        keys_box = meta.find("keys")
        ilst_box = meta.find("ilst")
        if keys_box is None or ilst_box is None:
            continue
        keys = _parse_keys(keys_box.serialize())
        if target not in keys:
            continue
        key_index = keys.index(target) + 1  # ilst indices are 1-based
        values = _parse_ilst(ilst_box.serialize())
        if key_index in values:
            return values[key_index].decode("utf-8", "replace")
    return None


def _mov_has_still_image_time(mov_bytes: bytes) -> bool:
    root = parse(mov_bytes)
    return b"com.apple.quicktime.still-image-time" in mov_bytes and bool(
        root.find_all(b"trak"))


def _image_content_identifier(image_path: str) -> Optional[str]:
    try:
        import pillow_heif
        from PIL import Image
        pillow_heif.register_heif_opener()
        img = Image.open(image_path)
        exif = img.info.get("exif")
    except Exception:
        exif = None
    if not exif:
        return None
    try:
        d = piexif.load(exif)
        mn = d["Exif"].get(piexif.ExifIFD.MakerNote)
    except Exception:
        return None
    if not mn or not mn.startswith(b"Apple"):
        return None
    # The UUID is stored as ASCII; pull the printable run after the header.
    text = mn.decode("latin1", "replace")
    import re
    m = re.search(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
                  r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", text)
    return m.group(0) if m else None


def verify_pair(still_path: str, video_path: str) -> dict:
    """Return a report dict describing the Live Photo validity of a pair."""
    with open(video_path, "rb") as fh:
        mov_bytes = fh.read()

    mov_id = _mov_content_identifier(mov_bytes)
    img_id = _image_content_identifier(still_path)
    has_sit = _mov_has_still_image_time(mov_bytes)

    info = ffmpeg.probe(video_path)
    vcodec = None
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            vcodec = s.get("codec_tag_string") or s.get("codec_name")
            break
    brand = info.get("format", {}).get("tags", {}).get("major_brand", "")

    checks = {
        "image_has_identifier": img_id is not None,
        "mov_has_identifier": mov_id is not None,
        "identifiers_match": bool(mov_id and img_id and mov_id == img_id),
        "mov_has_still_image_time": has_sit,
    }
    report = {
        "image_identifier": img_id,
        "mov_identifier": mov_id,
        "video_codec_tag": vcodec,
        "major_brand": brand,
        "checks": checks,
        "valid": all(checks.values()),
    }
    return report
