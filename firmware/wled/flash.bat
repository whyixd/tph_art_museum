@echo off
rem Double-click entry for flash.py. Needs uv (https://docs.astral.sh/uv/).
uv run "%~dp0flash.py" %*
pause
