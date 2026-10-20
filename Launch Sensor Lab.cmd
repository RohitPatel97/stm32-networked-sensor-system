@echo off
setlocal
cd /d "%~dp0"
title STM32 Sensor Lab - Renode Demo
if "%~1"=="" if exist "build\desktop\SensorLab.exe" (
  start "" "build\desktop\SensorLab.exe"
  exit /b 0
)
if not exist ".venv\Scripts\python.exe" (
  echo Missing project Python environment. See docs\SENSOR-LAB.md.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\interview_demo.py" %*
if errorlevel 1 (
  echo.
  echo The demo or test run failed. See the error above and docs\SENSOR-LAB.md.
  pause
  exit /b 1
)
