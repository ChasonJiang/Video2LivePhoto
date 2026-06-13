"""Inject Live Photo metadata into an existing QuickTime/MOV file.

What iOS needs (reverse engineered from genuine samples):
  * a movie-level ``meta`` box with the key
    ``com.apple.quicktime.content.identifier`` matching the still image, and
  * a timed-metadata track (sample format ``mebx``) carrying
    ``com.apple.quicktime.still-image-time``.

Offset safety
-------------
Every track stores absolute file offsets (``stco``/``co64``) that point into
``mdat``.  We must keep those valid.  Strategy:
  * The new still-image sample goes into a *new* ``mdat`` appended at the very
    end of the file, so it never disturbs the original media.
  * Adding boxes to ``moov`` may shift the original ``mdat`` only when ``moov``
    sits before it; we measure that shift and fix every existing chunk-offset
    table accordingly.
"""

from __future__ import annotations

import struct

from . import boxes
from .mp4 import Box, parse, serialize_tree


def _read_mvhd(mvhd: Box):
    p = mvhd.payload
    if p[0] == 1:
        timescale = struct.unpack(">I", p[20:24])[0]
        duration = struct.unpack(">Q", p[24:32])[0]
    else:
        timescale = struct.unpack(">I", p[12:16])[0]
        duration = struct.unpack(">I", p[16:20])[0]
    return timescale, duration


def _set_mvhd_next_track_id(mvhd: Box, next_id: int):
    p = bytearray(mvhd.payload)
    p[-4:] = struct.pack(">I", next_id)
    mvhd.payload = bytes(p)


def _max_track_id(moov: Box) -> int:
    max_id = 0
    for trak in moov.children:
        if trak.type != b"trak":
            continue
        tkhd = trak.find("tkhd")
        if not tkhd:
            continue
        if tkhd.payload[0] == 1:
            tid = struct.unpack(">I", tkhd.payload[20:24])[0]
        else:
            tid = struct.unpack(">I", tkhd.payload[12:16])[0]
        max_id = max(max_id, tid)
    return max_id


def _shift_chunk_offsets(moov: Box, delta: int, exclude):
    """Add ``delta`` to every stco/co64 entry of existing tracks.

    ``exclude`` is a set/collection of trak Boxes to skip (the newly added
    tracks whose offsets are set explicitly afterwards).
    """
    if delta == 0:
        return
    for trak in moov.children:
        if trak.type != b"trak" or trak in exclude:
            continue
        stbl = trak.find("mdia/minf/stbl")
        if stbl is None:
            continue
        for child in stbl.children:
            if child.type == b"stco":
                p = child.payload
                count = struct.unpack(">I", p[4:8])[0]
                out = bytearray(p[:8])
                for i in range(count):
                    off = struct.unpack(">I", p[8 + i * 4:12 + i * 4])[0]
                    out += struct.pack(">I", off + delta)
                child.payload = bytes(out)
            elif child.type == b"co64":
                p = child.payload
                count = struct.unpack(">I", p[4:8])[0]
                out = bytearray(p[:8])
                for i in range(count):
                    off = struct.unpack(">Q", p[8 + i * 8:16 + i * 8])[0]
                    out += struct.pack(">Q", off + delta)
                child.payload = bytes(out)


