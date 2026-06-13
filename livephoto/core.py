"""High level Live Photo creation: pairs a still image with a .MOV so iOS
Photos recognises them as a single Live Photo.

The two files are linked by a shared *content identifier* (a UUID):
  * stored in the MOV inside ``moov/meta`` under
    ``com.apple.quicktime.content.identifier`` (see :mod:`writer`), and
  * stored in the still image's EXIF Apple maker note (tag 0x0011).
"""

from __future__ import annotations

import os
import struct
import subprocess
import uuid
from typing import Optional

import piexif

from . import ffmpeg
from .writer import inject


def new_content_identifier() -> str:
    """Return an upper-case UUID string as used by iOS Live Photos."""
    return str(uuid.uuid4()).upper()


# ---------------------------------------------------------------------------
# Still image side
# ---------------------------------------------------------------------------
def _build_apple_maker_note(identifier: str) -> bytes:
    """Build an Apple iOS maker note carrying the ContentIdentifier (0x0011).

    Offsets inside the maker note are relative to its own start, matching the
    layout produced by iPhones.
    """
    ident = identifier.encode("ascii") + b"\x00"   # NUL terminated ASCII
    header = b"Apple iOS\x00\x00\x01MM"            # 14 byte signature (big-endian)
    # Single IFD entry; value stored after the IFD at offset 32.
    value_offset = len(header) + 2 + 12 + 4         # =32
    entry = struct.pack(">HHI", 0x0011, 2, len(ident)) + struct.pack(">I", value_offset)
    ifd = struct.pack(">H", 1) + entry + struct.pack(">I", 0)  # count + entry + next-IFD
    return header + ifd + ident


def _exif_with_identifier(base_exif: Optional[bytes], identifier: str) -> bytes:
    if base_exif:
        try:
            exif_dict = piexif.load(base_exif)
        except Exception:
            exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}
    else:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}, "thumbnail": None}

    exif_dict.setdefault("Exif", {})
    exif_dict["Exif"][piexif.ExifIFD.MakerNote] = _build_apple_maker_note(identifier)
    return piexif.dump(exif_dict)


def _write_still(image_path: str, out_path: str, identifier: str, fmt: str):
    """Create the still image at ``out_path`` embedding the identifier.

    ``fmt`` is 'heic' or 'jpeg'.
    """
    from PIL import Image
    import pillow_heif
    pillow_heif.register_heif_opener()

    img = Image.open(image_path)
    base_exif = img.info.get("exif")
    exif_bytes = _exif_with_identifier(base_exif, identifier)

    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")

    if fmt == "heic":
        # iOS decodes HEIC colour via the nclx colour box.  If we only embed an
        # ICC profile (the default when the source carries one) the YCbCr->RGB
        # matrix is undefined for Apple's decoder and the photo shows up
        # grayscale.  So we drop any ICC profile and write an explicit sRGB
        # nclx profile (primaries=1 BT.709, transfer=13 sRGB, matrix=6 BT.601,
        # full range) which matches what iPhones produce.
        heif = pillow_heif.from_pillow(img)
        heif.info.pop("icc_profile", None)
        heif.info.pop("icc_profile_type", None)
        heif.save(
            out_path,
            quality=100,
            exif=exif_bytes,
            save_nclx_profile=True,
            matrix_coefficients=6,
            color_primaries=1,
            transfer_characteristic=13,
            full_range_flag=1,
        )
        # Some libheif/x265 builds declare only one channel in the 'pixi' box
        # even though the HEVC data is full colour, which makes iOS render the
        # photo greyscale.  Rewrite pixi to 3 channels.
        from .heic_fix import fix_pixi_channels
        fix_pixi_channels(out_path)
    elif fmt == "jpeg":
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(out_path, format="JPEG", exif=exif_bytes, quality=100)
    else:
        raise ValueError(f"unsupported image format: {fmt}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def make_live_photo(
    video_path: str,
    output_dir: str,
    image_path: Optional[str] = None,
    image_format: str = "heic",
    duration: Optional[float] = None,
    basename: Optional[str] = None,
    still_time: Optional[float] = None,
    crf: int = None,
    bitrate: int = 24,  # mbps
    maxrate: float = 24, # mbps
    minrate: float = 24, # mbps
    bufsize: float = 24, # mbps
) -> dict:
    """Convert ``video_path`` (+ optional ``image_path``) into a Live Photo.

    Returns a dict with keys ``still``, ``video`` and ``identifier``.
    """
    os.makedirs(output_dir, exist_ok=True)
    identifier = new_content_identifier()

    stem = basename or os.path.splitext(os.path.basename(video_path))[0]
    ext = ".heic" if image_format == "heic" else ".jpg"
    still_out = os.path.join(output_dir, stem + ext)
    video_out = os.path.join(output_dir, stem + ".MOV")

    # 1. Determine the still source.  If no image supplied, grab a frame.
    extracted = None
    if image_path is None:
        extracted = os.path.join(output_dir, stem + "_frame.png")
        ffmpeg.extract_frame(video_path, extracted, at=still_time or 0.0)
        image_path = extracted

    # 2. Build the still image with the embedded identifier.
    _write_still(image_path, still_out, identifier, image_format)

    # 3. Transcode the video to an iOS friendly MOV.
    transcoded = os.path.join(output_dir, stem + "_tmp.mov")
    ffmpeg.transcode_for_livephoto(video_path, 
                                   transcoded, 
                                   duration=duration, 
                                   crf=crf, 
                                   bitrate=bitrate, 
                                   maxrate=maxrate, 
                                   minrate=minrate, 
                                   bufsize=bufsize
                                   )

    # 4. Inject the Live Photo metadata (content id + still-image-time track).
    with open(transcoded, "rb") as fh:
        mov_bytes = fh.read()
    final = inject(mov_bytes, identifier)
    with open(video_out, "wb") as fh:
        fh.write(final)

    # 5. Clean up temporaries.
    for tmp in (transcoded, extracted):
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    return {"still": still_out, "video": video_out, "identifier": identifier}
