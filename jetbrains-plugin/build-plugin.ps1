# Stop on all errors
$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$ProjectDir = (Get-Item "$ScriptDir\..").FullName
$PluginDir = Join-Path $ProjectDir "jetbrains-plugin"
# The core gets its own dist/work dirs so a parallel install_cli.py build of
# the project's dist\kvist.exe cannot corrupt this one (or be wiped by it).
$CoreBuildDir = Join-Path $PluginDir ".build\core"
$DistDir = Join-Path $CoreBuildDir "dist"
$WorkDir = Join-Path $CoreBuildDir "work"
$PluginBuildDir = Join-Path $PluginDir "build"
$SandboxRoot = Join-Path $PluginDir ".intellijPlatform\sandbox"
$PluginName = "kvist-intellij"
$SandboxBinarySuffix = "plugins\$PluginName\bin\win\kvist.exe"

$WinBinarySrc = Join-Path $DistDir "kvist.exe"
$WinBinaryDst = Join-Path $PluginDir "bin\win\kvist.exe"

function Write-Log($Message) {
    Write-Host "===> $Message" -ForegroundColor Cyan
}

function Find-Python {
    $VenvPath = Join-Path $ProjectDir "venv\Scripts\python.exe"
    if (Test-Path $VenvPath) { return $VenvPath }
    
    $PythonPath = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonPath) { return $PythonPath.Source }
    
    throw "Python interpreter not found."
}

function Stop-SandboxAgents {
    $Processes = Get-CimInstance Win32_Process -Filter "Name = 'kvist.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            ($_.ExecutablePath -and
                [System.IO.Path]::GetFullPath($_.ExecutablePath).StartsWith(
                    [System.IO.Path]::GetFullPath($SandboxRoot),
                    [System.StringComparison]::OrdinalIgnoreCase
                ) -and
                [System.IO.Path]::GetFullPath($_.ExecutablePath).EndsWith(
                    $SandboxBinarySuffix,
                    [System.StringComparison]::OrdinalIgnoreCase
                )) -or
            ($_.CommandLine -and $_.CommandLine.IndexOf($SandboxBinarySuffix, [System.StringComparison]::OrdinalIgnoreCase) -ge 0)
        }

    if ($Processes) {
        Write-Log "Stopping running sandbox Kvist processes"
        $Processes | ForEach-Object {
            Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Start-Sleep -Seconds 1
    }
}

function Refresh-SandboxBinary {
    if (-not (Test-Path $SandboxRoot)) { return }
    
    $CopiedAny = $false
    # Find all kvist.exe files specifically inside the plugin's bin/win path within sandbox
    $Pattern = [regex]::Escape($SandboxBinarySuffix)
    $SandboxBins = Get-ChildItem -Path $SandboxRoot -Filter "kvist.exe" -Recurse
    
    foreach ($Bin in $SandboxBins) {
        if ($Bin.FullName -match $Pattern) {
            Copy-Item -Path $WinBinaryDst -Destination $Bin.FullName -Force
            Write-Host "    synced: $($Bin.FullName)"
            $CopiedAny = $true
        }
    }
    
    if ($CopiedAny) { Write-Log "Sandbox binaries refreshed" }
}

# --- Execution ---

$PythonBin = Find-Python

Write-Log "Project directory: $ProjectDir"
Write-Log "Plugin directory: $PluginDir"
Write-Log "Python: $PythonBin"

if (-not (Test-Path (Join-Path $ProjectDir "kvist.spec"))) {
    throw "kvist.spec not found in $ProjectDir"
}

if (-not (Test-Path (Join-Path $PluginDir "gradlew.bat"))) {
    throw "gradlew.bat not found in $PluginDir"
}

Stop-SandboxAgents

Write-Log "Cleaning previous build artifacts"
foreach ($Dir in @($CoreBuildDir, $PluginBuildDir)) {
    if (Test-Path $Dir) { Remove-Item -Path $Dir -Recurse -Force }
}

Write-Log "Building Kvist binary with PyInstaller"
Set-Location $ProjectDir
& $PythonBin -m PyInstaller kvist.spec --noconfirm --distpath $DistDir --workpath $WorkDir

if (-not (Test-Path $WinBinarySrc)) {
    throw "PyInstaller output not found: $WinBinarySrc"
}

Write-Log "Installing Windows binary into plugin"
$DestFolder = Split-Path $WinBinaryDst
if (-not (Test-Path $DestFolder)) { New-Item -ItemType Directory -Path $DestFolder | Out-Null }
Copy-Item -Path $WinBinarySrc -Destination $WinBinaryDst -Force

Refresh-SandboxBinary

Write-Log "Building JetBrains plugin ZIP"
Set-Location $PluginDir
cmd.exe /c "gradlew.bat clean buildPlugin"

Write-Log "Build completed successfully"
$DistPath = Join-Path $PluginDir "build\distributions\"
Write-Host "ZIP: $DistPath"
