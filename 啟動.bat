@echo off
rem Double-click to start smart-macropad in the background. Quit: Ctrl+Alt+Shift+0
cd /d "%~dp0"
set "PYW=pythonw"
where uv >nul 2>nul
if %errorlevel%==0 (
    for /f "delims=" %%i in ('uv python find 3.12 2^>nul') do set "PY=%%i"
)
if defined PY set "PYW=%PY:python.exe=pythonw.exe%"
start "" "%PYW%" -m macropad
