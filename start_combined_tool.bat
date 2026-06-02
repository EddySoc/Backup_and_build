@echo off
setlocal

cd /d "%~dp0"

echo.
echo ====================================
echo   Starting Build + Spec Combo Tool
echo ====================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo Creating local virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        python -m venv .venv
        if errorlevel 1 (
            echo Failed to create virtual environment.
            pause
            exit /b 1
        )
    )
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 (
    echo Failed to activate virtual environment.
    pause
    exit /b 1
)

python -c "import customtkinter" >nul 2>nul
if errorlevel 1 (
    echo Installing required dependency: customtkinter
    python -m pip install --upgrade pip customtkinter
    if errorlevel 1 (
        echo Failed to install dependencies.
        pause
        exit /b 1
    )
)

python "build_combined_gui.py"
if errorlevel 1 (
    echo.
    echo Error running Build + Spec Combo Tool.
    pause
)
