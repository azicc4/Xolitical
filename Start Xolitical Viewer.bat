@echo off
rem ---------------------------------------------------------------------------
rem  Xolitical Viewer launcher - double-click to start (or restart).
rem  Every run stops any viewer that is already running and starts a fresh one,
rem  so the viewer always runs the current code. Nothing is lost: every save is
rem  written to disk the moment you make it.
rem  To stop the viewer, close the minimized "Xolitical Viewer" window.
rem ---------------------------------------------------------------------------
setlocal
cd /d "%~dp0"
set "SERVER=%~dp0tools\viewer\server.py"
set "PYEXE="
set "PYARGS="

rem Prefer the "py" launcher (standard python.org install), then "python".
where py >nul 2>nul && py -3 -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul && set "PYEXE=py" && set "PYARGS=-3"
if not defined PYEXE (
  where python >nul 2>nul && python -c "import sys; sys.exit(sys.version_info < (3, 9))" >nul 2>nul && set "PYEXE=python"
)

if not defined PYEXE (
  title Xolitical Viewer
  echo.
  echo   The Xolitical viewer needs Python 3.9 or newer, and none was found.
  echo.
  echo   1. Download it from https://www.python.org/downloads/
  echo   2. In the installer, tick "Add python.exe to PATH".
  echo   3. Double-click this file again.
  echo.
  pause
  exit /b 1
)

echo Restarting the Xolitical viewer - your browser will open in a moment...

rem 1. Ask a running viewer to save its session and shut down.
"%PYEXE%" %PYARGS% "%SERVER%" --stop >nul 2>nul

rem 2. Stop anything still running from this folder (e.g. a viewer from an older
rem    version, or a leftover window), so only current code runs.
powershell -NoProfile -Command "$s = $env:SERVER.ToLower(); Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -and $_.CommandLine.ToLower().Contains($s) -and $_.CommandLine -notmatch '--rebuild' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" >nul 2>nul

rem 3. Start the viewer in a minimized window.
start "Xolitical Viewer - close this window to stop it" /min cmd /c ""%PYEXE%" %PYARGS% "%SERVER%" || pause"
exit /b 0
