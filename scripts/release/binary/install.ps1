# Installs Kvist from the single-binary release, on Windows, with nothing but PowerShell 5.1+.
# The binary carries the core, the terminal UI, the Node.js it runs on and the native session
# program, so this is the whole install:
#   * %LOCALAPPDATA%\Kvist\kvist-core.exe, checked against the release's SHA256SUMS;
#   * the `kvist` launcher in %LOCALAPPDATA%\Kvist\bin, added to the user PATH;
#   * on a first install with a terminal, the first-run setup screen right away.
#
#   irm https://github.com/davud-d/Kvist/releases/download/latest-binary-build/install.ps1 | iex
#   $env:KVIST_INSTALL_NO_RUN = "1"   # install only, do not open the setup screen
#
# KVIST_RELEASE_URL points the download at a mirror or a local test server.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is orders of magnitude slower with the progress bar

$ReleaseUrl = if ($env:KVIST_RELEASE_URL) { $env:KVIST_RELEASE_URL } else { "https://github.com/davud-d/Kvist/releases/download/latest-binary-build" }
$Asset = "kvist-windows-x64.exe"
$LibDir = Join-Path $env:LOCALAPPDATA "Kvist"
$BinDir = Join-Path $LibDir "bin"

function Write-Step([string]$Message) { Write-Host "==> $Message" }

if (-not [Environment]::Is64BitOperatingSystem) {
    throw "The Kvist binary is built for 64-bit Windows."
}

$WorkDir = Join-Path ([IO.Path]::GetTempPath()) ("kvist-install-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $WorkDir | Out-Null

try {
    Write-Step "Downloading $Asset (the one file that holds everything, about 180 MB)"
    $download = Join-Path $WorkDir $Asset
    Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseUrl/$Asset" -OutFile $download
    $sums = Join-Path $WorkDir "SHA256SUMS"
    Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseUrl/SHA256SUMS" -OutFile $sums

    # The sums file lists `<hash>  <name>`; only this system's line is compared.
    $line = Get-Content -LiteralPath $sums | Where-Object { ($_ -split "\s+")[1] -eq $Asset } | Select-Object -First 1
    if (-not $line) { throw "SHA256SUMS has no entry for $Asset" }
    $expected = ($line -split "\s+")[0]
    $actual = (Get-FileHash -LiteralPath $download -Algorithm SHA256).Hash
    if ($actual -ne $expected.ToUpperInvariant()) { throw "The downloaded $Asset does not match its SHA-256 sum; nothing was installed." }

    New-Item -ItemType Directory -Force -Path $LibDir, $BinDir | Out-Null
    $core = Join-Path $LibDir "kvist-core.exe"
    $firstInstall = -not (Test-Path -LiteralPath $core)
    # A running kvist-core.exe cannot be overwritten, but it can be renamed away.
    $old = "$core.old"
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force }
    if (Test-Path -LiteralPath $core) { Move-Item -LiteralPath $core -Destination $old -Force }
    Copy-Item -LiteralPath $download -Destination $core -Force
    Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue

    # --workspace comes first so extra flags do not have to repeat it; argparse keeps the last
    # occurrence, so an explicit --workspace still wins.
    $wrapper = "@echo off`r`nsetlocal`r`n`"$core`" --workspace `"%CD%`" %*`r`nexit /b %ERRORLEVEL%`r`n"
    # cmd.exe reads a batch file in the OEM code page, so a non-ASCII user name survives only in that encoding.
    $oem = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
    [IO.File]::WriteAllText((Join-Path $BinDir "kvist.cmd"), $wrapper, $oem)
    Write-Step "Installed $core and the kvist launcher"

    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if ($current) { $entries = $current -split ";" | Where-Object { $_ } }
    if (-not ($entries | Where-Object { $_.TrimEnd("\") -ieq $BinDir.TrimEnd("\") })) {
        [Environment]::SetEnvironmentVariable("Path", (($entries + $BinDir) -join ";"), "User")
        Write-Host "Added $BinDir to your user PATH; open a new terminal to use kvist there."
    }

    # The first run has no provider yet, so kvist opens the setup screen by itself.
    if ($firstInstall -and -not $env:KVIST_INSTALL_NO_RUN -and [Environment]::UserInteractive -and -not [Console]::IsInputRedirected) {
        & $core --workspace (Get-Location).Path
    } else {
        Write-Host "Done. Run kvist in a project folder to start."
    }
} finally {
    Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue
}
