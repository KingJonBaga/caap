@echo off
rem Double-click this file to start NURTURA. Keep the window open while using the app.
cd /d "%~dp0"
title NURTURA server
set NO_OPEN=1
py server\server.py
pause
