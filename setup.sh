#!/bin/bash
# Setup script for jira2unit4
# Run once after cloning the repository

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "jira2unit4 Setup"
echo "========================================"
echo

# Check prerequisites
echo "[0] Checking prerequisites..."

MISSING=""

# Check Python version
PYTHON_CMD=""
for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
        # Try to get version, skip if command fails (e.g., pyenv shim without version)
        version=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null) || continue
        major=$(echo "$version" | cut -d. -f1)
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$major" -ge 3 ] && [ "$minor" -ge 11 ]; then
            PYTHON_CMD="$cmd"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "    [x] Python 3.11+ NOT FOUND"
    MISSING="$MISSING python3.11+"
else
    echo "    [ok] Python $version"
fi

# Check pip
if ! $PYTHON_CMD -m pip --version &> /dev/null 2>&1; then
    echo "    [x] pip NOT FOUND"
    MISSING="$MISSING pip"
else
    echo "    [ok] pip"
fi

# Check node (needed for Playwright)
if ! command -v node &> /dev/null; then
    echo "    [x] Node.js NOT FOUND (needed for Playwright)"
    MISSING="$MISSING nodejs"
else
    echo "    [ok] Node.js $(node --version 2>/dev/null || echo '?')"
fi

# Exit if prerequisites missing
if [ -n "$MISSING" ]; then
    echo
    echo "[!] ERROR: Missing prerequisites:$MISSING"
    echo
    echo "    Install the missing packages and try again."
    echo "    On Ubuntu/Debian: sudo apt install python3.11 python3-pip nodejs"
    echo "    On macOS: brew install python@3.11 node"
    exit 1
fi

echo
echo "[1] Using $PYTHON_CMD (version $version)"

# Create virtual environment
if [ ! -d ".venv" ]; then
    echo "[2] Creating virtual environment..."
    $PYTHON_CMD -m venv .venv
else
    echo "[2] Virtual environment already exists"
fi

# Activate venv
echo "[3] Activating virtual environment..."
source .venv/bin/activate

# Install dependencies
echo "[4] Installing Python dependencies..."
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# Install Playwright browser
echo "[5] Installing Chromium browser for Playwright..."
playwright install chromium

# Create config from template if needed
if [ ! -f "config.json" ]; then
    if [ -f "config.example.json" ]; then
        echo "[6] Creating config.json from template..."
        cp config.example.json config.json
        CONFIG_CREATED=true
    else
        echo "[6] No config.example.json found, skipping config creation"
        CONFIG_CREATED=false
    fi
else
    echo "[6] config.json already exists"
    CONFIG_CREATED=false
fi

# Create empty mapping file if needed
if [ ! -f "account_to_arbauft_mapping.json" ]; then
    echo "[7] Creating empty mapping file..."
    echo "{}" > account_to_arbauft_mapping.json
    MAPPING_CREATED=true
else
    # Count mappings
    MAPPING_COUNT=$(grep -c "unit4_arbauft" account_to_arbauft_mapping.json 2>/dev/null || echo "0")
    echo "[7] Mapping file exists ($MAPPING_COUNT mappings)"
    MAPPING_CREATED=false
fi

echo
echo "========================================"
echo "Setup complete!"
echo "========================================"
echo
echo "Next steps:"
if [ "$CONFIG_CREATED" = true ]; then
    echo "  1. Edit config.json with your API tokens:"
    echo "     - Jira: https://id.atlassian.com/manage-profile/security/api-tokens"
    echo "     - Tempo: Settings > API Integration in Tempo"
    echo
fi
echo "  2. Test connectivity:"
echo "     ./sync --check"
echo
if [ "$MAPPING_CREATED" = true ]; then
    echo "  3. Build the Account -> ArbAuft mapping (choose one):"
    echo "     a) Auto-build from Unit4 history (recommended for first setup):"
    echo "        source .venv/bin/activate"
    echo "        python build_mapping_from_history.py"
    echo "     b) Or enter mappings manually when prompted during sync"
    echo
fi
echo "  4. Sync your time entries:"
echo "     ./sync 202606              # dry-run first"
echo "     ./sync 202606 --execute    # actually sync"
echo
