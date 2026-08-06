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

# PyInstaller stamps 0.0.0, so a built app cannot say which release it is.
# Take the version from the package metadata and write it into the bundle.
VERSION=$($PY -c "import tomllib,pathlib; print(tomllib.loads(pathlib.Path('pyproject.toml').read_text())['project']['version'])")
PLIST=dist/ByteBarn.app/Contents/Info.plist
/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$PLIST"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$PLIST" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$PLIST"

# Without these, macOS denies access to protected folders instead of asking —
# and tools we spawn (Claude Code, bash) inherit the denial as a bare EPERM.
add_usage() {
  /usr/libexec/PlistBuddy -c "Set :$1 $2" "$PLIST" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :$1 string $2" "$PLIST"
}
REASON="ByteBarn needs access to the folders your projects live in so agents can read and edit them."
add_usage NSDocumentsFolderUsageDescription "$REASON"
add_usage NSDesktopFolderUsageDescription "$REASON"
add_usage NSDownloadsFolderUsageDescription "$REASON"
add_usage NSRemovableVolumesUsageDescription "$REASON"

echo
echo "built dist/ByteBarn.app ($VERSION)"
if [[ "${1:-}" == "--install" ]]; then
  rm -rf /Applications/ByteBarn.app
  cp -R dist/ByteBarn.app /Applications/ByteBarn.app
  echo "installed to /Applications/ByteBarn.app"
fi
