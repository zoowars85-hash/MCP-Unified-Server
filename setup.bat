@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"
:: ВАЖНО: сохраните этот файл в кодировке UTF-8 with BOM!

:: === DIAGNOSTICS ===
echo ===============================================
echo MCP Setup Script
echo Current folder: %cd%
echo Date: %date% %time%
echo ===============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3.10+ and add to PATH.
    pause >nul
    exit /b 1
)
echo [OK] Python found:
python --version
echo.

setlocal enabledelayedexpansion

:: ==================== PATHS ====================
set "PYTHON_DEPS_DIR=%~dp0python_deps"
set "VENV_DIR=%~dp0.venv"
set "PYTHON_EXE=%VENV_DIR%\Scripts\python.exe"
set "PID_FILE=%VENV_DIR%\server.pid"
set "STARTUP_DIR=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "LINK_NAME=MCP_Server.lnk"
set "SCRIPT_PATH=%~dp0mcp_fs_server.py"
set "WORK_DIR=%~dp0"
:: Префикс PowerShell для принудительного TLS 1.2 (нужен на старых Windows)
set "PS_TLS=[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12;"

:: ==================== AUTO-SETUP ON FIRST RUN ====================
if not exist "%VENV_DIR%" (
    echo Virtual environment not found. Creating...
    call :clean_venv_silent
    if errorlevel 1 (
        echo Error creating venv.
        pause >nul
        exit /b 1
    )
    if exist "%PYTHON_DEPS_DIR%\*.whl" (
        echo Installing dependencies from python_deps...
        call "%PYTHON_EXE%" mcp_setup.py --offline
    ) else (
        echo Downloading dependencies from internet...
        call "%PYTHON_EXE%" mcp_setup.py --online
        call "%PYTHON_EXE%" mcp_setup.py --offline
    )
)

:: ==================== CHECK SERVER STATUS ====================
set "SERVER_RUNNING=0"
set "PID="
if exist "%PID_FILE%" (
    set /p PID=<"%PID_FILE%"
    if defined PID (
        tasklist /fi "PID eq !PID!" 2>nul | find /i "!PID!" >nul
        if not errorlevel 1 set "SERVER_RUNNING=1"
    )
)

:: ==================== MAIN MENU ====================
:menu
cls
echo ================================================
echo        MCP Filesystem Server – Management
echo ================================================
echo.
if "%SERVER_RUNNING%"=="1" (
    echo [SERVER] Running (PID: !PID!^)
) else (
    echo [SERVER] Stopped
)
echo.
echo --- Environment and Dependencies ---
echo  1. Recreate virtual environment (clean^)
echo  2. Download dependencies (online^)
echo  3. Install dependencies (offline from python_deps^)
echo  4. Check and install missing dependencies (quick^)
echo.
echo --- Configuration ---
echo  5. Fix paths in mcpServers.json (manual^)
echo  6. Update LM Studio config (legacy^)
echo  7. Fix LM Studio config (fully automatic^)
echo.
echo --- Server Control ---
echo  8. Start / Stop server
echo  9. Add to startup
echo  A. Remove from startup
echo.
echo --- Extra Tools ---
echo  B. Install pandoc + wkhtmltopdf (for PDF export^)
echo  C. Install RAG dependencies (sentence-transformers + chromadb^)
echo  D. Install web-reader advanced deps
echo  E. Install mempalace (external memory palace^)
echo.
echo  F. Exit
echo.

choice /C 123456789ABCDEF /N /M "Choose action: "
set "CHOICE=%errorlevel%"

:: Правильное соответствие номерам в меню
if "%CHOICE%"=="15" goto exit
if "%CHOICE%"=="14" goto install_mempalace
if "%CHOICE%"=="13" goto install_web_deps
if "%CHOICE%"=="12" goto install_rag_deps
if "%CHOICE%"=="11" goto install_pdf_tools
if "%CHOICE%"=="10" goto remove_autostart
if "%CHOICE%"=="9"  goto add_autostart
if "%CHOICE%"=="8"  goto toggle_server
if "%CHOICE%"=="7"  goto auto_fix_lmstudio
if "%CHOICE%"=="6"  goto fix_lmstudio
if "%CHOICE%"=="5"  goto fix_config_paths
if "%CHOICE%"=="4"  goto check_and_install
if "%CHOICE%"=="3"  goto offline_install
if "%CHOICE%"=="2"  goto online_download
if "%CHOICE%"=="1"  goto clean_venv
goto menu

:: ==================== 1. RECREATE VENV ====================
:clean_venv
echo.
echo ================================================
echo   Recreating virtual environment
echo ================================================
call :clean_venv_silent
if errorlevel 1 (
    echo.
    echo [ERROR] Failed to create or fix virtual environment.
    echo Check that Python is correctly installed.
    pause
    goto menu
)
echo [OK] Virtual environment ready.
pause
goto menu

