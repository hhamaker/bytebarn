#!/bin/bash
# Build ByteBarn.app (macOS) with PyInstaller and optionally install it.
#
#   ./scripts/build_macos_app.sh            # build dist/ByteBarn.app
#   ./scripts/build_macos_app.sh --install  # ...and copy to /Applications
set -euo pipefail
cd "$(dirname "$0")/.."

PY=.venv/bin/python
$PY -c "import PyInstaller" 2>/dev/null || .venv/bin/pip install pyinstaller

$PY scripts/make_icns.py build/ByteBarn.icns

# hidden imports: PyInstaller misses the idna codec + package, which kills
# every HTTPS request in the bundle ("unknown encoding: idna" / generic
# "Connection error." from the provider SDKs)
$PY -m PyInstaller \
  --noconfirm --clean --windowed \
  --name ByteBarn \
  --icon build/ByteBarn.icns \
  --add-data "bytebarn/assets:assets" \
  --hidden-import encodings.idna \
  --collect-submodules idna \
  --osx-bundle-identifier dev.bytebarn.app \
  scripts/app_entry.py

echo
echo "built dist/ByteBarn.app"
if [[ "${1:-}" == "--install" ]]; then
  rm -rf /Applications/ByteBarn.app
  cp -R dist/ByteBarn.app /Applications/ByteBarn.app
  echo "installed to /Applications/ByteBarn.app"
fi
