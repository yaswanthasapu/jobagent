@echo off
setlocal

:: Resolve directory of this script
set "SCRIPT_DIR=%~dp0"

:: Check for local isolated virtual environment
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" (
    "%SCRIPT_DIR%.venv\Scripts\python.exe" -m jobagent %*
    goto :end
)

:: Fall back to global py or python
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -m jobagent %*
    goto :end
)

where python >nul 2>nul
if %errorlevel% equ 0 (
    python -m jobagent %*
    goto :end
)

echo [ERROR] Python not found in PATH or .venv.
echo Please run install.bat first to set up JobAgent.
pause

:end
endlocal
