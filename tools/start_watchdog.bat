@echo off
title Nexus Download Watchdog
cd /d "%~dp0.."
start "" /min pythonw "%~dp0watchdog.py"
echo Watchdog started (invisible). It keeps the supervisor and log mirror alive.
timeout /t 3 >nul
