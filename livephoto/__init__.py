"""Live Photo toolkit: convert a video (+ optional still) into an
iOS-readable Live Photo (a paired still image + .MOV)."""

from .core import make_live_photo, new_content_identifier  # noqa: F401

__all__ = ["make_live_photo", "new_content_identifier"]
