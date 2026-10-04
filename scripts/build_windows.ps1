$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)
python -m pip install -r requirements.txt
python -m PyInstaller --noconfirm --clean --windowed --name FrameForgeStudio frameforge_launcher.py
Write-Host "Build complete: dist\FrameForgeStudio\FrameForgeStudio.exe"

