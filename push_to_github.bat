@echo off
title Push AlphaScalp to GitHub
color 0A
cls
echo ====================================================
echo   AlphaScalp - GitHub Push Sync Tool
echo ====================================================
echo.
echo Target Repo: https://github.com/gorthailand1617-glitch/crypto_trading_bot.git
echo Branch:      main
echo.
echo Sending files to GitHub...
echo.

git push -u origin main --force

echo.
if %errorlevel% equ 0 (
    echo ====================================================
    echo   [SUCCESS] Push to GitHub completed successfully!
    echo ====================================================
) else (
    echo ====================================================
    echo   [FAILED] Error pushing to GitHub.
    echo   Please check if you authorized GitHub login above.
    echo ====================================================
)
echo.
pause
