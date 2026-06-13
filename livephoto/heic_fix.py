"""Fix the ``pixi`` (pixel information) box of HEIC files produced by some
libheif/x265 builds.

Those builds encode full-colour HEVC image data but emit a ``pixi`` box that
declares a single (luma-only) channel.  iOS trusts ``pixi`` and therefore
renders the photo as greyscale.  We rewrite ``pixi`` to declare three 8-bit
channels (matching what iPhones produce) and fix up every box size and
``iloc`` offset affected by the 2-byte growth.
"""

from __future__ import annotations

import struct

# Correct pixi body for a 3-channel 8-bit image: ver/flags + count + 3 depths.
_PIXI_3CH_BODY = b"\x00\x00\x00\x00" + bytes([3]) + b"\x08\x08\x08"


def _find_box(data: bytes, box_type: bytes, start: int = 0, end: int | None = None):
    """Return (offset, size) of the first ``box_type`` within [start, end)."""
    if end is None:
        end = len(data)
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", data[off:off + 4])[0]
        btype = data[off + 4:off + 8]
        if size == 1:
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
        if size == 0:
            size = end - off
        if btype == box_type:
            return off, size
        off += size
    return None, None


def _patch_iloc_base_offsets(data: bytearray, threshold: int, delta: int):
    """Add ``delta`` to every iloc base_offset/extent offset >= threshold."""
    j, jsize = _find_box(bytes(data), b"iloc")
    if j is None:
        marker = bytes(data).find(b"iloc")
        if marker < 4:
            return
        j = marker - 4
    p = j + 8  # skip size + 'iloc'
    version = data[p]
    p += 4  # version + flags
    b = data[p]
    offset_size = b >> 4
    length_size = b & 0xF
    p += 1
    b = data[p]
    base_offset_size = b >> 4
    index_size = b & 0xF
    p += 1
    if version < 2:
        item_count = struct.unpack(">H", data[p:p + 2])[0]
        p += 2
    else:
        item_count = struct.unpack(">I", data[p:p + 4])[0]
        p += 4

    def patch_field(pos, n):
        val = int.from_bytes(data[pos:pos + n], "big")
        if val >= threshold:
            data[pos:pos + n] = (val + delta).to_bytes(n, "big")

    for _ in range(item_count):
        p += 2 if version < 2 else 4          # item_ID
        if version in (1, 2):
            p += 2                            # construction_method
        p += 2                                # data_reference_index
        if base_offset_size:
            patch_field(p, base_offset_size)  # base_offset
            p += base_offset_size
        extent_count = struct.unpack(">H", data[p:p + 2])[0]
        p += 2
        for _ in range(extent_count):
            if version in (1, 2) and index_size:
                p += index_size
            if offset_size:
                patch_field(p, offset_size)   # extent_offset (when base==0)
                p += offset_size
            p += length_size


def _grow_box_size(data: bytearray, box_offset: int, delta: int):
    size = struct.unpack(">I", data[box_offset:box_offset + 4])[0]
    data[box_offset:box_offset + 4] = struct.pack(">I", size + delta)


def fix_pixi_channels(path: str) -> bool:
    """Rewrite a 1-channel ``pixi`` box to 3 channels.  Returns True if changed."""
    with open(path, "rb") as fh:
        data = bytearray(fh.read())

    pixi_marker = data.find(b"pixi")
    if pixi_marker < 4:
        return False
    pixi_off = pixi_marker - 4  # box size precedes the type
    pixi_size = struct.unpack(">I", data[pixi_off:pixi_off + 4])[0]

    body = data[pixi_off + 8:pixi_off + pixi_size]
    num_channels = body[4] if len(body) > 4 else 0
    if num_channels == 3:
        return False  # already correct

    delta = len(_PIXI_3CH_BODY) - len(body)  # typically +2

    # Grow every ancestor container that encloses pixi (meta/iprp/ipco).
    # Containers may be nested, so scan recursively for any box whose byte
    # range encloses pixi_off and whose type is a known container.
    _CONTAINERS = (b"meta", b"iprp", b"ipco")

    def grow_ancestors(start: int, end: int):
        off = start
        while off + 8 <= end:
            size = struct.unpack(">I", data[off:off + 4])[0]
            bt = data[off + 4:off + 8]
            hdr = 8
            if size == 1:
                size = struct.unpack(">Q", data[off + 8:off + 16])[0]
                hdr = 16
            if size == 0:
                break
            box_end = off + size
            if off < pixi_off < box_end and off != pixi_off:
                if bt in _CONTAINERS:
                    _grow_box_size(data, off, delta)
                    # meta has a 4-byte version/flags before children.
                    child_start = off + hdr + (4 if bt == b"meta" else 0)
                    grow_ancestors(child_start, box_end)
                    return
            off += size

    grow_ancestors(0, len(data))

    # Fix iloc offsets that point past the pixi box (the media in mdat moves).
    _patch_iloc_base_offsets(data, threshold=pixi_off, delta=delta)

    # Replace the pixi box.
    new_pixi = struct.pack(">I", 8 + len(_PIXI_3CH_BODY)) + b"pixi" + _PIXI_3CH_BODY
    data[pixi_off:pixi_off + pixi_size] = new_pixi

    with open(path, "wb") as fh:
        fh.write(data)
    return True
