@echo off
rem Vocab flashcards on the CYD (flash env cyd_vocab first): starts the resident program with vocab on.
rem Keys 1 = no, 2 = yes, knob = prev/next/flip; keys 3-6 keep their usual actions.
rem Stop any other "python -m macropad" or game_bridge first. Quit: Ctrl+Alt+Shift+0 or Ctrl+C
cd /d "%~dp0"
uv run --python 3.12 python -m macropad --vocab %*
pause
