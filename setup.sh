#!/usr/bin/env bash
# OctoOps one-shot setup (Linux/macOS).
# Installs uv if needed, provisions Python + locked deps, fetches the bridge
# binary (best-effort), and runs diagnostics. Re-runnable and idempotent.
set -euo pipefail

cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
    echo "==> installing uv"
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

# Ensure uv is reachable for the rest of this script even if it was just installed
# (its installer edits shell profiles, which only take effect in a NEW shell) or
# if it lives in ~/.local/bin but isn't on PATH in this session yet.
export PATH="$HOME/.local/bin:$PATH"

echo "==> syncing environment (uv sync)"
uv sync

echo "==> fetching whatsmeow bridge (best-effort)"
uv run python scripts/fetch_bridge.py || true

echo "==> running diagnostics"
uv run python -m octoops --check || true

echo
echo "Setup complete."
echo "  Configure:  uv run python -m octoops --setup"
echo "  Start:      uv run python -m octoops"
echo
if ! command -v uv >/dev/null 2>&1; then
    echo "Note: 'uv' isn't on your PATH in new terminals yet. Add this line to"
    echo "      ~/.bashrc or ~/.profile, then restart your shell:"
    echo '      export PATH="$HOME/.local/bin:$PATH"'
fi
