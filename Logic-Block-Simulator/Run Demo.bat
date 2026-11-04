@echo off
setlocal
cd /d "%~dp0"
"%~dp0runtime\python.exe" "%~dp0run_demo.py"
set "sim_exit=%errorlevel%"
echo.
pause
exit /b %sim_exit%
