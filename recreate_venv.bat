@echo off
setlocal EnableExtensions EnableDelayedExpansion

cd /d "%~dp0"

echo.
echo ====================================
echo   Recreate Python Virtual Env (.venv)
echo ====================================
echo.

set "AUTO_Y=0"
if /I "%~1"=="/y" set "AUTO_Y=1"

if exist ".venv" (
    if "%AUTO_Y%"=="1" (
        echo Removing existing .venv ...
        rmdir /s /q ".venv"
    ) else (
        set /p REPLY=.venv exists. Remove and recreate it? [y/N]: 
        if /I not "!REPLY!"=="y" (
            echo Aborted. No changes made.
            exit /b 0
        )
        echo Removing existing .venv ...
        rmdir /s /q ".venv"
    )
)

echo Creating virtual environment...
py -3 -m venv .venv
if errorlevel 1 (
    python -m venv .venv
    if errorlevel 1 (
        echo Failed to create virtual environment.
        echo Ensure Python is installed and available on PATH.
        exit /b 1
    )
)

echo Upgrading pip/setuptools/wheel...
.venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo Failed to upgrade base packaging tools.
    exit /b 1
)

set "REQ="
for %%F in (requirements.txt venv_packages.txt venv_packages_after_cleanup.txt venv_packages_final.txt venv_packages_cleanup.txt) do (
    if not defined REQ if exist "%%F" set "REQ=%%F"
)

if defined REQ (
    echo Installing dependencies from %REQ% ...
    .venv\Scripts\python.exe -m pip install -r "%REQ%"
    if errorlevel 1 (
        echo Dependency installation failed.
        exit /b 1
    )
) else (
    echo No requirements file found. Skipping dependency install.
)

echo.
echo Done.
echo To activate in cmd.exe: .venv\Scripts\activate.bat
echo To activate in PowerShell: ^& .venv\Scripts\Activate.ps1
exit /b 0
