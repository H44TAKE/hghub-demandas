@echo off
rem Faz o painel abrir sozinho toda vez que este PC ligar (e o usuario entrar no Windows).
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$p=Join-Path ([Environment]::GetFolderPath('Startup')) 'Painel de Manutencao.lnk'; $s=(New-Object -ComObject WScript.Shell).CreateShortcut($p); $s.TargetPath='%~dp0iniciar_painel.bat'; $s.WorkingDirectory='%~dp0'; $s.WindowStyle=7; $s.Save(); Write-Host 'Pronto: o painel vai abrir sozinho quando o PC ligar.'"
pause
