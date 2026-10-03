# Installs Kvist from the release assets, on Windows, with nothing but PowerShell 5.1+:
#   * the agent binary as %LOCALAPPDATA%\Kvist\kvist-core.exe plus the `kvist` launcher
#     in %LOCALAPPDATA%\Kvist\bin, added to the user PATH (the layout
#     scripts\installer\install_cli.py produces);
#   * the JetBrains plugin into every JetBrains IDE found;
#   * the VS Code extension into VS Code, VS Code Insiders and VSCodium.
#
#   irm https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.ps1 | iex
#   .\install.ps1 -From .\downloaded-assets      # use files already on disk
param(
    [string]$From = ""
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is orders of magnitude slower with the progress bar

$ReleaseUrl = "https://github.com/davud-d/Kvist/releases/download/latest-plugin-build"
$BinaryAsset = "kvist-windows-x64.exe"
$JetBrainsAsset = "kvist-jetbrains.zip"
$VsCodeAsset = "kvist-vscode.vsix"
$JetBrainsFolder = "kvist-intellij"
$JetBrainsProducts = "IntelliJIdea|IdeaIC|PyCharm|PyCharmCE|WebStorm|PhpStorm|GoLand|CLion|Rider|RubyMine|DataGrip|DataSpell|RustRover|AndroidStudio|Aqua"

$LibDir = Join-Path $env:LOCALAPPDATA "Kvist"
$BinDir = Join-Path $LibDir "bin"

function Write-Step([string]$Message) { Write-Host "==> $Message" }

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "The release binary is built for 64-bit Windows."
}

$WorkDir = Join-Path ([IO.Path]::GetTempPath()) ("kvist-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $WorkDir | Out-Null

# Returns the path of the named asset, downloading it unless -From was given.
function Get-Asset([string]$Name) {
    if ($From) {
        $local = Join-Path $From $Name
        if (-not (Test-Path -LiteralPath $local -PathType Leaf)) { throw "$Name not found in $From" }
        return (Resolve-Path -LiteralPath $local).Path
    }
    Write-Step "Downloading $Name"
    $target = Join-Path $WorkDir $Name
    Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseUrl/$Name" -OutFile $target
    return $target
}

function Install-Cli([string]$Binary) {
    New-Item -ItemType Directory -Force -Path $LibDir, $BinDir | Out-Null
    $core = Join-Path $LibDir "kvist-core.exe"
    # A running kvist-core.exe cannot be overwritten, but it can be renamed away.
    $old = "$core.old"
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force }
    if (Test-Path -LiteralPath $core) { Move-Item -LiteralPath $core -Destination $old -Force }
    Copy-Item -LiteralPath $Binary -Destination $core -Force
    Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue

    # --workspace comes first so extra flags do not have to repeat it; argparse
    # keeps the last occurrence, so an explicit --workspace still wins.
    $wrapper = "@echo off`r`nsetlocal`r`n`"$core`" --workspace `"%CD%`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    # cmd.exe reads a batch file in the OEM code page, so a non-ASCII user name survives only in that encoding.
    $oem = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
    [IO.File]::WriteAllText((Join-Path $BinDir "kvist.cmd"), $wrapper, $oem)
    $webWrapper = "@echo off`r`n`"$core`" --web-controller %*`r`nexit /b %ERRORLEVEL%`r`n"
    $bridgeWrapper = "@echo off`r`n`"$core`" --web-bridge %*`r`nexit /b %ERRORLEVEL%`r`n"
    [IO.File]::WriteAllText((Join-Path $BinDir "kvist-web.cmd"), $webWrapper, $oem)
    [IO.File]::WriteAllText((Join-Path $BinDir "kvist-web-bridge.cmd"), $bridgeWrapper, $oem)
    Write-Step "Installed $core and the kvist, kvist-web and kvist-web-bridge launchers"
}

function Add-ToUserPath([string]$Dir) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if ($current) { $entries = $current -split ";" | Where-Object { $_ } }
    if ($entries | Where-Object { $_.TrimEnd("\") -ieq $Dir.TrimEnd("\") }) { return $false }
    [Environment]::SetEnvironmentVariable("Path", (($entries + $Dir) -join ";"), "User")
    return $true
}

function Install-JetBrains([string]$Zip) {
    $root = Join-Path $env:APPDATA "JetBrains"
    $ides = @()
    if (Test-Path -LiteralPath $root) {
        $ides = Get-ChildItem -LiteralPath $root -Directory | Where-Object { $_.Name -match "^($JetBrainsProducts)\d" }
    }
    if (-not $ides) {
        Write-Step "No JetBrains IDE found, skipping the plugin"
        return
    }
    foreach ($ide in $ides) {
        $plugins = Join-Path $ide.FullName "plugins"
        New-Item -ItemType Directory -Force -Path $plugins | Out-Null
        $staging = Join-Path $plugins (".kvist-install-" + [Guid]::NewGuid().ToString("N"))
        try {
            Expand-Archive -LiteralPath $Zip -DestinationPath $staging -Force
            $unpacked = Join-Path $staging $JetBrainsFolder
            if (-not (Test-Path -LiteralPath $unpacked -PathType Container)) { throw "plugin folder $JetBrainsFolder is missing from the archive" }
            $target = Join-Path $plugins $JetBrainsFolder
            if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Recurse -Force }
            Move-Item -LiteralPath $unpacked -Destination $target
            Write-Step "JetBrains plugin installed into $($ide.FullName) (restart the IDE)"
        } catch {
            Write-Warning "could not install the plugin into $($ide.FullName): $($_.Exception.Message)"
        } finally {
            if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
        }
    }
}

function Find-VsCodeEditors {
    $names = @("code.cmd", "code-insiders.cmd", "codium.cmd")
    $found = @()
    foreach ($name in $names) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $found += $command.Source }
    }
    $programs = @((Join-Path $env:LOCALAPPDATA "Programs"), $env:ProgramFiles, ${env:ProgramFiles(x86)}) | Where-Object { $_ }
    foreach ($dir in $programs) {
        foreach ($pair in @(@("Microsoft VS Code", "code.cmd"), @("Microsoft VS Code Insiders", "code-insiders.cmd"), @("VSCodium", "codium.cmd"))) {
            $candidate = Join-Path $dir (Join-Path $pair[0] (Join-Path "bin" $pair[1]))
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { $found += $candidate }
        }
    }
    return $found | Select-Object -Unique
}

function Install-VsCode([string]$Vsix) {
    $editors = Find-VsCodeEditors
    if (-not $editors) {
        Write-Step "No VS Code, Insiders or VSCodium found, skipping the extension"
        return
    }
    foreach ($editor in $editors) {
        & $editor --install-extension $Vsix --force | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Step "VS Code extension installed into $editor (reload the editor)"
        } else {
            Write-Warning "could not install the extension into $editor"
        }
    }
}

try {
    # Fetched before anything is installed, so a missing asset leaves the machine untouched.
    $binary = Get-Asset $BinaryAsset
    $jetBrainsZip = Get-Asset $JetBrainsAsset
    $vsix = Get-Asset $VsCodeAsset

    Install-Cli $binary
    Install-JetBrains $jetBrainsZip
    Install-VsCode $vsix

    if (Add-ToUserPath $BinDir) {
        Write-Host "`nDone. Added $BinDir to your user PATH. Open a new terminal, then run kvist in any directory to start the agent there."
    } else {
        Write-Host "`nDone. Run kvist in any directory to start the agent there."
    }
} finally {
    Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
}
