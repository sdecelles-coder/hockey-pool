@echo off
REM Double-clique ce fichier pour rafraichir les contrats (fetch + commit + push).
cd /d "%~dp0"
python refresh_contracts.py %*
echo.
pause
