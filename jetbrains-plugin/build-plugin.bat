@echo off
setlocal enabledelayedexpansion

:: Exit on error equivalent
set "EXIT_ON_ERROR=1"

:: Path definitions
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%.."
set "PROJECT_DIR=%CD%"
set "PLUGIN_DIR=%PROJECT_DIR%\jetbrains-plugin"
set "DIST_DIR=%PROJECT_DIR%\dist"
set "BUILD_DIR=%PROJECT_DIR%\build"
set "PLUGIN_BUILD_DIR=%PLUGIN_DIR%\build"
set "SANDBOX_ROOT=%PLUGIN_DIR%\.intellijPlatform\sandbox"
set "PLUGIN_NAME=devagent-intellij"
set "SANDBOX_BINARY_SUFFIX=plugins\%PLUGIN_NAME%\bin\win\devagent.exe"

:: Binary names (adding .exe for Windows)
set "WIN_BINARY_SRC=%DIST_DIR%\devagent.exe"
set "WIN_BINARY_DST=%PLUGIN_DIR%\bin\win\devagent.exe"

call :log "Project directory: %PROJECT_DIR%"
call :log "Plugin directory: %PLUGIN_DIR%"

:: Find Python interpreter
call :find_python
if %ERRORLEVEL% neq 0 (
    echo Python interpreter not found. >&2
    exit /b 1
)
call :log "Python: %PYTHON_BIN%"

:: Check for required files
if not exist "%PROJECT_DIR%\devagent.spec" (
    echo devagent.spec not found in %PROJECT_DIR% >&2
    exit /b 1
)

if not exist "%PLUGIN_DIR%\gradlew.bat" (
    echo gradlew.bat not found in %PLUGIN_DIR% >&2
    exit /b 1
)

:: Stop running sandbox agent processes
call :stop_running_sandbox_agents

call :log "Cleaning previous build artifacts"
if exist "%DIST_DIR%" rd /s /q "%DIST_DIR%"
if exist "%BUILD_DIR%" rd /s /q "%BUILD_DIR%"
if exist "%PLUGIN_BUILD_DIR%" rd /s /q "%PLUGIN_BUILD_DIR%"

call :log "Building DevAgent binary with PyInstaller"
cd /d "%PROJECT_DIR%"
"%PYTHON_BIN%" -m PyInstaller devagent.spec
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

if not exist "%WIN_BINARY_SRC%" (
    echo PyInstaller output not found: %WIN_BINARY_SRC% >&2
    exit /b 1
)

call :log "Installing Windows binary into plugin"
if not exist "%PLUGIN_DIR%\bin\win" mkdir "%PLUGIN_DIR%\bin\win"
copy /y "%WIN_BINARY_SRC%" "%WIN_BINARY_DST%" >nul

:: Refresh binaries in the IntelliJ sandbox
call :refresh_sandbox_binary

call :log "Building JetBrains plugin ZIP"
cd /d "%PLUGIN_DIR%"
call gradlew.bat clean buildPlugin
if %ERRORLEVEL% neq 0 exit /b %ERRORLEVEL%

call :log "Build completed successfully"
echo ZIP: %PLUGIN_DIR%\build\distributions\
goto :eof

:: --- Functions ---

:log
echo ===^> %~1
goto :eof

:find_python
if exist "%PROJECT_DIR%\venv\Scripts\python.exe" (
    set "PYTHON_BIN=%PROJECT_DIR%\venv\Scripts\python.exe"
    exit /b 0
)
where python >nul 2>nul
if %ERRORLEVEL% equ 0 (
    for /f "delims=" %%i in ('where python') do (
        set "PYTHON_BIN=%%i"
        exit /b 0
    )
)
exit /b 1

:stop_running_sandbox_agents
set "STOPPED_ANY="
where powershell.exe >nul 2>nul
if errorlevel 1 goto :eof

for /f "usebackq delims=" %%i in (`powershell.exe -NoProfile -Command "$ErrorActionPreference = 'SilentlyContinue'; $sandboxRoot = [System.IO.Path]::GetFullPath('%SANDBOX_ROOT%'); $binarySuffix = '%SANDBOX_BINARY_SUFFIX%'; $targetProcesses = Get-CimInstance Win32_Process -Filter \"Name = 'devagent.exe'\" ^| Where-Object { ($_.ExecutablePath -and [System.IO.Path]::GetFullPath($_.ExecutablePath).StartsWith($sandboxRoot, [System.StringComparison]::OrdinalIgnoreCase) -and [System.IO.Path]::GetFullPath($_.ExecutablePath).EndsWith($binarySuffix, [System.StringComparison]::OrdinalIgnoreCase)) -or ($_.CommandLine -and $_.CommandLine.IndexOf($binarySuffix, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) }; foreach ($process in $targetProcesses) { $process.ProcessId }"`) do (
    if not defined STOPPED_ANY (
        set "STOPPED_ANY=1"
        call :log "Stopping running sandbox DevAgent processes"
    )
    taskkill /f /pid %%i >nul 2>&1
)

if defined STOPPED_ANY (
    timeout /t 1 /nobreak >nul
)
goto :eof

:refresh_sandbox_binary
if not exist "%SANDBOX_ROOT%" goto :eof
set "COPIED_ANY=0"

:: Recursively find and replace binary in sandbox folders
for /r "%SANDBOX_ROOT%" %%f in (devagent.exe) do (
    echo %%f | findstr /c:"%SANDBOX_BINARY_SUFFIX%" >nul
    if not errorlevel 1 (
        copy /y "%WIN_BINARY_DST%" "%%f" >nul
        echo     synced: %%f
        set "COPIED_ANY=1"
    )
)

if "%COPIED_ANY%"=="1" (
    call :log "Sandbox binaries refreshed"
)
goto :eof