@echo off
title Cafe Nube - mi-app-python
echo Usuario del sistema:
whoami
echo Fecha y hora de inicio: %DATE% %TIME%
echo Aplicacion: mi-app-python - http://localhost:8000/
echo Metricas: http://localhost:8000/metrics
cd /d "%~dp0"
".venv\Scripts\python.exe" -m uvicorn main:app --host 0.0.0.0 --port 8000
echo La aplicacion Python se detuvo.
pause
