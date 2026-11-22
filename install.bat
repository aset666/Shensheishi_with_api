@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" py -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
if not exist ".env" copy ".env.example" ".env" >nul
echo Installation finished. Fill .env, then run start.bat.
pause
exit /b 0
:fail
echo Installation failed. Copy the error text, without API keys.
pause
exit /b 1
