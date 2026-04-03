#!/usr/bin/env bash
# RedClaw Linux Installer (Ubuntu/Debian)
# Usage: curl -sL <url> | bash   OR   ./install.sh

set -euo pipefail

VERSION="0.2.0"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/bin}"
DATA_DIR="${DATA_DIR:-$HOME/.redclaw}"

echo ""
echo " RedClaw v${VERSION} - Linux Installer"
echo " ====================================="
echo ""

# --- deps ---
if ! command -v python3 &>/dev/null; then
    echo " Installing Python 3.13..."
    sudo apt-get update -qq
    sudo apt-get install -y -qq software-properties-common
    sudo add-apt-repository -y ppa:deadsnakes/ppa
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3.13 python3.13-venv python3.13-dev
fi

PY="$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [[ "$(printf '%s\n' "3.11" "$PY" | sort -V | head -n1)" != "3.11" ]]; then
    echo " ERROR: Python >= 3.11 required (found $PY)"
    exit 1
fi

# --- venv ---
VENV="$HOME/.redclaw/venv"
echo " Creating virtual environment at $VENV ..."
python3 -m venv "$VENV" --system-site-packages 2>/dev/null || python3 -m venv "$VENV"
source "$VENV/bin/activate"

# --- install ---
echo " Installing RedClaw ..."
if pip install -q --upgrade pip; then true; fi
pip install -q "redclaw==${VERSION}" 2>/dev/null || {
    # Fallback: install from local source
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    if [[ -f "$SCRIPT_DIR/pyproject.toml" ]]; then
        pip install -q "$SCRIPT_DIR"
    else
        echo " ERROR: Could not install redclaw. Install manually with:"
        echo "   pip install redclaw"
        exit 1
    fi
}

# --- PATH ---
mkdir -p "$INSTALL_DIR"
ln -sf "$VENV/bin/redclaw" "$INSTALL_DIR/redclaw"

shell_rc="$HOME/.bashrc"
if [[ -n "${ZSH_VERSION:-}" ]]; then shell_rc="$HOME/.zshrc"; fi
if ! grep -q "$INSTALL_DIR" "$shell_rc" 2>/dev/null; then
    echo "" >> "$shell_rc"
    echo "# RedClaw" >> "$shell_rc"
    echo "export PATH=\"$INSTALL_DIR:\$PATH\"" >> "$shell_rc"
    echo " Added $INSTALL_DIR to PATH in $shell_rc"
fi

# --- data dir ---
mkdir -p "$DATA_DIR"/{memory,assistant,crypt/bloodlines,crypt/entombed,skills}

# --- done ---
echo ""
echo " Done! RedClaw installed."
echo ""
echo " Quick start:"
echo "   redclaw                          # CLI REPL"
echo "   redclaw --mode dashboard         # Web dashboard"
echo "   redclaw --mode webchat           # Browser chat"
echo "   redclaw --mode telegram          # Telegram bot"
echo ""
echo " Source your shell rc or open a new terminal, then run 'redclaw'."
echo ""
