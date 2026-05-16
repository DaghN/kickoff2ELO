@echo off
title Export KOATD Scores -> CSV
cd /d "%~dp0"

if not exist "koatd.mdb" (
  echo ERROR: koatd.mdb not found in this folder:
  echo   %~dp0
  echo Copy koatd.mdb here next to EXPORT_KOATD.bat then try again.
  pause
  exit /b 1
)

if not exist "data" mkdir data

python -m pip install -q pyodbc
set PYTHONPATH=src
python -m kool_elo.export_access_table --mdb "%~dp0koatd.mdb" --table Scores --out "%~dp0data\koatd_scores_export.csv"
if errorlevel 1 goto BAD

python -m kool_elo.export_access_table --mdb "%~dp0koatd.mdb" --table "Tournament players" --out "%~dp0data\koatd_tournament_players_export.csv"
if errorlevel 1 goto BAD

echo.
echo SUCCESS — CSV exports:
echo   %~dp0data\koatd_scores_export.csv
echo   %~dp0data\koatd_tournament_players_export.csv
for %%A in ("%~dp0data\koatd_scores_export.csv") do echo   Scores CSV size: %%~zA bytes
for %%A in ("%~dp0data\koatd_tournament_players_export.csv") do echo   Tournaments CSV size: %%~zA bytes
echo.
goto DONE

:BAD
  echo.
  echo If you see ODBC / driver errors, install this once Microsoft driver 64-bit:
  echo https://learn.microsoft.com/en-us/office/troubleshoot/access/jet-accdb-registry-key
  echo Or search for: AccessDatabaseEngine_X64.exe
  echo.
:DONE
pause