:clean_venv_silent
if exist "%VENV_DIR%" (
    echo Removing old .venv folder...
    rmdir /s /q "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] Could not remove old venv. Maybe files are locked?
        exit /b 1
    )
)
echo Creating new venv...
python -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo [ERROR] Failed to create virtual environment.
    exit /b 1
)
echo Ensuring pip is available...
"%PYTHON_EXE%" -m ensurepip --upgrade >nul 2>&1
if errorlevel 1 (
    echo ensurepip failed, trying to download get-pip.py...
    powershell -NoProfile -Command "%PS_TLS% Invoke-WebRequest -Uri https://bootstrap.pypa.io/get-pip.py -OutFile '%TEMP%\get-pip.py'" >nul 2>&1
    if exist "%TEMP%\get-pip.py" (
        "%PYTHON_EXE%" "%TEMP%\get-pip.py" >nul 2>&1
        del "%TEMP%\get-pip.py" 2>nul
    ) else (
        echo [ERROR] Could not get get-pip.py. No internet?
        exit /b 1
    )
)
"%PYTHON_EXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Pip not available. Reinstall Python from python.org.
    exit /b 1
)
echo [OK] Virtual environment ready with pip.
exit /b 0

:: ==================== 2. DOWNLOAD DEPENDENCIES ====================
:online_download
echo.
echo ================================================
echo   Downloading dependencies (internet required^)
echo ================================================
if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found. Run step 1 first.
    pause
    goto menu
)
"%PYTHON_EXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo Pip not available. Recreate venv using option 1.
    pause
    goto menu
)
call "%PYTHON_EXE%" mcp_setup.py --online
if errorlevel 1 (
    echo Error during download.
) else (
    echo [OK] Dependencies downloaded to python_deps folder
)
pause
goto menu

:: ==================== 3. INSTALL DEPENDENCIES ====================
:offline_install
echo.
echo ================================================
echo   Installing dependencies from python_deps
echo ================================================
if not exist "%PYTHON_DEPS_DIR%\*.whl" (
    echo Error: no .whl files in python_deps. Run step 2 first.
    pause
    goto menu
)
if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found. Run step 1 first.
    pause
    goto menu
)
"%PYTHON_EXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo Pip not available. Recreate venv using option 1.
    pause
    goto menu
)
call "%PYTHON_EXE%" mcp_setup.py --offline
if errorlevel 1 (
    echo Error during installation.
) else (
    echo [OK] Dependencies installed. Restart LM Studio.
)
pause
goto menu

:: ==================== 4. CHECK AND INSTALL MISSING ====================
:check_and_install
echo.
echo ================================================
echo   Checking and installing missing dependencies
echo ================================================
if not exist "%PYTHON_EXE%" (
    echo Virtual environment not found. Run step 1 first.
    pause
    goto menu
)
"%PYTHON_EXE%" -m pip --version >nul 2>&1
if errorlevel 1 (
    echo Pip not available. Recreate venv using option 1.
    pause
    goto menu
)
call "%PYTHON_EXE%" mcp_setup.py --check
if errorlevel 1 (
    echo Error during check/install.
) else (
    echo [OK] All required dependencies are installed.
)
pause
goto menu

:: ==================== 5. FIX PATHS IN CUSTOM CONFIG ====================
:fix_config_paths
echo.
echo ================================================
echo   Fixing Python path in MCP config file
echo ================================================
echo.
set "CONFIG_PATH="
set /p CONFIG_PATH="Path to JSON file (Enter for C:\Tools\mcpServers.json): "
if "!CONFIG_PATH!"=="" set "CONFIG_PATH=C:\Tools\mcpServers.json"
call :fix_one_config "!CONFIG_PATH!"
pause
goto menu

:: ==================== 6. UPDATE LM STUDIO CONFIG (LEGACY) ====================
:fix_lmstudio
echo.
echo ================================================
echo   Updating LM Studio configuration (legacy^)
echo ================================================
set "LM_CONFIG=%USERPROFILE%\.lmstudio\mcp.json"
if not exist "%LM_CONFIG%" (
    echo Config not found: %LM_CONFIG%
    echo Searching in other locations...
    set "LM_CONFIG="
    for /f "delims=" %%i in ('dir /s /b "%USERPROFILE%\.lmstudio\*.json" 2^>nul ^| findstr /i "mcp"') do set "LM_CONFIG=%%i"
    if not defined LM_CONFIG (
        echo Cannot find config. Use manual step 5.
        pause
        goto menu
    )
)
echo Found config: !LM_CONFIG!
call :fix_one_config "!LM_CONFIG!"
echo Don't forget to restart LM Studio.
pause
goto menu

