# OctoOps one-shot setup (Windows).
# Installs uv + Go if needed, syncs Python deps, builds the whatsmeow bridge
# from source, and runs diagnostics. Re-runnable and idempotent.
#
# Run from PowerShell:  ./setup.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# --- uv (Python package manager) ---------------------------------------------
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "==> installing uv"
    powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
}
# Ensure uv is on PATH even if just installed (installer edits user PATH for
# new sessions only; this fixes it for the current session).
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"

Write-Host "==> syncing environment (uv sync)"
uv sync

# --- Go (needed to build the WhatsApp bridge) --------------------------------
if (-not (Get-Command go -ErrorAction SilentlyContinue)) {
    Write-Host "==> installing Go"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install GoLang.Go --silent --accept-package-agreements --accept-source-agreements
    } else {
        Write-Warning "winget not found. Install Go from https://go.dev/dl/ then re-run this script."
    }
}
# Same session-PATH fix as uv: Go's installer writes to system PATH but that
# only takes effect in new shells. Add the default install location here.
$env:Path = "C:\Program Files\Go\bin;$env:Path"

# --- build whatsmeow bridge --------------------------------------------------
if (Get-Command go -ErrorAction SilentlyContinue) {
    Write-Host "==> building whatsmeow bridge"
    try {
        Push-Location "$PSScriptRoot\whatsmeow-bridge"
        go get go.mau.fi/whatsmeow@latest modernc.org/sqlite@latest github.com/mdp/qrterminal/v3@latest
        if ($LASTEXITCODE -ne 0) { throw "go get failed" }
        go mod tidy
        if ($LASTEXITCODE -ne 0) { throw "go mod tidy failed" }
        go build -o "$PSScriptRoot\whatsmeow-bridge.exe" .
        if ($LASTEXITCODE -ne 0) { throw "go build failed" }
        Write-Host "    bridge built: whatsmeow-bridge.exe"
    } catch {
        Write-Warning "Bridge build failed: $_"
        Write-Warning "To retry: cd whatsmeow-bridge; go mod tidy; go build -o ..\whatsmeow-bridge.exe ."
    } finally {
        Pop-Location -ErrorAction SilentlyContinue
    }
} else {
    Write-Warning "Go not found — bridge not built. Install Go from https://go.dev/dl/ and re-run."
}

# --- diagnostics -------------------------------------------------------------
Write-Host "==> running diagnostics"
try { uv run python -m octoops --check } catch { Write-Warning $_ }

Write-Host ""
Write-Host "Setup complete."
Write-Host "  Configure:  uv run python -m octoops --setup"
Write-Host "  Start:      uv run python -m octoops"
Write-Host ""
Write-Host "Notes:"
Write-Host "  - If 'uv' or 'go' are not found in a new terminal, open a fresh PowerShell."
Write-Host "  - On first WhatsApp start, scan the QR code shown in the terminal to pair."
