@echo off
echo ========================================
echo  Building WealthWatch Desktop
echo ========================================
echo.

:: Install dependencies
pip install -r requirements.txt pyinstaller

:: Build the exe
pyinstaller build.spec --clean --noconfirm

echo.
echo ========================================
echo  Build complete!
echo  Output: dist\WealthWatch.exe
echo ========================================
pause
