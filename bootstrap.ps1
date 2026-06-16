# OctoOps bootstrap (Windows) — clone + setup in one go.
#
# Ensures Git is installed (via winget), clones the repo (or pulls if it already
# exists), then runs setup.ps1 (uv + Go + deps + bridge build + diagnostics) and,
# unless -NoConfigure is passed, launches the setup wizard.
#
# Run it either way:
#   irm https://raw.githubusercontent.com/clerio95/OctoOps/main/bootstrap.ps1 | iex
#   ./bootstrap.ps1            # if you already have this file
#
# Optional switches (only when invoked as a file, not via irm | iex):
#   ./bootstrap.ps1 -Dir C:\apps\OctoOps -NoConfigure
param(
    [string]$Repo = "https://github.com/clerio95/OctoOps.git",
    [string]$Dir = "OctoOps",
    [switch]$NoConfigure
)
$ErrorActionPreference = "Stop"

# --- Git (needed to clone) ---------------------------------------------------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "==> installing Git"
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        winget install -e --id Git.Git --silent --accept-source-agreements --accept-package-agreements
    } else {
        throw "winget not found and Git is missing. Install Git from https://git-scm.com/download/win then re-run."
    }
}
# Git's installer edits PATH for new sessions only; pull it into THIS session so
# the clone below works immediately (same trick setup.ps1 uses for uv/Go).
$env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
            [Environment]::GetEnvironmentVariable('Path', 'User') + ';C:\Program Files\Git\cmd'

# --- clone or update ---------------------------------------------------------
if (Test-Path (Join-Path $Dir ".git")) {
    Write-Host "==> updating existing checkout ($Dir)"
    Push-Location $Dir
    try { git pull --ff-only } finally { Pop-Location -ErrorAction SilentlyContinue }
} elseif (Test-Path $Dir) {
    throw "'$Dir' exists but is not a git checkout. Remove it or pass -Dir <other-path>."
} else {
    Write-Host "==> cloning $Repo"
    git clone $Repo $Dir
}

Set-Location $Dir

# --- setup (uv + Go + deps + bridge + diagnostics) ---------------------------
Write-Host "==> running setup.ps1"
& "$PWD\setup.ps1"

# --- configure ---------------------------------------------------------------
if ($NoConfigure) {
    Write-Host ""
    Write-Host "Bootstrap complete. Configure with:  uv run python -m octoops --setup"
} else {
    Write-Host "==> launching setup wizard"
    uv run python -m octoops --setup
    Write-Host ""
    Write-Host "Done. Start with:  uv run python -m octoops"
}
