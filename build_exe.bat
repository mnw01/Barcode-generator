@echo off
echo Starting Build Process...
echo.
pyinstaller --noconfirm --onefile --windowed --name "BarcodeGenerator" main.py
echo.
echo Build Complete!
echo The new exe is in the 'dist' folder.
pause
