@echo off
setlocal
cd /d "%~dp0.."

where py >nul 2>&1
if errorlevel 1 (
    echo Se requiere Python 3.12: https://www.python.org/downloads/
    pause
    exit /b 1
)

py -3.12 --version >nul 2>&1
if errorlevel 1 (
    echo No se encontro Python 3.12. Instalalo y volve a ejecutar este archivo.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    pause
    exit /b 1
)

if not exist "config.toml" if exist "config.example.toml" (
    copy /y "config.example.toml" "config.toml" >nul
)

start "" ".venv\Scripts\pythonw.exe" -m jarvis
endlocal
