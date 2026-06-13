"""Minimal MP4/QuickTime atom (box) parser and writer.

Only the pieces needed for Live Photo generation are implemented:
  * recursive parsing of the box tree
  * locating boxes by path
  * serialising a (possibly modified) tree back to bytes
  * helpers to build the boxes required to mark a MOV as a Live Photo
"""

from __future__ import annotations

import struct
from typing import List, Optional

# Boxes that contain other boxes (containers).
CONTAINER_TYPES = {
    b"moov", b"trak", b"mdia", b"minf", b"stbl", b"udta",
    b"edts", b"dinf", b"gmhd", b"mvex", b"moof", b"traf",
    b"meta",  # In QuickTime (MOV) `meta` is a plain container (no ver/flags).
}
# ISO-BMFF style `meta` carries a 4 byte version/flags prefix before children;
# QuickTime does not.  We auto-detect at parse time (see _parse_children).
FULLBOX_CONTAINER_TYPES: set = set()


class Box:
    """A single MP4 box.

    For container boxes ``children`` holds the parsed sub-boxes and ``payload``
    is empty.  For leaf boxes ``payload`` holds the raw bytes after the header.
    """

    __slots__ = ("type", "payload", "children", "is_container", "fullbox_prefix")

    def __init__(self, box_type: bytes, payload: bytes = b"",
                 children: Optional[List["Box"]] = None,
                 is_container: bool = False, fullbox_prefix: bytes = b""):
        self.type = box_type
        self.payload = payload
        self.children = children if children is not None else []
        self.is_container = is_container
        # For `meta`: the 4 byte version+flags that precede the children.
        self.fullbox_prefix = fullbox_prefix

    # -- serialisation ----------------------------------------------------
    def body(self) -> bytes:
        if self.is_container:
            inner = self.fullbox_prefix + b"".join(c.serialize() for c in self.children)
            return inner
        return self.payload

    def serialize(self) -> bytes:
        body = self.body()
        size = len(body) + 8
        if size > 0xFFFFFFFF:
            # 64-bit size
            return struct.pack(">I", 1) + self.type + struct.pack(">Q", size + 8) + body
        return struct.pack(">I", size) + self.type + body

    # -- navigation -------------------------------------------------------
    def find(self, path: str) -> Optional["Box"]:
        """Find first descendant matching a '/' separated type path."""
        parts = path.split("/")
        node = self
        for p in parts:
            key = p.encode("latin1")
            nxt = None
            for c in node.children:
                if c.type == key:
                    nxt = c
                    break
            if nxt is None:
                return None
            node = nxt
        return node

    def find_all(self, box_type: bytes) -> List["Box"]:
        out = []
        for c in self.children:
            if c.type == box_type:
                out.append(c)
            if c.is_container:
                out.extend(c.find_all(box_type))
        return out

    def __repr__(self):
        return f"<Box {self.type!r} container={self.is_container} size={len(self.body())+8}>"


def _parse_children(data: bytes, start: int, end: int) -> List[Box]:
    boxes = []
    off = start
    while off + 8 <= end:
        size = struct.unpack(">I", data[off:off + 4])[0]
        btype = data[off + 4:off + 8]
        header = 8
        if size == 1:
            size = struct.unpack(">Q", data[off + 8:off + 16])[0]
            header = 16
        elif size == 0:
            size = end - off
        box_end = off + size
        if box_end > end or size < header:
            # Malformed / trailing data: treat the rest as an opaque leaf.
            payload = data[off + 8:end]
            boxes.append(Box(btype, payload=payload))
            break
        body_start = off + header
        if btype == b"meta":
            # Detect QuickTime (no version/flags) vs ISO-BMFF (4 byte prefix)
            # by checking whether the first child header is a valid box.
            prefix = b""
            child_start = body_start
            # ISO style: bytes [4:8] of body would be the first child's type,
            # while QuickTime puts the first child ('hdlr') right at body_start.
            if data[body_start + 4:body_start + 8] != b"hdlr" and \
               data[body_start + 8:body_start + 12] == b"hdlr":
                prefix = data[body_start:body_start + 4]
                child_start = body_start + 4
            children = _parse_children(data, child_start, box_end)
            boxes.append(Box(btype, is_container=True, children=children,
                             fullbox_prefix=prefix))
        elif btype in CONTAINER_TYPES:
            children = _parse_children(data, body_start, box_end)
            boxes.append(Box(btype, is_container=True, children=children))
        elif btype in FULLBOX_CONTAINER_TYPES:
            prefix = data[body_start:body_start + 4]
            children = _parse_children(data, body_start + 4, box_end)
            boxes.append(Box(btype, is_container=True, children=children,
                             fullbox_prefix=prefix))
        else:
            boxes.append(Box(btype, payload=data[body_start:box_end]))
        off = box_end
    return boxes


def parse(data: bytes) -> Box:
    """Parse a whole file into a synthetic root container box."""
    root = Box(b"ROOT", is_container=True)
    root.children = _parse_children(data, 0, len(data))
    return root


def serialize_tree(root: Box) -> bytes:
    return b"".join(c.serialize() for c in root.children)
