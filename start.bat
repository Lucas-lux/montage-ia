@echo off
REM Lanceur de developpement (Windows) : demarre l'application depuis les sources.
REM Utilise le venv du depot s'il est fonctionnel, sinon le Python du systeme.
REM (La version installee se lance depuis le raccourci Montage IA.)
cd /d "%~dp0"
set "PY=python"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -c "import fastapi, faster_whisper" >nul 2>&1 && set "PY=.venv\Scripts\python.exe"
)
"%PY%" app.py
if errorlevel 1 pause
