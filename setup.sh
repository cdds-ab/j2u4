#!/bin/bash
# Setup script for j2u4
# Run once after cloning the repository

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "========================================"
echo "j2u4 Setup"
echo "========================================"
echo

# Check prerequisites
echo "[0] Checking prerequisites..."

if ! command -v uv &> /dev/null; then
    echo "    [x] uv NOT FOUND"
    echo
    echo "[!] ERROR: uv is required but not installed."
    echo
    echo "    Install uv:"
    echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
    echo
    echo "    More info: https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

echo "    [ok] uv $(uv --version 2>/dev/null | awk '{print $2}')"
echo

# Install dependencies (uv handles Python + venv automatically)
echo "[1] Installing dependencies..."
uv sync

# Install Playwright browser
echo "[2] Installing Chromium browser for Playwright..."
uv run playwright install chromium

# Create config from template if needed
if [ ! -f "config.json" ]; then
    if [ -f "config.example.json" ]; then
        echo "[3] Creating config.json from template..."
        cp config.example.json config.json
        CONFIG_CREATED=true
    else
        echo "[3] No config.example.json found, skipping config creation"
        CONFIG_CREATED=false
    fi
else
    echo "[3] config.json already exists"
    CONFIG_CREATED=false
fi

# Create empty mapping file if needed
if [ ! -f "account_to_arbauft_mapping.json" ]; then
    echo "[4] Creating empty mapping file..."
    echo "{}" > account_to_arbauft_mapping.json
    MAPPING_CREATED=true
else
    # Count mappings
    MAPPING_COUNT=$(grep -c "unit4_arbauft" account_to_arbauft_mapping.json 2>/dev/null || echo "0")
    echo "[4] Mapping file exists ($MAPPING_COUNT mappings)"
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
    echo "        ./build-mapping"
    echo "     b) Or enter mappings manually when prompted during sync"
    echo
fi
echo "  4. Sync your time entries:"
echo "     ./sync 202606              # dry-run first"
echo "     ./sync 202606 --execute    # actually sync"
echo
