@echo off
rem Fake CCTV camera: loops a video as a live RTSP stream (MediaMTX + ffmpeg).
rem Usage: fake_camera\start_camera.bat <video file name in VIDEOS_DIR, or a path>
rem Settings are read from backend\.env. See start_camera.py for details.
setlocal
set "PYTHON=%~dp0..\.venv\Scripts\python.exe"
if not exist "%PYTHON%" set "PYTHON=python"
"%PYTHON%" "%~dp0start_camera.py" %*
exit /b %ERRORLEVEL%
