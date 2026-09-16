#!/usr/bin/env bash
# =======================================================
# JobAgent - 1-Click macOS & Linux Automated Setup
# =======================================================

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}=======================================================${NC}"
echo -e "${CYAN}         JobAgent - macOS & Linux Automated Setup      ${NC}"
echo -e "${CYAN}=======================================================${NC}"
echo ""

# 1. Detect Python 3
if command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PY_CMD="python"
else
    echo -e "${RED}[ERROR] Python 3 is not installed or not found in PATH.${NC}"
    echo -e "On macOS:  brew install python"
    echo -e "On Ubuntu/Debian: sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    echo -e "On Fedora: sudo dnf install -y python3 python3-pip"
    echo -e "On Arch:   sudo pacman -S python python-pip"
    exit 1
fi

echo -e "${GREEN}[1/4] Found Python:${NC} $($PY_CMD --version)"

# 2. Check Python version >= 3.10
$PY_CMD -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" || {
    echo -e "${RED}[ERROR] Python 3.10 or higher is required.${NC}"
    echo -e "Current version: $($PY_CMD --version)"
    exit 1
}

# 3. Setup Virtual Environment (avoids PEP 668 externally-managed-environment errors)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/pyproject.toml" ]; then
    VENV_DIR="$SCRIPT_DIR/.venv"
else
    VENV_DIR="$HOME/.jobagent/venv"
fi

echo -e "${CYAN}[2/4] Setting up isolated virtual environment in ${VENV_DIR}...${NC}"
if [ ! -f "$VENV_DIR/bin/python" ]; then
    mkdir -p "$(dirname "$VENV_DIR")"
    $PY_CMD -m venv "$VENV_DIR" || {
        echo -e "${RED}[ERROR] Failed to create venv.${NC}"
        echo -e "${YELLOW}On Debian/Ubuntu, run: sudo apt install -y python3-venv${NC}"
        exit 1
    }
fi
VENV_PY="$VENV_DIR/bin/python"
echo -e "${GREEN}Virtual environment ready.${NC}"

# 4. Install / Upgrade JobAgent
echo -e "${CYAN}[3/4] Installing / Upgrading JobAgent and dependencies...${NC}"
"$VENV_PY" -m pip install --upgrade pip --quiet
if [ -f "$SCRIPT_DIR/requirements.txt" ]; then
    "$VENV_PY" -m pip install -r "$SCRIPT_DIR/requirements.txt" --quiet
    "$VENV_PY" -m pip install -e "$SCRIPT_DIR" --quiet
else
    "$VENV_PY" -m pip install --upgrade jobagent --quiet
fi
echo -e "${GREEN}JobAgent installed successfully.${NC}"

# 5. Install Playwright Chromium
echo -e "${CYAN}[4/4] Installing Playwright Chromium browser binaries...${NC}"
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    # On Linux, try with-deps or standard install
    "$VENV_PY" -m playwright install chromium || true
else
    "$VENV_PY" -m playwright install chromium
fi

# 6. Create symlink in ~/.local/bin if possible
BIN_DIR="$HOME/.local/bin"
mkdir -p "$BIN_DIR"
cat << 'EOF' > "$BIN_DIR/jobagent"
#!/usr/bin/env bash
EOF
echo "\"$VENV_PY\" -m jobagent \"\$@\"" >> "$BIN_DIR/jobagent"
chmod +x "$BIN_DIR/jobagent"

echo ""
echo -e "${GREEN}=======================================================${NC}"
echo -e "${GREEN} SUCCESS! JobAgent is installed and ready to use!     ${NC}"
echo -e "${GREEN}=======================================================${NC}"
echo -e "You can run JobAgent from your terminal with:"
echo -e "${YELLOW}  jobagent${NC}"
echo -e "Or via the runner script:"
echo -e "${YELLOW}  ./run.sh${NC}"
echo ""

if [[ ":$PATH:" != *":$HOME/.local/bin:"* ]]; then
    echo -e "${YELLOW}Tip: Add ~/.local/bin to your PATH to run 'jobagent' from anywhere:${NC}"
    echo -e "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.zshrc   # for zsh"
    echo -e "  echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc  # for bash"
    echo ""
fi

read -p "Would you like to launch JobAgent now? (Y/n): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]] || [[ -z $REPLY ]]; then
    "$VENV_PY" -m jobagent
fi
