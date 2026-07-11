"""Render the app icon to Crew.icns (macOS) using the in-app sprite art.

Usage: .venv/bin/python scripts/make_icns.py [output.icns]
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PySide6.QtWidgets import QApplication  # noqa: E402


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("build/Crew.icns")
    out.parent.mkdir(parents=True, exist_ok=True)

    _app = QApplication([])
    from crew.app.icon import _render

    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Crew.iconset"
        iconset.mkdir()
        for size in (16, 32, 64, 128, 256, 512):
            _render(size).save(str(iconset / f"icon_{size}x{size}.png"))
            _render(size * 2).save(str(iconset / f"icon_{size}x{size}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], check=True)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
