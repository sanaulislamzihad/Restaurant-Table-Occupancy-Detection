@echo off
rem Starts the backend (which starts MediaMTX itself) and the dashboard, each in its own window,
rem and opens the dashboard in the browser. Do the setup steps in README.md once first.
rem To stop: press Ctrl+C in both windows, or close them.
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo The Python environment .venv is missing. Do the setup steps in README.md first.
    pause
    exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
    echo npm was not found. Install Node.js ^(see README.md^), then run this again.
    pause
    exit /b 1
)
if not exist "fake_camera\bin\mediamtx.exe" (
    echo Downloading MediaMTX...
    ".venv\Scripts\python.exe" fake_camera\download_mediamtx.py
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

rem Install the dashboard packages when some are missing or out of date.
pushd frontend
call npm ls --depth=0 >nul 2>nul
if errorlevel 1 (
    echo Installing the dashboard packages...
    call npm install
    if errorlevel 1 (
        popd
        pause
        exit /b 1
    )
)
popd

rem OpenCV prints only fatal FFmpeg messages; the backend logs connection problems itself.
set OPENCV_FFMPEG_LOGLEVEL=8
start "Table Occupancy - backend" /D "%~dp0backend" cmd /k "..\.venv\Scripts\python.exe -m app"
start "Table Occupancy - dashboard" /D "%~dp0frontend" cmd /k "npm run dev -- --open"
echo The backend and the dashboard are starting in two new windows.
echo The dashboard opens in the browser; the backend log is in backend\logs\backend.log.
