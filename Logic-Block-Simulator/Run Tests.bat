@echo off
setlocal
cd /d "%~dp0"
"%~dp0runtime\python.exe" -m unittest -v test_native_simulator
set "sim_exit=%errorlevel%"
echo.
pause
exit /b %sim_exit%
