@echo off
rem Double-click launcher for Windows. Runs launch.ps1 in this folder.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launch.ps1"
