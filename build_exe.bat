@echo off
echo Starting Build Process...
echo.
pyinstaller --noconfirm --onefile --windowed --name "BarcodeGenerator" --icon="app.ico" --add-data "app.ico;." main.py
echo.
echo Build Complete!
echo The new exe is in the 'dist' folder.
pause
