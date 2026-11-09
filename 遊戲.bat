@echo off
rem Six-lock safe game: forward macropad keys to the CYD (flash env cyd_game first).
rem Stop the resident "python -m macropad" before running. Quit: Ctrl+C
cd /d "%~dp0"
uv run --python 3.12 python -m macropad.game_bridge %*
pause
