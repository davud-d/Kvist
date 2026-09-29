$ErrorActionPreference = "Stop"

$ScriptDir = $PSScriptRoot
$ProjectDir = (Get-Item (Join-Path $ScriptDir "..\..\..")).FullName
$ExtensionDir = $ScriptDir
# Keep the embedded core build private to the extension. The repository-level
# dist/ and build/ directories are used by other installers and must not race it.
$CoreBuildDir = Join-Path $ExtensionDir ".build\core"
$DistDir = Join-Path $CoreBuildDir "dist"
$WorkDir = Join-Path $CoreBuildDir "work"
$VsixDir = Join-Path $ExtensionDir ".build\vsix"
$WinBinarySrc = Join-Path $DistDir "kvist.exe"
$WinBinaryDst = Join-Path $ExtensionDir "bin\win\kvist.exe"

function Write-Log($Message) {
    Write-Host "===> $Message" -ForegroundColor Cyan
}

function Find-Python {
    foreach ($RelativePath in @(".venv\Scripts\python.exe", "venv\Scripts\python.exe")) {
        $Candidate = Join-Path $ProjectDir $RelativePath
        if (Test-Path $Candidate) { return $Candidate }
    }

    $PythonPath = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($PythonPath) { return $PythonPath.Source }

    $PythonPath = Get-Command python -ErrorAction SilentlyContinue
    if ($PythonPath) { return $PythonPath.Source }

    throw "Python interpreter not found."
}

function Find-CommandPath([string[]]$Names) {
    foreach ($Name in $Names) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command) { return $Command.Source }
    }
    throw "Command not found: $($Names -join ', ')"
}

function Invoke-Native([string]$FilePath, [string[]]$Arguments) {
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE: $FilePath $($Arguments -join ' ')"
    }
}

function Ensure-NodeDeps {
    if (Test-Path (Join-Path $ExtensionDir "node_modules")) { return }

    Write-Log "Installing VS Code extension dependencies"
    Set-Location $ExtensionDir
    if (Test-Path (Join-Path $ExtensionDir "package-lock.json")) {
        Invoke-Native $NpmBin @("ci")
    } else {
        Invoke-Native $NpmBin @("install")
    }
}

$PythonBin = Find-Python
$NpmBin = Find-CommandPath @("npm.cmd", "npm.exe", "npm")
$NpxBin = Find-CommandPath @("npx.cmd", "npx.exe", "npx")

Write-Log "Project directory: $ProjectDir"
Write-Log "Extension directory: $ExtensionDir"
Write-Log "Python: $PythonBin"
Write-Log "npm: $NpmBin"

if (-not (Test-Path (Join-Path $ProjectDir "kvist.spec"))) {
    throw "kvist.spec not found in $ProjectDir"
}

if (-not (Test-Path (Join-Path $ExtensionDir "package.json"))) {
    throw "package.json not found in $ExtensionDir"
}

Ensure-NodeDeps

Write-Log "Cleaning previous Python build artifacts"
if (Test-Path $CoreBuildDir) { Remove-Item -Path $CoreBuildDir -Recurse -Force }

Write-Log "Building Kvist binary with PyInstaller"
Set-Location $ProjectDir
Invoke-Native $PythonBin @("-m", "PyInstaller", "kvist.spec", "--noconfirm", "--distpath", $DistDir, "--workpath", $WorkDir)

if (-not (Test-Path $WinBinarySrc)) {
    throw "PyInstaller output not found: $WinBinarySrc"
}

Write-Log "Installing Windows binary into extension"
$DestFolder = Split-Path $WinBinaryDst
if (-not (Test-Path $DestFolder)) { New-Item -ItemType Directory -Path $DestFolder | Out-Null }
Copy-Item -Path $WinBinarySrc -Destination $WinBinaryDst -Force

Write-Log "Compiling VS Code extension"
Set-Location $ExtensionDir
Invoke-Native $NpmBin @("run", "build")

$Package = Get-Content (Join-Path $ExtensionDir "package.json") -Raw | ConvertFrom-Json
$VsixPath = Join-Path $VsixDir "kvist-$($Package.version)-win32-x64.vsix"
if (-not (Test-Path $VsixDir)) { New-Item -ItemType Directory -Path $VsixDir | Out-Null }

Write-Log "Packaging VS Code extension VSIX"
Invoke-Native $NpxBin @("--no-install", "vsce", "package", "--no-dependencies", "--skip-license", "--out", $VsixPath)

Write-Log "Build completed successfully"
Write-Host "VSIX: $VsixPath"
