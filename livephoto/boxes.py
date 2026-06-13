"""Builders for the QuickTime boxes that turn an ordinary MOV into a Live
Photo movie.

Two things are required by iOS:

1. A movie level ``meta`` box carrying the metadata key
   ``com.apple.quicktime.content.identifier`` whose value matches the
   ContentIdentifier stored in the still image's EXIF maker note.
2. A timed metadata track (sample format ``mebx``) carrying the key
   ``com.apple.quicktime.still-image-time``.  A single sample with the value
   ``0xFF`` marks the still frame.

The byte layouts below were reverse engineered from genuine Live Photo
samples produced by an iPhone.
"""

from __future__ import annotations

import struct

from .mp4 import Box

STILL_IMAGE_TIME_KEY = b"com.apple.quicktime.still-image-time"
CONTENT_IDENTIFIER_KEY = b"com.apple.quicktime.content.identifier"
STILL_IMAGE_TRANSFORM_KEY = b"com.apple.quicktime.live-photo-still-image-transform"
LIVE_PHOTO_INFO_KEY = b"com.apple.quicktime.live-photo-info"


def _box(box_type: bytes, payload: bytes) -> Box:
    return Box(box_type, payload=payload)


def _container(box_type: bytes, children) -> Box:
    return Box(box_type, is_container=True, children=list(children))


# ---------------------------------------------------------------------------
# Movie level content identifier  (moov/meta)
# ---------------------------------------------------------------------------
def build_content_identifier_meta(identifier: str) -> Box:
    """Build a QuickTime ``meta`` box holding the content.identifier value."""
    ident = identifier.encode("ascii")

    # hdlr: handler type 'mdta'
    hdlr_payload = (
        b"\x00\x00\x00\x00"          # version + flags
        b"\x00\x00\x00\x00"          # predefined
        b"mdta"                      # handler type
        + b"\x00" * 12               # reserved (3 x uint32) + empty name
    )
    hdlr = _box(b"hdlr", hdlr_payload)

    # keys: one key entry of namespace 'mdta'
    key_entry = struct.pack(">I", 8 + len(CONTENT_IDENTIFIER_KEY)) + b"mdta" + CONTENT_IDENTIFIER_KEY
    keys_payload = (
        b"\x00\x00\x00\x00"          # version + flags
        + struct.pack(">I", 1)       # entry count
        + key_entry
    )
    keys = _box(b"keys", keys_payload)

    # ilst: value for key index 1, stored in a 'data' box (type 1 = UTF-8)
    data_payload = (
        struct.pack(">I", 1)         # type 1 = UTF-8 text
        + b"\x00\x00\x00\x00"        # locale
        + ident
    )
    data_box = _box(b"data", data_payload)
    # The item box type is the 1-based key index (here: 0x00000001)
    item = _container(struct.pack(">I", 1), [data_box])
    ilst = _container(b"ilst", [item])

    return _container(b"meta", [hdlr, keys, ilst])


# ---------------------------------------------------------------------------
# Still image time metadata track  (moov/trak)
# ---------------------------------------------------------------------------
# The still-image-time sample carries two metadata items so iOS treats the
# clip as a fully featured Live Photo (required for wallpaper use):
#   item 1: still-image-time                      -> 1 byte 0xFF
#   item 2: live-photo-still-image-transform       -> 3x3 identity matrix
_IDENTITY_TRANSFORM = struct.pack(
    ">9d", 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0
)  # 72-byte 3x3 identity matrix (no padding, matching genuine samples)

STILL_IMAGE_TIME_SAMPLE = (
    struct.pack(">I", 9) + struct.pack(">I", 1) + b"\xff"
    + struct.pack(">I", 8 + len(_IDENTITY_TRANSFORM))
    + struct.pack(">I", 2) + _IDENTITY_TRANSFORM
)


def _key_entry(local_key_id: int, namespace: bytes, key: bytes,
               dtyp_value: bytes) -> bytes:
    """Build one mebx key entry.

    Genuine layout (per entry, no shared header in the keys box):
        size(4) local_key_id(4) [keyd box] [dtyp box]
    where the sample data references ``local_key_id``.
    """
    keyd = struct.pack(">I", 8 + len(namespace) + len(key)) + b"keyd" + namespace + key
    dtyp = struct.pack(">I", 8 + len(dtyp_value)) + b"dtyp" + dtyp_value
    body = struct.pack(">I", local_key_id) + keyd + dtyp
    return struct.pack(">I", len(body) + 4) + body


def _mebx_stsd(entries) -> Box:
    """Sample description ('mebx') declaring the given metadata key entries.

    ``entries`` is a list of (local_key_id, namespace, key, dtyp_value) tuples.
    The 'keys' box body is the concatenation of the entries (no version/flags
    or count field, unlike the movie-level 'keys' box).
    """
    keys_entries = b"".join(
        _key_entry(kid, ns, k, dt) for kid, ns, k, dt in entries)
    keys_box = struct.pack(">I", len(keys_entries) + 8) + b"keys" + keys_entries

    mebx_body = (
        b"\x00\x00\x00\x00\x00\x00"  # reserved
        + struct.pack(">H", 1)       # data reference index
        + keys_box
    )
    mebx_entry = struct.pack(">I", len(mebx_body) + 8) + b"mebx" + mebx_body
    stsd_payload = (
        b"\x00\x00\x00\x00"          # version + flags
        + struct.pack(">I", 1)       # entry count
        + mebx_entry
    )
    return _box(b"stsd", stsd_payload)


