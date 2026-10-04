@echo off
setlocal
cd /d "%~dp0.."
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --windowed --name FrameForgeStudio frameforge_launcher.py
echo.
echo Build complete: dist\FrameForgeStudio\FrameForgeStudio.exe
pause
