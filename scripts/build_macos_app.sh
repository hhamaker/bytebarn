#!/bin/bash
# Build Crew.app (macOS) with PyInstaller and optionally install it.
#
#   ./scripts/build_macos_app.sh            # build dist/Crew.app
#   ./scripts/build_macos_app.sh --install  # ...and copy to /Applications
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
$PY -c "import PyInstaller" 2>/dev/null || .venv/bin/pip install pyinstaller

$PY scripts/make_icns.py build/Crew.icns

# hidden imports: PyInstaller misses the idna codec + package, which kills
# every HTTPS request in the bundle ("unknown encoding: idna" / generic
# "Connection error." from the provider SDKs)
$PY -m PyInstaller \
  --noconfirm --clean --windowed \
  --name Crew \
  --icon build/Crew.icns \
  --add-data "assets:assets" \
  --hidden-import encodings.idna \
  --collect-submodules idna \
  --osx-bundle-identifier dev.crew.app \
  scripts/app_entry.py

echo
echo "built dist/Crew.app"
if [[ "${1:-}" == "--install" ]]; then
  rm -rf /Applications/Crew.app
  cp -R dist/Crew.app /Applications/Crew.app
  echo "installed to /Applications/Crew.app"
fi
