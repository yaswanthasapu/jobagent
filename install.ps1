# JobAgent 1-Click PowerShell Setup
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host "         JobAgent - PowerShell Automated Setup         " -ForegroundColor White
Write-Host "=======================================================" -ForegroundColor Cyan
Write-Host ""

# 1. Detect Python
$pythonCmd = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $pythonCmd = "py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $pythonCmd = "python"
} elseif (Get-Command python3 -ErrorAction SilentlyContinue) {
    $pythonCmd = "python3"
}

if (-not $pythonCmd) {
    Write-Host "[ERROR] Python is not installed or not in PATH." -ForegroundColor Red
    Write-Host "Please install Python 3.10+ from: https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "Ensure 'Add python.exe to PATH' is checked during installation." -ForegroundColor Yellow
    Read-Host "Press Enter to exit"
    exit 1
}

Write-Host "[1/4] Found Python: $pythonCmd" -ForegroundColor Green
& $pythonCmd --version

# 2. Check version
$verCheck = & $pythonCmd -c "import sys; print(sys.version_info >= (3, 10))"
if ($verCheck.Trim() -ne "True") {
    Write-Host "[ERROR] Python version must be 3.10 or higher." -ForegroundColor Red
    Read-Host "Press Enter to exit"
    exit 1
}

# 3. Create virtual environment
Write-Host "`n[2/4] Setting up isolated virtual environment in .venv..." -ForegroundColor Cyan
$venvPath = Join-Path $PSScriptRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    & $pythonCmd -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] Failed to create virtual environment." -ForegroundColor Red
        exit 1
    }
}
Write-Host "Virtual environment ready." -ForegroundColor Green

# 4. Install dependencies
Write-Host "`n[3/4] Installing / Upgrading JobAgent and dependencies..." -ForegroundColor Cyan
& $venvPython -m pip install --upgrade pip --quiet
$reqFile = Join-Path $PSScriptRoot "requirements.txt"
if (Test-Path $reqFile) {
    & $venvPython -m pip install -r $reqFile --quiet
    & $venvPython -m pip install -e $PSScriptRoot --quiet
} else {
    & $venvPython -m pip install --upgrade jobagent --quiet
}

# 5. Install Playwright Chromium
Write-Host "`n[4/4] Installing Playwright Chromium browser binaries..." -ForegroundColor Cyan
& $venvPython -m playwright install chromium

Write-Host "`n=======================================================" -ForegroundColor Green
Write-Host " SUCCESS! JobAgent is installed and ready to use!" -ForegroundColor Green
Write-Host "=======================================================" -ForegroundColor Green
Write-Host "You can run JobAgent anytime using: .\run.bat or .\jobagent.ps1" -ForegroundColor Yellow

$response = Read-Host "Would you like to start JobAgent now? (Y/n)"
if ($response -ne "n" -and $response -ne "N") {
    & $venvPython -m jobagent
}
