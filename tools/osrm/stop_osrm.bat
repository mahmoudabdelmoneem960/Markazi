@echo off
rem Stops the local OSRM server
taskkill /IM osrm-routed.exe /F >nul 2>&1
if %errorlevel%==0 (echo OSRM stopped.) else (echo OSRM was not running.)
ping -n 3 127.0.0.1 >nul
