@echo off
chcp 65001 >nul
set PYTHONUTF8=1
title Мартышка
cd /d "%~dp0"
".venv\Scripts\python.exe" martyshka_main.py
pause
