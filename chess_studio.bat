@echo off
cd /d "%~dp0"
if exist venv\Scripts\pythonw.exe (
  start "" venv\Scripts\pythonw.exe chess_studio.pyw
) else (
  start "" pyw chess_studio.pyw
)
