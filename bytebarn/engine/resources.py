"""Locate bundled data files, both from a checkout and a frozen app bundle."""

from __future__ import annotations

import sys
from pathlib import Path


def prompts_dir() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller bundle
        return Path(getattr(sys, "_MEIPASS", ".")) / "assets" / "prompts"
    return Path(__file__).resolve().parent.parent.parent / "assets" / "prompts"
