@echo off
title Nexus Download Supervisor
cd /d "%~dp0.."
python "%~dp0supervisor.py"
echo.
echo Supervisor exited. Check supervisor.log.
pause
