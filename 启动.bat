@echo off
setlocal
cd /d "%~dp0"

where pyw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pyw.exe "%~dp0my_devices.py"
    exit /b
)

where pythonw.exe >nul 2>nul
if not errorlevel 1 (
    start "" pythonw.exe "%~dp0my_devices.py"
    exit /b
)

where python.exe >nul 2>nul
if not errorlevel 1 (
    start /min "" python.exe "%~dp0my_devices.py"
    exit /b
)

echo Python 3.10+ was not found. Install Python first.
pause
