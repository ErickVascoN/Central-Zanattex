@echo off
REM ============================================================
REM  Inicia a Central de Dados Zanattex (Django) e abre no navegador.
REM  Duplo-clique neste arquivo para rodar. Para parar: feche esta janela.
REM ============================================================
setlocal

set "PROJ=%~dp0"
set "PY=C:\Users\erick\venvs\central-zanattex\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERRO] Ambiente virtual nao encontrado em:
    echo        %PY%
    echo Rode primeiro a instalacao do venv (ver README.md).
    pause
    exit /b 1
)

cd /d "%PROJ%"

echo Iniciando a Central de Dados... aguarde.
REM Abre o navegador na tela de login apos 3s (sem travar o servidor)
start "" cmd /c "timeout /t 3 >nul & start http://localhost:8000/"

"%PY%" manage.py runserver

pause
