@echo off
rem Build a portable single-file Windows exe.  Needs: Python 3 (with tkinter) + pip install pyinstaller
setlocal
cd /d "%~dp0\.."
for /f %%v in ('python -c "import ps4_pkg_region_gui as g; print(g.__version__)"') do set VERSION=%%v
if "%VERSION%"=="" (echo Could not read the version & exit /b 1)
set NAME=PS4-PKG-Region-Changer-%VERSION%-windows-x64
if exist dist\%NAME%.exe del dist\%NAME%.exe
set EXTRA=
if exist bundled-tools (set EXTRA=--add-data "%CD%\bundled-tools;bundled-tools" & echo Embedding bundled-tools)
python -m PyInstaller --noconfirm --clean --onefile --windowed --name %NAME% ^
    --icon "%CD%\packaging\icon.ico" --distpath dist --workpath build\win --specpath build\win ^
    %EXTRA% "%CD%\ps4_pkg_region_gui.py"
if errorlevel 1 exit /b 1
echo Built dist\%NAME%.exe
endlocal
