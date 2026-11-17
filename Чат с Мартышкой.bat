@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title Мартышка — текстовый чат
cd /d "%~dp0"
".venv\Scripts\python.exe" chat.py
pause
