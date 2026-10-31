@echo off
rem Lets you type "nuphy" (the board) or "nuphy pad", "nuphy config ..." (this folder is on PATH).
if "%~1"=="" (
  python "%~dp0nuphy_tui.py"
) else (
  python "%~dp0nuphyhid.py" %*
)