:: ==================== 7. FIX LM STUDIO CONFIG (FULLY AUTOMATIC) ====================
:auto_fix_lmstudio
echo.
echo ================================================
echo   Fixing LM Studio configuration (automatic^)
echo ================================================
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Virtual environment not found. Run step 1 first.
    pause
    goto menu
)
set "FIX_SCRIPT=%~dp0fix_lmstudio_config.py"
if not exist "%FIX_SCRIPT%" (
    echo [ERROR] fix_lmstudio_config.py not found.
    echo Please create it in the MCP folder.
    pause
    goto menu
)
echo Running fix script...
"%PYTHON_EXE%" "%FIX_SCRIPT%"
if errorlevel 1 (
    echo [ERROR] Failed to update config automatically.
    echo You can manually edit %USERPROFILE%\.lmstudio\mcp.json
) else (
    echo [SUCCESS] LM Studio config updated.
)
echo.
echo Please restart LM Studio if it was running.
pause
goto menu

:: ==================== COMMON FUNCTION TO FIX ONE CONFIG ====================
:fix_one_config
set "TARGET_CFG=%~1"
if not exist "%TARGET_CFG%" (
    echo [X] File not found: %TARGET_CFG%
    exit /b 1
)
if not exist "%PYTHON_EXE%" (
    echo [X] Python not found in venv. Run step 1 first.
    exit /b 1
)
echo Fixing: %TARGET_CFG%
call "%PYTHON_EXE%" "%~dp0mcp_setup.py" --fix-config "%TARGET_CFG%" "%PYTHON_EXE%"
exit /b %errorlevel%

:: ==================== 8. START / STOP SERVER ====================
:toggle_server
if "!SERVER_RUNNING!"=="1" (
    echo Stopping server...
    taskkill /pid !PID! /f >nul 2>&1
    del "%PID_FILE%" 2>nul
    set "SERVER_RUNNING=0"
    set "PID="
    echo Server stopped.
) else (
    if not exist "%PYTHON_EXE%" (
        echo Error: venv not found. Run steps 1 and 3.
        pause
        goto menu
    )
    if not exist "%SCRIPT_PATH%" (
        echo Error: mcp_fs_server.py not found in %~dp0
        pause
        goto menu
    )
    echo Starting server in background...
    powershell -NoProfile -Command "$exe = $env:PYTHON_EXE; $script = $env:SCRIPT_PATH; $wd = $env:WORK_DIR; $pidFile = $env:PID_FILE; $p = Start-Process -FilePath $exe -ArgumentList ('\"' + $script + '\"') -WorkingDirectory $wd -WindowStyle Hidden -PassThru; $p.Id | Out-File -FilePath $pidFile -Encoding ASCII"
    if exist "%PID_FILE%" (
        set /p NEW_PID=<"%PID_FILE%"
        echo Server started (PID: !NEW_PID!^)
        set "SERVER_RUNNING=1"
        set "PID=!NEW_PID!"
    ) else (
        echo [ERROR] Failed to start server.
    )
)
timeout /t 2 >nul
goto menu

:: ==================== 9. ADD TO AUTOSTART ====================
:add_autostart
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Virtual environment not found. Run step 1 first.
    pause
    goto menu
)
set "HIDDEN_LAUNCHER=%~dp0start_hidden.ps1"
echo Generating hidden launcher...
(
    echo # Auto-generated by setup.bat
    echo $exe = '%PYTHON_EXE%'
    echo $script = '%SCRIPT_PATH%'
    echo $workdir = '%WORK_DIR%'
    echo $pidFile = '%PID_FILE%'
    echo $p = Start-Process -FilePath $exe -ArgumentList ('"' + $script + '"'^) -WorkingDirectory $workdir -WindowStyle Hidden -PassThru
    echo $p.Id ^| Out-File -FilePath $pidFile -Encoding ASCII
) > "%HIDDEN_LAUNCHER%"

echo Creating startup shortcut...
powershell -NoProfile -Command "$s = (New-Object -COM WScript.Shell).CreateShortcut('%STARTUP_DIR%\%LINK_NAME%'); $s.TargetPath = 'powershell.exe'; $s.Arguments = '-ExecutionPolicy Bypass -WindowStyle Hidden -File \"%HIDDEN_LAUNCHER%\"'; $s.WorkingDirectory = '%~dp0'; $s.Save()"
if errorlevel 1 (
    echo [ERROR] Failed to create shortcut.
) else (
    echo [OK] Autostart added: %STARTUP_DIR%\%LINK_NAME%
)
pause
goto menu

:: ==================== 10. REMOVE FROM AUTOSTART ====================
:remove_autostart
if exist "%STARTUP_DIR%\%LINK_NAME%" (
    del "%STARTUP_DIR%\%LINK_NAME%"
    echo [OK] Autostart removed.
) else (
    echo Shortcut not found.
)
if exist "%~dp0start_hidden.ps1" (
    del "%~dp0start_hidden.ps1"
    echo [OK] Hidden launcher removed.
)
pause
goto menu

