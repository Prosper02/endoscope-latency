@echo off
rem Double-click launcher for EndoLatency on Windows. Installs dependencies on first use.
cd /d "%~dp0"
python -c "import PySide6, serial, mss, scipy, matplotlib" 2>nul || python -m pip install -r requirements.txt
python -m endolatency
