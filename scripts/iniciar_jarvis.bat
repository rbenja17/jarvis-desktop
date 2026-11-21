@echo off
rem Inicia JARVIS sin ventana de consola (orbe + bandeja).
cd /d "%~dp0.."
start "" ".venv\Scripts\pythonw.exe" -m jarvis
