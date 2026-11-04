@echo off
setlocal
"%~dp0runtime\python.exe" -m native_sim_cli %*
exit /b %errorlevel%
