@echo off
REM Double-click this file to launch the Kick Off 2 ELO ratings Streamlit dashboard.
REM Folder with this BAT file = repo root (where dashboard.py lives).

cd /d "%~dp0"
echo Installing Python packages if needed...
python -m pip install -q -r requirements.txt
echo Starting dashboard (browser should open shortly)...
python -m streamlit run "%~dp0dashboard.py"

pause