:: ==================== 11. INSTALL PDF TOOLS ====================
:install_pdf_tools
echo.
echo ================================================
echo   Installing pandoc + wkhtmltopdf
echo ================================================
echo.
set "TOOLS_DIR=%~dp0mcp_tools"
if not exist "%TOOLS_DIR%" mkdir "%TOOLS_DIR%"

echo Downloading pandoc...
powershell -NoProfile -Command "%PS_TLS% Invoke-WebRequest -Uri 'https://github.com/jgm/pandoc/releases/download/3.9.0.2/pandoc-3.9.0.2-windows-x86_64.msi' -OutFile '%TEMP%\pandoc.msi'"
if exist "%TEMP%\pandoc.msi" (
    echo Installing pandoc...
    start /wait msiexec /i "%TEMP%\pandoc.msi" /quiet /norestart
    del "%TEMP%\pandoc.msi" 2>nul
    echo [OK] pandoc installed.
) else (
    echo [WARN] Failed to download pandoc.
)
echo.
echo Downloading wkhtmltopdf...
powershell -NoProfile -Command "%PS_TLS% Invoke-WebRequest -Uri 'https://github.com/wkhtmltopdf/packaging/releases/download/0.12.6-1/wkhtmltox-0.12.6-1.msvc2015-win64.exe' -OutFile '%TEMP%\wkhtmltopdf.exe'"
if exist "%TEMP%\wkhtmltopdf.exe" (
    echo Installing wkhtmltopdf silently...
    start /wait "%TEMP%\wkhtmltopdf.exe" /S /D="%TOOLS_DIR%\wkhtmltopdf"
    del "%TEMP%\wkhtmltopdf.exe" 2>nul
    echo [OK] wkhtmltopdf installed to %TOOLS_DIR%\wkhtmltopdf
) else (
    echo [WARN] Failed to download wkhtmltopdf. Check your internet connection.
)
echo.
echo PDF tools installation finished.
pause
goto menu

:: ==================== 12. INSTALL RAG DEPS ====================
:install_rag_deps
echo.
echo ================================================
echo   Installing RAG dependencies
echo ================================================
echo.
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Virtual environment not found. Run step 1 first.
    pause
    goto menu
)
echo [1/3] Installing Python packages...
"%PYTHON_EXE%" -m pip install sentence-transformers chromadb pypdf pdfplumber ebooklib
if errorlevel 1 (
    echo [ERROR] Failed to install Python packages.
    pause
    goto menu
)
echo [2/3] Downloading default embedding model (all-MiniLM-L6-v2^)...
"%PYTHON_EXE%" -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('all-MiniLM-L6-v2')"
if errorlevel 1 (
    echo [WARN] Model download failed. It will be downloaded on first use.
)
echo [3/3] Creating RAG database directory...
if not exist "%~dp0mcp_rag_db" mkdir "%~dp0mcp_rag_db"
echo.
echo [OK] RAG dependencies installed.
echo You can now use tools: rag_index_folder, rag_search, rag_ask
echo.
pause
goto menu

:: ==================== 13. INSTALL WEB-READER ADVANCED DEPS ====================
:install_web_deps
echo.
echo ================================================
echo   Installing web-reader advanced dependencies
echo ================================================
echo.
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Virtual environment not found. Run step 1 first.
    pause
    goto menu
)
echo Installing trafilatura, readability-lxml, html-table-takeout, pandas...
"%PYTHON_EXE%" -m pip install trafilatura readability-lxml html-table-takeout pandas
if errorlevel 1 (
    echo [ERROR] Failed to install some packages.
) else (
    echo [OK] Advanced web-reader dependencies installed.
)
pause
goto menu

:: ==================== 14. INSTALL MEMPALACE ====================
:install_mempalace
echo.
echo ================================================
echo   Installing mempalace (external memory palace^)
echo ================================================
if not exist "%PYTHON_EXE%" (
    echo [ERROR] Virtual environment not found. Run step 1 first.
    pause
    goto menu
)
echo Installing mempalace package...
"%PYTHON_EXE%" -m pip install mempalace
if errorlevel 1 (
    echo [ERROR] Failed to install mempalace. Check internet connection.
) else (
    echo [OK] mempalace installed successfully.
    echo.
    echo Quick start:
    echo   mempalace init D:\myproject
    echo   mempalace mine D:\myproject --mode files
    echo   mempalace search "query"
    echo.
    echo MCP tools available: mempalace_init, mempalace_mine, mempalace_search
)
pause
goto menu

:exit
echo Press any key to exit...
pause >nul
endlocal
exit /b 0