def _still_image_time_stsd() -> Box:
    # dtyp 0x41 = signed 8-bit int (still-image-time);
    # dtyp 0x53 matches the transform blob type in genuine samples.
    return _mebx_stsd([
        (1, b"mdta", STILL_IMAGE_TIME_KEY,
         b"\x00\x00\x00\x00\x00\x00\x00\x41"),
        (2, b"mdta", STILL_IMAGE_TRANSFORM_KEY,
         b"\x00\x00\x00\x00\x00\x00\x00\x53"),
    ])


def _still_image_time_only_stsd() -> Box:
    """Single-key still-image-time stsd (audio-synced track, no transform)."""
    return _mebx_stsd([
        (1, b"mdta", STILL_IMAGE_TIME_KEY,
         b"\x00\x00\x00\x00\x00\x00\x00\x41"),
    ])


# Sample for the single-key still-image-time track: one item, value 0xFF.
STILL_IMAGE_TIME_ONLY_SAMPLE = struct.pack(">I", 9) + struct.pack(">I", 1) + b"\xff"


def _metadata_track(track_id: int, media_timescale: int,
                    movie_timescale: int, duration_seconds: float,
                    stsd: Box, samples, empty_edit_seconds: float = 0.0,
                    fixed_sample_duration: int = 0) -> Box:
    """Build a generic timed-metadata ('mebx') trak spanning the clip.

    ``duration_seconds``  total presented duration of the track.
    ``empty_edit_seconds`` leading empty-edit (media_time=-1) before samples,
                           used to position e.g. the still frame.
    ``fixed_sample_duration`` if > 0, each sample lasts exactly this many media
                           ticks (matching genuine clips); otherwise the media
                           duration is split evenly across the samples.
    ``stco`` is a placeholder (0), patched once the mdat layout is known.
    """
    ts = media_timescale
    n = len(samples)
    if fixed_sample_duration > 0:
        per_sample = fixed_sample_duration
    else:
        media_dur = max(1, int(round(duration_seconds * ts)))
        per_sample = max(1, media_dur // n)
    media_dur = per_sample * n
    # The presented (edit-list) duration of the samples in the movie timescale
    # is derived from the actual media duration, not the requested clip length.
    samples_movie_dur = int(round(media_dur / ts * movie_timescale))
    empty_movie_dur = int(round(empty_edit_seconds * movie_timescale))
    movie_dur = empty_movie_dur + samples_movie_dur

    # --- tkhd (flags 0x0F = enabled|in movie|in preview|in poster) ---------
    # v0 tkhd body (80 bytes): creation, modification, track_id, reserved,
    # duration, reserved(8), layer, alt_group, volume, reserved, matrix(36),
    # width, height.
    tkhd_body = struct.pack(
        ">IIIII", 0, 0, track_id, 0, movie_dur)        # 20 bytes
    tkhd_body += struct.pack(">II", 0, 0)              # reserved (8)
    tkhd_body += struct.pack(">hhhh", 0, 0, 0, 0)      # layer, alt, volume, res
    matrix = struct.pack(">9i", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)
    tkhd_body += matrix + struct.pack(">II", 0, 0)     # matrix + width/height
    tkhd = _box(b"tkhd", b"\x00\x00\x00\x0f" + tkhd_body)

    # --- edts/elst (optional leading empty edit, then the samples) ---------
    elst_entries = []
    if empty_edit_seconds > 0:
        elst_entries.append((empty_movie_dur, -1, 0x10000))
    elst_entries.append((samples_movie_dur, 0, 0x10000))
    elst_body = struct.pack(">I", len(elst_entries))
    for d, mt, rate in elst_entries:
        elst_body += struct.pack(">IiI", d, mt, rate)
    elst = _box(b"elst", b"\x00\x00\x00\x00" + elst_body)
    edts = _container(b"edts", [elst])

    # --- mdhd ---------------------------------------------------------------
    mdhd_body = struct.pack(">IIIIHH", 0, 0, ts, media_dur, 0x55C4, 0)
    mdhd = _box(b"mdhd", b"\x00\x00\x00\x00" + mdhd_body)

    # --- hdlr (metadata) ----------------------------------------------------
    name = b"Core Media Metadata"
    hdlr_body = (
        b"mhlr" + b"meta" + b"appl"
        + b"\x00\x00\x00\x01" + b"\x00\x00\x00\x00"
        + bytes([len(name)]) + name
    )
    hdlr = _box(b"hdlr", b"\x00\x00\x00\x00" + hdlr_body)

    # --- minf/gmhd/gmin -----------------------------------------------------
    gmin_body = struct.pack(">HHHHHH", 0x0040, 0x8000, 0x8000, 0x8000, 0, 0)
    gmin = _box(b"gmin", b"\x00\x00\x00\x00" + gmin_body)
    gmhd = _container(b"gmhd", [gmin])

    dname = b"Core Media Data Handler"
    dh_body = (
        b"dhlr" + b"alis" + b"appl"
        + b"\x00\x00\x00\x00" + b"\x00\x00\x00\x00"
        + bytes([len(dname)]) + dname
    )
    minf_hdlr = _box(b"hdlr", b"\x00\x00\x00\x00" + dh_body)
    url_box = struct.pack(">I", 12) + b"alis" + b"\x00\x00\x00\x01"
    dref = _box(b"dref", b"\x00\x00\x00\x00" + struct.pack(">I", 1) + url_box)
    dinf = _container(b"dinf", [dref])

    # --- stbl ---------------------------------------------------------------
    stts = _box(b"stts", b"\x00\x00\x00\x00"
                + struct.pack(">III", 1, n, per_sample))
    # all samples in a single chunk
    stsc = _box(b"stsc", b"\x00\x00\x00\x00" + struct.pack(">IIII", 1, 1, n, 1))
    same_size = len(samples[0]) if samples and all(
        len(s) == len(samples[0]) for s in samples) else 0
    if same_size:
        stsz = _box(b"stsz", b"\x00\x00\x00\x00"
                    + struct.pack(">II", same_size, n))
    else:
        stsz = _box(b"stsz", b"\x00\x00\x00\x00" + struct.pack(">II", 0, n)
                    + b"".join(struct.pack(">I", len(s)) for s in samples))
    stco = _box(b"stco", b"\x00\x00\x00\x00" + struct.pack(">II", 1, 0))
    stbl = _container(b"stbl", [stsd, stts, stsc, stsz, stco])

    minf = _container(b"minf", [gmhd, minf_hdlr, dinf, stbl])
    mdia = _container(b"mdia", [mdhd, hdlr, minf])
    return _container(b"trak", [tkhd, edts, mdia])


def build_still_image_time_track(track_id: int, movie_timescale: int,
                                 duration_seconds: float,
                                 still_time_seconds: float = 0.0) -> Box:
    """Track marking the still frame (still-image-time + transform).

    Genuine iPhone clips mark the still frame with a single, essentially
    instantaneous sample (media duration of one tick) positioned by a leading
    empty edit.  We replicate that: an empty edit up to ``still_time_seconds``
    then a 1-tick marker sample.
    """
    return _metadata_track(
        track_id=track_id,
        media_timescale=600,
        movie_timescale=movie_timescale,
        duration_seconds=1.0 / 600,           # one tick -> instantaneous marker
        stsd=_still_image_time_stsd(),
        samples=[STILL_IMAGE_TIME_SAMPLE],
        empty_edit_seconds=still_time_seconds,
    )


def build_still_image_time_audio_track(track_id: int, movie_timescale: int,
                                       still_time_seconds: float = 0.0) -> Box:
    """Second still-image-time track, synced to the audio (44100) timescale.

    Genuine iPhone clips carry this single-key still-image-time track in
    addition to the transform-bearing one.
    """
    return _metadata_track(
        track_id=track_id,
        media_timescale=44100,
        movie_timescale=movie_timescale,
        duration_seconds=88.0 / 44100,        # ~one audio frame, like genuine
        stsd=_still_image_time_only_stsd(),
        samples=[STILL_IMAGE_TIME_ONLY_SAMPLE],
        empty_edit_seconds=still_time_seconds,
    )


def build_live_photo_info_track(track_id: int, movie_timescale: int,
                                duration_seconds: float,
                                num_frames: int = 0) -> Box:
    """Track carrying per-frame live-photo-info (enables wallpaper use).

    Uses the captured iPhone setup/sample templates (see templates.py).  All
    frames carry the same motion blob, which is sufficient for iOS to accept
    the clip as a wallpaper-capable Live Photo.  The track is a fixed 60 fps
    metadata stream (1000 ticks per sample at a 60000 timescale), exactly like
    genuine clips.
    """
    from . import templates
    ts = templates.LIVE_PHOTO_INFO_TIMESCALE          # 60000
    sample_ticks = ts // 60                            # 1000 ticks => 60 fps
    empty = 0.05                                        # ~50ms leading empty edit
    if num_frames <= 0:
        span = max(0.0, duration_seconds - empty)
        num_frames = max(1, int(round(span * ts / sample_ticks)))
    stsd = _box(b"stsd", templates.LIVE_PHOTO_INFO_STSD)
    samples = [templates.LIVE_PHOTO_INFO_SAMPLE] * num_frames
    return _metadata_track(
        track_id=track_id,
        media_timescale=ts,
        movie_timescale=movie_timescale,
        duration_seconds=duration_seconds,
        stsd=stsd,
        samples=samples,
        empty_edit_seconds=empty,
        fixed_sample_duration=sample_ticks,
    )