def inject(mov_bytes: bytes, identifier: str) -> bytes:
    root = parse(mov_bytes)

    moov = next((b for b in root.children if b.type == b"moov"), None)
    mdat = next((b for b in root.children if b.type == b"mdat"), None)
    if moov is None or mdat is None:
        raise ValueError("source is not a valid MOV (missing moov/mdat)")

    moov_index = root.children.index(moov)
    mdat_index = root.children.index(mdat)

    # Match genuine Live Photos: ftyp major brand 'qt  ' with minor_version 0
    # and a single 'qt  ' compatible brand.
    ftyp = next((b for b in root.children if b.type == b"ftyp"), None)
    if ftyp is not None:
        ftyp.payload = b"qt  " + b"\x00\x00\x00\x00" + b"qt  "

    # Genuine Live Photos enable all tkhd flags (enabled|in movie|in preview|
    # in poster = 0x0F).  ffmpeg only sets 0x03, which can stop iOS from using
    # the clip as a wallpaper.  Force 0x0F on every existing track.
    for trak in moov.children:
        if trak.type != b"trak":
            continue
        tkhd = trak.find("tkhd")
        if tkhd is not None:
            p = bytearray(tkhd.payload)
            p[1:4] = b"\x00\x00\x0f"
            tkhd.payload = bytes(p)

    # Genuine clips carry a track aperture ('tapt') box on the video track,
    # right after tkhd.  Add one (clef/prof/enof = encoded dimensions) if the
    # video track lacks it.
    for trak in moov.children:
        if trak.type != b"trak":
            continue
        hdlr = trak.find("mdia/hdlr")
        if not hdlr or hdlr.payload[8:12] != b"vide":
            continue
        if trak.find("tapt") is not None:
            break
        tkhd = trak.find("tkhd")
        if tkhd is None:
            break
        # tkhd width/height are 16.16 fixed point at the end of the payload.
        width = struct.unpack(">I", tkhd.payload[-8:-4])[0]
        height = struct.unpack(">I", tkhd.payload[-4:])[0]
        dims = struct.pack(">II", width, height)
        def _dim_box(name):
            return struct.pack(">I", 20) + name + b"\x00\x00\x00\x00" + dims
        tapt_body = _dim_box(b"clef") + _dim_box(b"prof") + _dim_box(b"enof")
        tapt = Box(b"tapt", payload=tapt_body)
        # Insert tapt right after tkhd.
        idx = trak.children.index(tkhd)
        trak.children.insert(idx + 1, tapt)
        break

    # Size of moov before modification (to measure the shift it causes).
    moov_size_before = len(moov.serialize())

    mvhd = moov.find("mvhd")
    movie_timescale, movie_duration = _read_mvhd(mvhd)
    duration_seconds = movie_duration / movie_timescale if movie_timescale else 1.0
    
    # Genuine Live Photos use a fixed movie duration of ~1.05s to accommodate
    # the 60-frame motion data (1.0s at 60fps) plus a 0.05s empty edit.
    # If the video is shorter, we still use 1.05s to ensure the motion data
    # has full playback range.
    min_movie_duration = 1.05
    duration_seconds = max(duration_seconds, min_movie_duration)
    
    # Update mvhd duration to match the new movie duration
    new_movie_duration = int(duration_seconds * movie_timescale)
    mvhd.payload = bytearray(mvhd.payload)
    if mvhd.payload[0] == 0:  # version 0
        struct.pack_into(">I", mvhd.payload, 16, new_movie_duration)
    else:  # version 1
        struct.pack_into(">Q", mvhd.payload, 24, new_movie_duration)
    
    # Update video track's tkhd duration to match
    video_trak = next((t for t in moov.children if t.type == b"trak" and 
                       t.find("mdia/hdlr").payload[8:12] == b"vide"), None)
    if video_trak:
        tkhd = video_trak.find("tkhd")
        if tkhd:
            tkhd.payload = bytearray(tkhd.payload)
            if tkhd.payload[0] == 0:  # version 0
                struct.pack_into(">I", tkhd.payload, 20, new_movie_duration)
            else:  # version 1
                struct.pack_into(">Q", tkhd.payload, 28, new_movie_duration)
    
    # Update audio track's tkhd duration to match
    audio_trak = next((t for t in moov.children if t.type == b"trak" and 
                       t.find("mdia/hdlr").payload[8:12] == b"soun"), None)
    if audio_trak:
        tkhd = audio_trak.find("tkhd")
        if tkhd:
            tkhd.payload = bytearray(tkhd.payload)
            if tkhd.payload[0] == 0:  # version 0
                struct.pack_into(">I", tkhd.payload, 20, new_movie_duration)
            else:  # version 1
                struct.pack_into(">Q", tkhd.payload, 28, new_movie_duration)
    
    base_track_id = _max_track_id(moov)

    # Build the still-image-time track and the live-photo-info track (the
    # latter is required for the clip to be usable as a wallpaper).  Place the
    # still marker at 0.5s (matching genuine samples), or at the middle if
    # the clip is shorter than 1.0s.
    still_time = min(0.5, duration_seconds / 2.0)
    still_trak = boxes.build_still_image_time_track(
        track_id=base_track_id + 1,
        movie_timescale=movie_timescale,
        duration_seconds=duration_seconds,
        still_time_seconds=still_time,
    )
    info_trak = boxes.build_live_photo_info_track(
        track_id=base_track_id + 2,
        movie_timescale=movie_timescale,
        duration_seconds=duration_seconds,
    )
    still_audio_trak = boxes.build_still_image_time_audio_track(
        track_id=base_track_id + 3,
        movie_timescale=movie_timescale,
        still_time_seconds=still_time,
    )
    # Order matches genuine clips: live-photo-info, then the two
    # still-image-time tracks (transform-bearing, then audio-synced).
    new_traks = [info_trak, still_trak, still_audio_trak]

    # Insert the new tracks before any udta/meta, else at the end of moov.
    insert_at = len(moov.children)
    for i, c in enumerate(moov.children):
        if c.type in (b"udta", b"meta"):
            insert_at = i
            break
    for off, t in enumerate(new_traks):
        moov.children.insert(insert_at + off, t)
    _set_mvhd_next_track_id(mvhd, base_track_id + 4)

    # Drop the encoder 'udta' (e.g. ffmpeg's writer string); genuine Live
    # Photos don't carry it and it only adds noise.
    moov.children = [c for c in moov.children if c.type != b"udta"]

    # Replace/insert the movie-level content.identifier meta box (kept last).
    meta = boxes.build_content_identifier_meta(identifier)
    moov.children = [c for c in moov.children if c.type != b"meta"]
    moov.children.append(meta)

    # Build the new metadata samples and append them to the END of the existing
    # mdat.  The original media sits at the front of mdat, so its absolute
    # stco/co64 offsets stay valid; only data after the original payload moves.
    from . import templates
    still_sample = boxes.STILL_IMAGE_TIME_SAMPLE
    # Use the frame count the info track actually declared (its stsz sample
    # count) so the appended bytes match the track tables exactly.
    info_stsz = info_trak.find("mdia/minf/stbl/stsz").payload
    info_count = struct.unpack(">I", info_stsz[8:12])[0]
    info_samples = b"".join([templates.LIVE_PHOTO_INFO_SAMPLE] * info_count)
    still_audio_sample = boxes.STILL_IMAGE_TIME_ONLY_SAMPLE
    appended = still_sample + info_samples + still_audio_sample

    # Offset of the appended bytes = start of mdat + header + old payload len.
    # Compute the absolute position of mdat's payload in the final file.  We
    # place the layout as [ftyp, (wide), mdat, moov] (single mdat, moov last).
    ordered = [c for c in root.children if c.type not in (b"moov", b"mdat")]
    # Preserve original pre-mdat boxes order (ftyp, wide, ...).
    pre_mdat = [c for c in ordered]
    root.children = pre_mdat + [mdat, moov]

    mdat_payload_start = sum(len(c.serialize()) for c in pre_mdat) + 8
    appended_offset = mdat_payload_start + len(mdat.payload)

    mdat.payload = mdat.payload + appended

    # Patch the new tracks' stco to point at their samples (in append order).
    off1 = appended_offset
    off2 = off1 + len(still_sample)
    off3 = off2 + len(info_samples)
    still_trak.find("mdia/minf/stbl/stco").payload = \
        b"\x00\x00\x00\x00" + struct.pack(">II", 1, off1)
    info_trak.find("mdia/minf/stbl/stco").payload = \
        b"\x00\x00\x00\x00" + struct.pack(">II", 1, off2)
    still_audio_trak.find("mdia/minf/stbl/stco").payload = \
        b"\x00\x00\x00\x00" + struct.pack(">II", 1, off3)

    return serialize_tree(root)
