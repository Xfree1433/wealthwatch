@echo off
echo ========================================
echo  Building WealthWatch Desktop
echo ========================================
echo.

:: Install dependencies
pip install -r requirements.txt pyinstaller

:: Build the folder-based exe
python -m PyInstaller build.spec --clean --noconfirm

:: Create zip for distribution
echo.
echo Creating distribution zip...
cd dist
powershell -Command "Compress-Archive -Path 'WealthWatch' -DestinationPath 'WealthWatch.zip' -Force"
cd ..

echo.
echo ========================================
echo  Build complete!
echo  Folder: dist\WealthWatch\
echo  Zip:    dist\WealthWatch.zip
echo ========================================
pause
