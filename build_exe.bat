@echo off
setlocal

cd /d "%~dp0"

set "PYTHON=python"
if exist "venv\Scripts\python.exe" set "PYTHON=venv\Scripts\python.exe"
if exist ".venv\Scripts\python.exe" set "PYTHON=.venv\Scripts\python.exe"

echo [1/8] Cleaning previous build outputs...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo [2/8] Installing build requirements...
%PYTHON% -m pip install -r requirements-build.txt
if errorlevel 1 goto fail

echo [3/8] Building AttendanceBot.exe...
%PYTHON% -m PyInstaller --noconfirm --clean AttendanceBot.spec
if errorlevel 1 goto fail

echo [4/8] Building AttendanceBotManager.exe...
%PYTHON% -m PyInstaller --noconfirm --clean AttendanceBotManager.spec
if errorlevel 1 goto fail

echo [5/8] Creating release folder...
if not exist release mkdir release
if not exist release\data mkdir release\data
if not exist release\backups mkdir release\backups
if not exist release\logs mkdir release\logs

echo [6/8] Copying executable and support files...
copy /y dist\AttendanceBot.exe release\AttendanceBot.exe
if errorlevel 1 goto fail
copy /y dist\AttendanceBotManager.exe release\AttendanceBotManager.exe
if errorlevel 1 goto fail
copy /y .env.example release\.env.example
if errorlevel 1 goto fail
copy /y README.txt release\README.txt
if errorlevel 1 goto fail

echo [7/8] Release layout:
dir release

echo [8/8] Build succeeded.
echo.
echo Release folder is ready: %CD%\release
pause
exit /b 0

:fail
echo.
echo Build failed. Check the messages above.
pause
exit /b 1
