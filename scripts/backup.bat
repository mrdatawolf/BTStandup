@echo off
setlocal
cd /d "%~dp0\.."
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found. Follow the setup steps in README.md.
  exit /b 1
)
".venv\Scripts\python.exe" backup.py
