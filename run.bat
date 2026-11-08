@echo off
rem Run from source on Windows (needs Python 3 with tkinter - the python.org installer has it).
cd /d "%~dp0"
where pyw >nul 2>nul && (start "" pyw -3 ps4_pkg_region_gui.py & exit /b)
where pythonw >nul 2>nul && (start "" pythonw ps4_pkg_region_gui.py & exit /b)
python ps4_pkg_region_gui.py
pause
