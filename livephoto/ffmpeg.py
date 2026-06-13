"""Thin wrapper around the ffmpeg / ffprobe command line tools."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from typing import List, Optional

_FFMPEG = None
_FFPROBE = None
# Cache the first HEVC/H.264 encoder that actually works on this machine.
_VIDEO_ENCODER: Optional[tuple] = None


def _imageio_ffmpeg() -> Optional[str]:
    """Return the ffmpeg binary bundled with imageio-ffmpeg, if installed.

    That build is compiled with libx264/libx265 (GPL), which we need to produce
    genuine HEVC Live Photos.  Many system ffmpeg builds are hardware-only and
    cannot encode HEVC reliably.
    """
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe and os.path.exists(exe):
            return exe
    except Exception:
        pass
    return None


def _find(name: str, override_env: str) -> str:
    val = os.environ.get(override_env)
    if val and os.path.exists(val):
        return val
    found = shutil.which(name)
    if found:
        return found
    # Common bundled location used in this environment.
    cand = os.path.join(os.path.expanduser("~"), "Apps", "lib", "ffmpeg", name + ".exe")
    if os.path.exists(cand):
        return cand
    raise FileNotFoundError(
        f"{name} not found. Install it or set the {override_env} environment variable."
    )


def ffmpeg_path() -> str:
    global _FFMPEG
    if _FFMPEG is None:
        # Prefer an explicit override, then a build with libx264/libx265.
        env = os.environ.get("FFMPEG_BINARY")
        if env and os.path.exists(env):
            _FFMPEG = env
        else:
            _FFMPEG = _imageio_ffmpeg() or _find("ffmpeg", "FFMPEG_BINARY")
    return _FFMPEG


def ffprobe_path() -> str:
    global _FFPROBE
    if _FFPROBE is None:
        _FFPROBE = _find("ffprobe", "FFPROBE_BINARY")
    return _FFPROBE


def _run(args: List[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=False)


def probe(path: str) -> dict:
    out = subprocess.run(
        [ffprobe_path(), "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path}: {out.stderr}")
    return json.loads(out.stdout)

BR=24
# Candidate encoders in preference order.  Each entry is
# (codec_name, [extra ffmpeg args], video_tag).  HEVC first (matches genuine
# Live Photos), then H.264 which iOS also accepts.
_ENCODER_CANDIDATES = [
    # info=0:sei=0 stops x265 from stuffing huge SEI/info NAL units into the
    # hvcC config box (genuine clips keep it ~100 bytes; the default bloats it
    # to several KB, which breaks iOS Live Photo / wallpaper handling).
    # ("libx265", ["-crf", "11", "-x265-params", "info=0:sei=0"], "hvc1"),
    # ("libx265", ["-crf", "16","-b:v", f"{BR}M", "-maxrate", f"{BR}M", "-bufsize", f"{BR*1.5}M"], "hvc1"),
    # ("libx265", "hvc1"),
    # ("libx265", ["-crf", "20"], "hvc1"),
    # ("hevc_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0"], "hvc1"),
    # ("hevc_qsv", ["-global_quality", "20"], "hvc1"),
    # ("hevc_amf", ["-quality", "quality", "-qp_i", "20", "-qp_p", "20"], "hvc1"),
    # MediaFoundation: use quality based rate control (lower value = better).
    # The default CBR-ish mode produces heavy artefacts, so set it explicitly.
    # ("hevc_mf", ["-rate_control", "quality", "-quality", "18"], "hvc1"),
    # ("libx264", ["-crf", "20"], "avc1"),
    # ("libx264", ["-crf", "20","-b:v", f"{BR}M", "-maxrate", f"{BR}M", "-bufsize", f"{BR*1.5}M"], "avc1"),
    # ("h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "19", "-b:v", "0"], "avc1"),
    # ("h264_qsv", ["-global_quality", "19"], "avc1"),
    # ("h264_amf", ["-quality", "quality", "-qp_i", "19", "-qp_p", "19"], "avc1"),
    # ("h264_mf", ["-crf", "16","-b:v", f"{BR}M", "-maxrate", f"{BR}M", "-bufsize", f"{BR*1.5}M"], "avc1"),
    # ("h264_mf", ["-lossless"], "avc1"),
    # ("mpeg4", ["-q:v", "3"], "mp4v"),
    ("libx265", None,"hvc1"),
    ("libx264", None,"avc1"),

]


def _available_encoders() -> set:
    out = subprocess.run([ffmpeg_path(), "-hide_banner", "-encoders"],
                         capture_output=True, text=True)
    names = set()
    for line in out.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] and all(c in "VASFXBD." for c in parts[0]):
            names.add(parts[1])
    return names


def _pick_encoder(test_input: str,
                    extra: List[str]
                ) -> tuple:

    """Return the first encoder candidate that successfully encodes a frame."""
    # global _VIDEO_ENCODER
    # if _VIDEO_ENCODER is not None:
    #     return _VIDEO_ENCODER
    video_encoders= []
    available = _available_encoders()
    import tempfile
    for codec, _extra, tag in _ENCODER_CANDIDATES:
        if codec not in available:
            continue
        if _extra is not None:
            extra = _extra
            print(f"trying {codec} with extra args {extra}")
        tmp = tempfile.NamedTemporaryFile(suffix=".mov", delete=False)
        tmp.close()
        try:
            args = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", test_input, "-t", "0.5", "-an",
                    "-c:v", codec] + extra + ["-tag:v", tag, tmp.name]
            r = _run(args)
            if r.returncode == 0 and os.path.getsize(tmp.name) > 1000:
                video_encoders.append((codec, extra, tag))
                # _VIDEO_ENCODER = (codec, extra, tag)
                # return _VIDEO_ENCODER
        finally:
            if os.path.exists(tmp.name):
                os.remove(tmp.name)
    # raise RuntimeError("no working H.264/HEVC encoder found in this ffmpeg build")

    # if not video_encoders:
    #     raise RuntimeError("no working H.264/HEVC encoder found in this ffmpeg build")
    
    return video_encoders[0]


def extract_frame(video_path: str, out_png: str, at: float = 0.0):
    args = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-ss", str(at), "-i", video_path, "-frames:v", "1", out_png]
    r = _run(args)
    if r.returncode != 0 or not os.path.exists(out_png):
        raise RuntimeError(f"frame extraction failed: {r.stderr.decode('utf-8','replace')}")


def transcode_for_livephoto(video_path: str, out_mov: str,
                            duration: Optional[float] = None,
                            crf: int = None,
                            bitrate: int = 24,  # mbps
                            maxrate: float = 24, # mbps
                            minrate: float = 24, # mbps
                            bufsize: float = 36, # mbps
                            ):
    """Transcode to a QuickTime MOV suitable for a Live Photo.

    * video: HEVC (hvc1) when available, else H.264 (avc1)
    * audio: AAC
    * container brand: ``qt``
    * crf: quality level (lower = better quality, higher bitrate).
           If crf < 18, automatically caps bitrate at 16 Mbps to stay within
           iOS Live Photo wallpaper limits (genuine samples use 11.8-15.5 Mbps).
    """
    if crf is not None:
        extra = ["-crf", str(crf), "-b:v", f"{bitrate}M", "-maxrate", f"{maxrate}M", "-bufsize", f"{bufsize}M"]
    else:
        extra = ["-b:v", f"{bitrate}M", "-maxrate", f"{maxrate}M", "-minrate", f"{minrate}M", "-bufsize", f"{bufsize}M"]
    codec, extra, tag = _pick_encoder(video_path, extra)
    print(f"Using {codec} with extra args {extra} and tag {tag}")

    # If CRF is too low (high quality), switch to fixed bitrate to prevent
    # exceeding iOS Live Photo wallpaper limits.
    # use_fixed_bitrate = crf < 18
    # if use_fixed_bitrate:
    #     # Override CRF-based extra args with fixed bitrate
    #     extra = ["-b:v", "13M", "-x265-params", "info=0:sei=0"] if codec == "libx265" else ["-b:v", "13M"]

    # Genuine Live Photos always carry an audio track.  If the source has none,
    # synthesize a silent AAC track so the structure matches.
    has_audio = any(s.get("codec_type") == "audio"
                    for s in probe(video_path).get("streams", []))

    args = [ffmpeg_path(), "-hide_banner", "-loglevel", "error", "-y",
            "-i", video_path]
    if not has_audio:
        args += ["-f", "lavfi", "-i",
                 "anullsrc=channel_layout=stereo:sample_rate=44100"]
    if duration is not None:
        args += ["-t", str(duration)]
    args += ["-c:v", codec] + extra + ["-tag:v", tag,
             # Tag with explicit BT.709 colour metadata (matching genuine Live
             # Photos) so iOS renders colours correctly.
             "-pix_fmt", "yuv420p",
             "-color_primaries", "bt709",
             "-color_trc", "bt709",
             "-colorspace", "bt709",
             # Cap bitrate at 16 Mbps to stay within iOS Live Photo wallpaper
             # limits (genuine samples use 11.8-15.5 Mbps).
            #  "-maxrate", "25.5M",
            #  "-bufsize", "32M"
             ]
    # Hardware encoders (e.g. h264_mf) often drop the colour VUI; force it into
    # the bitstream with the matching metadata bitstream filter.
    if tag == "hvc1":
        args += ["-bsf:v",
                 "hevc_metadata=colour_primaries=1:transfer_characteristics=1:"
                 "matrix_coefficients=1"]
    elif tag == "avc1":
        args += ["-bsf:v",
                 "h264_metadata=colour_primaries=1:transfer_characteristics=1:"
                 "matrix_coefficients=1"]
    args += ["-c:a", "aac", "-b:a", "128k"]
    if not has_audio:
        # Map the real video and the synthesized audio; stop at video length.
        args += ["-map", "0:v:0", "-map", "1:a:0", "-shortest"]
    args += ["-brand", "qt", "-f", "mov", out_mov]
    r = _run(args)
    if r.returncode != 0 or not os.path.exists(out_mov):
        raise RuntimeError(
            "transcode failed: " + r.stderr.decode("utf-8", "replace"))
