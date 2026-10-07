@echo off
rem ---------------------------------------------------------------------------
rem  Xolitical Viewer launcher - double-click to start.
rem  Finds Python 3.9+, starts the viewer in a minimized window, and opens it
rem  in your browser. Double-clicking again while it runs just reopens the tab.
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

echo Starting the Xolitical viewer - your browser will open in a moment...
start "Xolitical Viewer - close this window to stop it" /min cmd /c ""%PYEXE%" %PYARGS% "%SERVER%" || pause"
exit /b 0
