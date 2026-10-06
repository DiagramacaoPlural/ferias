@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Preparando o ambiente pela primeira vez...
    python -m venv .venv || goto :erro
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :erro
)

start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" run.py %*
goto :eof

:erro
echo.
echo Nao foi possivel preparar o ambiente. Verifique se o Python 3.10+ esta instalado.
pause
