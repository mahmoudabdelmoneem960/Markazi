@echo off
rem Starts the local OSRM routing server (real road distances) on 127.0.0.1:5001
rem OSRM home: this folder if it holds the tools, else %USERPROFILE%\osrm (or set the OSRM_HOME variable)
setlocal
if "%OSRM_HOME%"=="" (
  if exist "%~dp0bin\binding_napi_v8\osrm-routed.exe" (set "OSRM_HOME=%~dp0") else (set "OSRM_HOME=%USERPROFILE%\osrm")
)
if "%OSRM_HOME:~-1%"=="\" set "OSRM_HOME=%OSRM_HOME:~0,-1%"
set "EXE=%OSRM_HOME%\bin\binding_napi_v8\osrm-routed.exe"
set "DATA=%OSRM_HOME%\data\egypt.osrm"

if not exist "%EXE%" (
  echo [!] osrm-routed.exe not found: %EXE%
  pause
  exit /b 1
)
if not exist "%DATA%.mldgr" (
  echo [!] prepared map data not found: %DATA%.mldgr
  pause
  exit /b 1
)

tasklist /FI "IMAGENAME eq osrm-routed.exe" 2>nul | find /I "osrm-routed.exe" >nul
if %errorlevel%==0 (
  echo OSRM is already running on 127.0.0.1:5001
  ping -n 3 127.0.0.1 >nul
  exit /b 0
)

start "OSRM server 127.0.0.1:5001" /min "%EXE%" --algorithm mld --ip 127.0.0.1 --port 5001 --max-table-size 100 --threads 8 "%DATA%"
echo OSRM is starting on 127.0.0.1:5001 (loading the map takes a few seconds)...
ping -n 6 127.0.0.1 >nul
exit /b 0
