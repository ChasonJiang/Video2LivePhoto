#!/usr/bin/env python
"""Video2LivePhoto - convert a video (and optional still image) into an
iOS-readable Live Photo (a paired still image + .MOV).

Examples
--------
  # Use an explicit still image, output HEIC + MOV
  python video2livephoto.py -v clip.mp4 -i photo.jpg -o out

  # No still image -> a frame is grabbed from the video; output JPEG
  python video2livephoto.py -v clip.mp4 -o out --image-format jpeg

  # Just verify an existing pair
  python video2livephoto.py --verify still.HEIC video.MOV
"""

from __future__ import annotations

import argparse
import json
import sys

from livephoto import make_live_photo
from livephoto.verify import verify_pair


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="video2livephoto",
        description="Convert a video (+ optional image) into an iOS Live Photo.",
    )
    p.add_argument("-v", "--video", help="source video file")
    p.add_argument("-i", "--image", default=None,
                   help="still image (optional; a frame is grabbed if omitted)")
    p.add_argument("-o", "--output-dir", default="output",
                   help="directory for the generated pair (default: ./output)")
    p.add_argument("--image-format","-fmt", choices=["heic", "jpeg"], default="heic",
                   help="still image output format (default: heic)")
    p.add_argument("-d", "--duration", type=float, default=None,
                   help="trim the video to this many seconds")
    p.add_argument("--still-time", "-st", type=float, default=None,
                   help="timestamp (s) of the frame to grab when no image given")
    p.add_argument("--crf", "-crf", type=int, default=None,
                   help="video quality (lower=better, 0-51, default: None). ")
    p.add_argument("--bitrate", "-bitrate", type=float, default=20.0,
                   help="average video bitrate (Mbps, default: 20)")
    p.add_argument("--maxrate", "-maxrate", type=float, default=20.0,
                   help="maximum video bitrate (Mbps, default: 20)")
    p.add_argument("--minrate", "-minrate", type=float, default=20.0,
                   help="mimimum video bitrate (Mbps, default: 20)")
    p.add_argument("--bufsize", "-bufsize", type=float, default=20.0,
                   help="buffer size (Mbps, default: 20.0)")
    p.add_argument("--name", default=None,
                   help="output basename (default: derived from the video)")
    p.add_argument("--verify", nargs=2, metavar=("STILL", "VIDEO"),
                   help="verify an existing still+MOV pair and exit")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.verify:
        report = verify_pair(args.verify[0], args.verify[1])
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["valid"] else 1

    if not args.video:
        build_parser().error("--video is required (or use --verify)")

    result = make_live_photo(
        video_path=args.video,
        output_dir=args.output_dir,
        image_path=args.image,
        image_format=args.image_format,
        duration=args.duration,
        basename=args.name,
        still_time=args.still_time,
        crf=args.crf,
        bitrate=args.bitrate,
        maxrate=args.maxrate,
        minrate=args.minrate,
        bufsize=args.bufsize,
    )

    print("Live Photo created:")
    print(f"  still : {result['still']}")
    print(f"  video : {result['video']}")
    print(f"  id    : {result['identifier']}")

    report = verify_pair(result["still"], result["video"])
    print("\nVerification:")
    print(json.dumps(report["checks"], indent=2))
    if not report["valid"]:
        print("WARNING: pair did not pass all Live Photo checks", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
