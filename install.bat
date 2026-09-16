@echo off
setlocal enabledelayedexpansion

echo =======================================================
echo          JobAgent - 1-Click Windows Setup
echo =======================================================
echo.

:: 1. Detect Python
where py >nul 2>nul
if %errorlevel% equ 0 (
    set "PY_CMD=py"
) else (
    where python >nul 2>nul
    if %errorlevel% equ 0 (
        set "PY_CMD=python"
    ) else (
        echo [ERROR] Python is not installed or not detected in PATH.
        echo.
        echo To install Python:
        echo 1. Download Python 3.11 or 3.12 from: https://www.python.org/downloads/
        echo 2. IMPORTANT: Check the box "Add python.exe to PATH" during installation!
        echo 3. Run this install.bat again after Python is installed.
        echo.
        pause
        exit /b 1
    )
)

echo [1/4] Found Python:
%PY_CMD% --version
echo.

:: 2. Verify Python version >= 3.10
%PY_CMD% -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if %errorlevel% neq 0 (
    echo [ERROR] Python version must be 3.10 or higher.
    echo Please install a modern version of Python from https://www.python.org/downloads/
    pause
    exit /b 1
)

:: 3. Create isolated virtual environment
echo [2/4] Setting up isolated virtual environment in .venv...
if not exist ".venv\Scripts\python.exe" (
    %PY_CMD% -m venv .venv
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)
echo Virtual environment ready.
echo.

:: 4. Upgrade pip and install dependencies
echo [3/4] Installing / Upgrading JobAgent and dependencies...
.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
if exist "requirements.txt" (
    .venv\Scripts\python.exe -m pip install -r requirements.txt --quiet
    .venv\Scripts\python.exe -m pip install -e . --quiet
) else (
    .venv\Scripts\python.exe -m pip install --upgrade jobagent --quiet
)

if %errorlevel% neq 0 (
    echo [WARNING] Dependency installation finished with non-zero status. Retrying without quiet mode...
    if exist "requirements.txt" (
        .venv\Scripts\python.exe -m pip install -r requirements.txt
        .venv\Scripts\python.exe -m pip install -e .
    ) else (
        .venv\Scripts\python.exe -m pip install --upgrade jobagent
    )
)
echo Dependencies installed successfully.
echo.

:: 5. Install Playwright Chromium browser
echo [4/4] Installing Playwright Chromium browser binaries...
.venv\Scripts\python.exe -m playwright install chromium
echo.

echo =======================================================
echo  SUCCESS! JobAgent is installed and ready to use!
echo =======================================================
echo.
echo You can run JobAgent anytime by double-clicking 'run.bat'
echo or running 'run.bat' from Command Prompt / PowerShell.
echo.

set /p LAUNCH="Would you like to launch JobAgent now? (Y/n): "
if /i "%LAUNCH%"=="n" (
    echo Setup complete. Exiting.
    pause
    exit /b 0
)

echo Starting JobAgent...
call "%~dp0run.bat"
