@echo off
title Crypto-Trader-bot Runner
cls

:: Check for virtual environment and activate if exists
if exist .venv\Scripts\activate.bat (
    echo [INFO] Activating virtual environment from .venv...
    call .venv\Scripts\activate.bat
) else if exist venv\Scripts\activate.bat (
    echo [INFO] Activating virtual environment from venv...
    call venv\Scripts\activate.bat
)

:: Detect python command
set PYTHON_CMD=python
python --version >nul 2>&1
if %errorlevel% equ 0 goto python_detected

py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PYTHON_CMD=py
    echo [INFO] 'python' was not found in PATH, using 'py' launcher instead.
    goto python_detected
)

echo [WARNING] Neither 'python' nor 'py' command was found in your PATH.

:python_detected

:menu
cls
echo ====================================================
echo        AlphaScalp Trading Engine V5.0 Runner
echo ====================================================
echo.
echo Please select an option:
echo [1] Install / Update Dependencies (pip install)
echo [2] Run Trading Bot (bot.py)
echo [3] Run Streamlit Dashboard (run_dashboard.py)
echo [4] Run Both (Bot and Dashboard)
echo [5] Exit
echo.
echo ====================================================
set /p opt="Enter your choice (1-5): "

if "%opt%"=="1" goto install_deps
if "%opt%"=="2" goto run_bot
if "%opt%"=="3" goto run_dash
if "%opt%"=="4" goto run_both
if "%opt%"=="5" goto exit_bot
echo Invalid option. Please select 1, 2, 3, 4, or 5.
pause
goto menu

:install_deps
cls
echo Installing dependencies from requirements.txt...
%PYTHON_CMD% -m pip install --default-timeout=60 -r requirements.txt
if %errorlevel% neq 0 (
    echo.
    echo [ERROR] Failed to install dependencies. Make sure python/py and pip are in your PATH.
) else (
    echo.
    echo [SUCCESS] Dependencies installed successfully!
)
pause
goto menu

:run_bot
cls
echo Starting Trading Bot (bot.py)...
%PYTHON_CMD% bot.py
pause
goto menu

:run_dash
cls
echo Starting Streamlit Dashboard (run_dashboard.py)...
%PYTHON_CMD% run_dashboard.py
pause
goto menu

:run_both
cls
echo Starting both Trading Bot and Dashboard in separate windows...
start "Trading Bot" cmd /k %PYTHON_CMD% bot.py
start "Streamlit Dashboard" cmd /k %PYTHON_CMD% run_dashboard.py
echo Launch commands sent! You can check the separate windows.
pause
goto menu

:exit_bot
echo Goodbye!
exit /b
