@echo off
setlocal
"%~dp0runtime\python.exe" -m native_trace %*
exit /b %errorlevel%
