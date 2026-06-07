# OctoOps one-shot setup (Windows).
# Installs uv if needed, provisions Python + locked deps, fetches the bridge
# binary (best-effort), and runs diagnostics. Re-runnable and idempotent.
#
# Run from PowerShell:  ./setup.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> installing uv"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
}

# Ensure uv is reachable for the rest of this script even if it was just installed
# (its installer edits the user PATH, which only applies to NEW sessions) or if it
# lives in %USERPROFILE%\.local\bin but isn't on PATH in this session yet.
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

Write-Host "==> syncing environment (uv sync)"
uv sync

Write-Host "==> fetching whatsmeow bridge (best-effort)"
try { uv run python scripts/fetch_bridge.py } catch { Write-Warning $_ }

Write-Host "==> running diagnostics"
try { uv run python -m octoops --check } catch { Write-Warning $_ }

Write-Host ""
Write-Host "Setup complete."
Write-Host "  Configure:  uv run python -m octoops --setup"
Write-Host "  Start:      uv run python -m octoops"
Write-Host ""
Write-Host "Note: if 'uv' isn't found in a new terminal, open a fresh PowerShell"
Write-Host "      (the installer updates PATH for new sessions only)."
