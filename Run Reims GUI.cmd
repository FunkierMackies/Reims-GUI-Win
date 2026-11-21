@echo off
rem Portable launcher: resolves the manager exe relative to this batch file,
rem so the whole Reims-GUI-Win folder can live on any drive.
set "ROOT=%~dp0"
start "" "%ROOT%manager\Reims GUI Manager.exe" %*
