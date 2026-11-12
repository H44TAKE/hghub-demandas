@echo off
rem Painel de TV - Manutencao REDE HG
rem Abre o painel e o reabre sozinho se ele cair (queda de energia, erro, etc).
cd /d "%~dp0"
:inicio
python painel.py
timeout /t 15 /nobreak >nul
goto inicio
