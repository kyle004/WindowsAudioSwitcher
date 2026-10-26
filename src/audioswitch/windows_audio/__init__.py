"""Platform audio backends.

`create_backend()` picks the Windows COM backend on Windows and the fake
backend elsewhere (or when AUDIOSWITCH_BACKEND=fake is set).
"""
from __future__ import annotations

import logging
import os
import sys

log = logging.getLogger(__name__)


def create_backend(name: str | None = None):
    name = (name or os.environ.get("AUDIOSWITCH_BACKEND") or "").lower()
    if name == "fake" or (not name and sys.platform != "win32"):
        from .fake import FakeAudioBackend

        log.info("Using FAKE audio backend")
        return FakeAudioBackend()
    if name in ("", "windows", "com"):
        from .backend import WindowsAudioBackend

        return WindowsAudioBackend()
    raise ValueError(f"Unknown backend '{name}'")
