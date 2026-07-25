#!/bin/sh
# ByteBarn installer: isolated venv in ~/.bytebarn/venv + `bytebarn` on PATH.
#
#   curl -fsSL https://raw.githubusercontent.com/hhamaker/bytebarn/main/scripts/install.sh | sh
#
# Re-running upgrades in place. Uninstall: rm -rf ~/.bytebarn/venv ~/.local/bin/bytebarn
set -eu

PACKAGE="${BYTEBARN_INSTALL_SOURCE:-bytebarn}"   # overridable for testing
VENV="$HOME/.bytebarn/venv"
BIN_DIR="$HOME/.local/bin"

say() { printf '%s\n' "$*"; }
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }

# -- find a Python 3.12+ ----------------------------------------------------
PY=""
for candidate in python3.13 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)'; then
            PY="$(command -v "$candidate")"
            break
        fi
    fi
done

if [ -z "$PY" ]; then
    case "$(uname -s)" in
        Darwin) hint="install it with:  brew install python@3.12" ;;
        *)      hint="install python3.12 with your package manager (e.g. apt install python3.12 python3.12-venv)" ;;
    esac
    fail "ByteBarn needs Python 3.12 or newer — $hint"
fi
say "using $("$PY" -V) at $PY"

# -- isolated venv ----------------------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then
    say "creating $VENV"
    "$PY" -m venv "$VENV" || fail "could not create a venv (Debian/Ubuntu: apt install python3.12-venv)"
fi
say "installing $PACKAGE ..."
"$VENV/bin/pip" install --quiet --upgrade pip "$PACKAGE" \
    || fail "pip install failed — see output above"

# -- launcher on PATH -------------------------------------------------------
mkdir -p "$BIN_DIR"
ln -sf "$VENV/bin/bytebarn" "$BIN_DIR/bytebarn"
say "linked $BIN_DIR/bytebarn"

case ":$PATH:" in
    *":$BIN_DIR:"*) say "" ; say "done — run:  bytebarn" ;;
    *)
        say ""
        say "done. $BIN_DIR is not on your PATH — add this to your shell profile:"
        say "  export PATH=\"\$HOME/.local/bin:\$PATH\""
        say "then run:  bytebarn"
        ;;
esac
