@echo off
title Nexus Downloads - Live Log
cd /d "%~dp0.."
echo Following master.log - Ctrl+C closes only this window, downloads keep running.
echo.
powershell -NoProfile -Command "Get-Content -LiteralPath '.\master.log' -Tail 40 -Wait"
echo.
echo Log window closed. Downloads are unaffected.
pause
