
# Installs Kvist from the release assets, on Windows, with nothing but PowerShell 5.1+:
#   * the agent binary as %LOCALAPPDATA%\Kvist\kvist-core.exe plus the `kvist` launcher
#     in %LOCALAPPDATA%\Kvist\bin (the layout scripts\installer\install_cli.py produces);
#   * Node.js, which the terminal interface runs on, as %LOCALAPPDATA%\Kvist\node\node.exe
#     (downloaded from nodejs.org and checked against a pinned SHA-256);
#   * a static ffmpeg as %LOCALAPPDATA%\Kvist\ffmpeg\ffmpeg.exe (pinned SHA-256) that plays the
#     preview video of the setup screen;
#   * the pixel desktop app (frontends\gui\tauri) as %LOCALAPPDATA%\Kvist\app\kvist-app.exe, with a
#     "Kvist Pixel" shortcut in the Start menu and on the Desktop;
#   * the JetBrains plugin into every JetBrains IDE found;
#   * the VS Code extension into VS Code, VS Code Insiders and VSCodium;
#   * the folder of the `kvist` launcher on the user PATH.
#
# With a terminal to draw on, it downloads the agent and Node.js into a temporary folder and opens
# the installer screen (the same one install.sh opens): what to install is ticked there, and the
# setup follows it. The screen is as good as the terminal it runs in: pictures in kitty-graphics
# terminals (kitty, Ghostty), Sixel pictures in Sixel ones (Windows Terminal 1.22+, WezTerm), half-block art elsewhere.
#
#   irm https://github.com/davud-d/Kvist/releases/download/latest-plugin-build/install.ps1 | iex
#   $env:KVIST_INSTALL_PLAIN = "1"; irm <url> | iex     # no screen, install the defaults
#   .\install.ps1 -From .\downloaded-assets             # use files already on disk
#   .\install.ps1 -Plan                                 # JSON: what each component would touch here
#   .\install.ps1 -Only cli,path -Events                # chosen components, progress as JSON lines
#
# KVIST_RELEASE_URL points the download at a mirror or a local test server.
param(
    [string]$From = "",
    [string]$Cache = "",
    [string]$Only = "",
    [switch]$Plan,
    [switch]$Events
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is orders of magnitude slower with the progress bar
# Windows PowerShell 5.1 on an older Windows offers TLS 1.0 only, which GitHub refuses.
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

$ReleaseUrl = if ($env:KVIST_RELEASE_URL) { $env:KVIST_RELEASE_URL.TrimEnd("/") } else { "https://github.com/davud-d/Kvist/releases/download/latest-plugin-build" }
$BinaryAsset = "kvist-windows-x64.exe"
$AppAsset = "kvist-app-windows-x64.exe"
$JetBrainsAsset = "kvist-jetbrains.zip"
$VsCodeAsset = "kvist-vscode.vsix"
$JetBrainsFolder = "kvist-intellij"
$JetBrainsProducts = "IntelliJIdea|IdeaIC|PyCharm|PyCharmCE|WebStorm|PhpStorm|GoLand|CLion|Rider|RubyMine|DataGrip|DataSpell|RustRover|AndroidStudio|Aqua"

# The Node.js the interface runs on, pinned: the SHA-256 is the one in nodejs.org's SHASUMS256.txt for
# this version. Whatever a server sends, only the exact pinned file is kept, so the mirrors are safe.
$NodeVersion = "22.23.3"
$NodeAsset = "node-v$NodeVersion-win-x64.zip"
$NodeSha256 = "2b0ff57b049cda1bbcea2240eec20467018713c1efe1f7360c2681859b90ed71"
$NodeUrls = @("https://nodejs.org/dist/v$NodeVersion", "https://cdn.npmmirror.com/binaries/node/v$NodeVersion", "https://mirrors.ustc.edu.cn/node/v$NodeVersion")
# The ffmpeg that plays the preview video: the static FFmpeg 6.0 build the ffmpeg-static npm package ships
# (a gzipped single binary from its tagged GitHub release), pinned by the SHA-256 recorded when it was
# chosen; the publisher gives no SHA-256 of its own, so this pins the file, not its origin.
$FfmpegVersion = "6.0"
$FfmpegAsset = "ffmpeg-win32-x64.gz"
$FfmpegSha256 = "450d66226c79405c724e821f291cab0911e934bfa9fa2231adcab587f3e07b50"
$FfmpegUrls = @("https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0", "https://cdn.npmmirror.com/binaries/ffmpeg-static/b6.0")

$LibDir = Join-Path $env:LOCALAPPDATA "Kvist"
$BinDir = Join-Path $LibDir "bin"

# Without -Only the defaults are what the script always did, plus Node.js when the assets are downloaded
# (-From is an offline install of the files given).
$OnlyGiven = [bool]$Only
if (-not $OnlyGiven) { $Only = if ($From) { "cli,app,jetbrains,vscode,path" } else { "cli,node,app,jetbrains,vscode,path" } }
$Chosen = @($Only -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
function Test-Wanted([string]$Id) { return $Chosen -contains $Id }

# What the installer screen reads: one JSON object per line, UTF-8, on stdout. Written as bytes, so
# neither the console's code page nor a byte-order mark can get into it.
function Write-JsonLine($Object) {
    $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $Object -Compress -Depth 6) + "`n")
    $stdout = [Console]::OpenStandardOutput()
    $stdout.Write($bytes, 0, $bytes.Length)
    $stdout.Flush()
}

# Under -Events every line the person would read is one JSON object on stdout instead.
function Send-Event([string]$Name, [hashtable]$Fields = @{}) {
    if (-not $Events) { return }
    $object = [ordered]@{ event = $Name }
    foreach ($key in $Fields.Keys) { $object[$key] = $Fields[$key] }
    Write-JsonLine $object
}

function Write-Step([string]$Message) {
    if ($Events) { Send-Event "step" @{ message = $Message } } else { Write-Host "==> $Message" }
}

function Write-Warn([string]$Message) {
    if ($Events) { Send-Event "warning" @{ message = $Message } } else { Write-Warning $Message }
}

$WorkDir = ""

# Returns the path of the named asset: the one in -From, in -Cache (what a previous step already
# downloaded), or downloaded now.
function Get-Asset([string]$Name) {
    if ($From) {
        $local = Join-Path $From $Name
        if (-not (Test-Path -LiteralPath $local -PathType Leaf)) { throw "$Name not found in $From" }
        return (Resolve-Path -LiteralPath $local).Path
    }
    if ($Cache) {
        $cached = Join-Path $Cache $Name
        if (Test-Path -LiteralPath $cached -PathType Leaf) { return $cached }
    }
    Write-Step "Downloading $Name"
    $target = Join-Path $WorkDir $Name
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseUrl/$Name" -OutFile $target
    } catch {
        throw "could not download $ReleaseUrl/${Name}: $($_.Exception.Message)"
    }
    return $target
}

# Like Get-Asset, for what the release may not have (the desktop app is built beside the agent and
# a platform's build can fail): $null instead of stopping the whole install.
function Get-OptionalAsset([string]$Name) {
    try {
        return Get-Asset $Name
    } catch {
        Write-Warn "$Name is not available ($($_.Exception.Message)); the desktop app was not installed"
        return $null
    }
}

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

# Tries the servers in turn until one delivers a file with the expected SHA-256; $false when none did.
function Get-Verified([string]$Dest, [string]$Label, [string]$Sha256, [string[]]$Urls) {
    foreach ($url in $Urls) {
        $hostName = ([Uri]$url).Host
        Write-Step "Downloading $Label from $hostName"
        try {
            # A transfer that stalls for a minute is given up on, so a dead mirror ends the step instead of hanging it.
            Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 -Uri $url -OutFile $Dest
        } catch {
            Write-Warn "$Label could not be downloaded from ${hostName}: $($_.Exception.Message)"
            Remove-Item -LiteralPath $Dest -Force -ErrorAction SilentlyContinue
            continue
        }
        if ((Get-Sha256 $Dest) -eq $Sha256) { return $true }
        Write-Warn "$Label from $hostName did not arrive intact; trying the next server"
        Remove-Item -LiteralPath $Dest -Force
    }
    return $false
}

# The pinned release asset of a tool: from -From or -Cache when it is there, else downloaded from the
# servers. Returns the path of a file whose SHA-256 is the pinned one, or $null after saying why not.
function Get-PinnedAsset([string]$Name, [string]$Label, [string]$Sha256, [string[]]$Servers) {
    $path = $null
    if ($From) {
        $path = Join-Path $From $Name
    } elseif ($Cache -and (Test-Path -LiteralPath (Join-Path $Cache $Name) -PathType Leaf)) {
        $path = Join-Path $Cache $Name
    }
    if ($path) {
        if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { Write-Warn "$Name not found in $From"; return $null }
    } else {
        $path = Join-Path $WorkDir $Name
        if (-not (Get-Verified $path $Label $Sha256 @($Servers | ForEach-Object { "$_/$Name" }))) {
            Write-Warn "could not download $Label from any server"
            return $null
        }
    }
    if ((Get-Sha256 $path) -ne $Sha256) {
        Write-Warn "$Name does not match the pinned SHA-256; refusing to use it"
        return $null
    }
    return $path
}

# Sets $script:NodeHome to a folder holding node.exe: the one the bootstrap already prepared
# (KVIST_NODE_HOME), or the pinned release downloaded, checked and unpacked here. Only node.exe and
# its licence are taken out of the archive: the interface needs no npm, and npm's folders are
# nested deeper than Windows PowerShell 5.1 can open.
function Initialize-Node {
    if ($env:KVIST_NODE_HOME) {
        $script:NodeHome = $env:KVIST_NODE_HOME
        return
    }
    $zip = Get-PinnedAsset $NodeAsset "Node.js $NodeVersion" $NodeSha256 $NodeUrls
    if (-not $zip) { throw "could not get Node.js $NodeVersion" }
    $script:NodeHome = Join-Path $WorkDir "node"
    New-Item -ItemType Directory -Force -Path $script:NodeHome | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($zip)
    try {
        $wanted = @("node-v$NodeVersion-win-x64/node.exe", "node-v$NodeVersion-win-x64/LICENSE")
        foreach ($entry in $archive.Entries) {
            if ($wanted -notcontains $entry.FullName) { continue }
            [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $script:NodeHome $entry.Name), $true)
        }
    } finally {
        $archive.Dispose()
    }
    if (-not (Test-Path -LiteralPath (Join-Path $script:NodeHome "node.exe") -PathType Leaf)) { throw "node.exe is missing from $NodeAsset" }
}

# Replaces $Target with a copy of $Source's files, never leaving it half-written.
function Copy-Folder([string]$Source, [string]$Target) {
    New-Item -ItemType Directory -Force -Path $LibDir | Out-Null
    $staging = Join-Path $LibDir (".staging-" + (Split-Path -Leaf $Target))
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Path $staging | Out-Null
    Copy-Item -Path (Join-Path $Source "*") -Destination $staging -Recurse -Force
    if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Recurse -Force }
    Move-Item -LiteralPath $staging -Destination $Target
}

function Install-Node {
    Copy-Folder $script:NodeHome (Join-Path $LibDir "node")
    Write-Step "Installed Node.js $NodeVersion as $(Join-Path $LibDir 'node')"
}

# Sets $script:FfmpegHome to a folder holding the unpacked ffmpeg.exe. ffmpeg only plays a preview, so
# a failure here is a warning and $false, never the end of the install.
function Initialize-Ffmpeg {
    $gz = Get-PinnedAsset $FfmpegAsset "ffmpeg $FfmpegVersion" $FfmpegSha256 $FfmpegUrls
    if (-not $gz) { return $false }
    $script:FfmpegHome = Join-Path $WorkDir "ffmpeg"
    New-Item -ItemType Directory -Force -Path $script:FfmpegHome | Out-Null
    $stream = [IO.File]::OpenRead($gz)
    try {
        $unzip = New-Object IO.Compression.GZipStream($stream, [IO.Compression.CompressionMode]::Decompress)
        $output = [IO.File]::Create((Join-Path $script:FfmpegHome "ffmpeg.exe"))
        try { $unzip.CopyTo($output) } finally { $output.Dispose(); $unzip.Dispose() }
    } finally {
        $stream.Dispose()
    }
    return $true
}

function Install-Ffmpeg {
    Copy-Folder $script:FfmpegHome (Join-Path $LibDir "ffmpeg")
    Write-Step "Installed ffmpeg $FfmpegVersion as $(Join-Path $LibDir 'ffmpeg\ffmpeg.exe')"
}

function New-Shortcut([string]$LinkPath, [string]$Target, [string]$Description) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($LinkPath)
    $link.TargetPath = $Target
    $link.WorkingDirectory = Split-Path -Parent $Target
    $link.IconLocation = "$Target,0"
    $link.Description = $Description
    $link.Save()
}

function Get-StartMenuFolder { return Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs" }

# kvist-core.exe sits one folder up from the app, where the app looks for it.
function Install-App([string]$Binary) {
    $appDir = Join-Path $LibDir "app"
    New-Item -ItemType Directory -Force -Path $appDir | Out-Null
    $app = Join-Path $appDir "kvist-app.exe"
    # A running app cannot be overwritten, but it can be renamed away.
    $old = "$app.old"
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Force }
    if (Test-Path -LiteralPath $app) { Move-Item -LiteralPath $app -Destination $old -Force }
    Copy-Item -LiteralPath $Binary -Destination $app -Force
    Remove-Item -LiteralPath $old -Force -ErrorAction SilentlyContinue
    $menu = Get-StartMenuFolder
    New-Item -ItemType Directory -Force -Path $menu | Out-Null
    New-Shortcut (Join-Path $menu "Kvist Pixel.lnk") $app "Kvist agents side by side, in pixel style"
    $desktop = [Environment]::GetFolderPath("Desktop")
    if ($desktop) { New-Shortcut (Join-Path $desktop "Kvist Pixel.lnk") $app "Kvist agents side by side, in pixel style" }
    Write-Step "Installed $app with a Start menu entry and a Desktop shortcut"
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

function Test-OnUserPath([string]$Dir) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if ($current) { $entries = $current -split ";" | Where-Object { $_ } }
    return [bool]($entries | Where-Object { $_.TrimEnd("\") -ieq $Dir.TrimEnd("\") })
}

function Install-Path {
    if (Test-OnUserPath $BinDir) {
        Write-Step "$BinDir is already on your user PATH"
        return
    }
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @()
    if ($current) { $entries = $current -split ";" | Where-Object { $_ } }
    [Environment]::SetEnvironmentVariable("Path", (($entries + $BinDir) -join ";"), "User")
    Write-Step "Added $BinDir to your user PATH (open a new terminal to use kvist there)"
}

function Find-JetBrainsIdes {
    $root = Join-Path $env:APPDATA "JetBrains"
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    return @(Get-ChildItem -LiteralPath $root -Directory | Where-Object { $_.Name -match "^($JetBrainsProducts)\d" })
}

function Install-JetBrains([string]$Zip) {
    $ides = Find-JetBrainsIdes
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
            Write-Warn "could not install the plugin into $($ide.FullName): $($_.Exception.Message)"
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
    return @($found | Select-Object -Unique)
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
            Write-Warn "could not install the extension into $editor"
        }
    }
}

# --plan: what each component would do on this machine, as facts (paths and commands); the words
# around them belong to whoever shows them.
function Write-Plan {
    $ffmpegPresent = (Get-Command ffmpeg.exe -ErrorAction SilentlyContinue) -or (Test-Path -LiteralPath (Join-Path $LibDir "ffmpeg\ffmpeg.exe"))
    $desktop = [Environment]::GetFolderPath("Desktop")
    $appTargets = @("$ReleaseUrl/$AppAsset", (Join-Path $LibDir "app\kvist-app.exe"), (Join-Path (Get-StartMenuFolder) "Kvist Pixel.lnk"))
    if ($desktop) { $appTargets += (Join-Path $desktop "Kvist Pixel.lnk") }
    $ides = @(Find-JetBrainsIdes | ForEach-Object { Join-Path (Join-Path $_.FullName "plugins") $JetBrainsFolder })
    $editors = @(Find-VsCodeEditors | ForEach-Object { "$_ --install-extension $VsCodeAsset --force" })
    $onPath = Test-OnUserPath $BinDir
    $components = @(
        [ordered]@{ id = "cli"; available = $true; targets = @((Join-Path $LibDir "kvist-core.exe"), (Join-Path $BinDir "kvist.cmd"), (Join-Path $BinDir "kvist-web.cmd"), (Join-Path $BinDir "kvist-web-bridge.cmd")) },
        [ordered]@{ id = "node"; available = $true; targets = @("$($NodeUrls[0])/$NodeAsset", (Join-Path $LibDir "node")) },
        [ordered]@{ id = "ffmpeg"; available = (-not $ffmpegPresent); reason = $(if ($ffmpegPresent) { "present" } else { "" }); targets = @("$($FfmpegUrls[0])/$FfmpegAsset", (Join-Path $LibDir "ffmpeg")) },
        [ordered]@{ id = "app"; available = $true; reason = ""; targets = $appTargets },
        [ordered]@{ id = "jetbrains"; available = ($ides.Count -gt 0); targets = $ides },
        [ordered]@{ id = "vscode"; available = ($editors.Count -gt 0); targets = $editors },
        [ordered]@{ id = "path"; available = (-not $onPath); targets = @("user PATH (HKCU\Environment): + $BinDir") }
    )
    Write-JsonLine ([ordered]@{ os = "Windows"; arch = "x64"; shell = "powershell"; privilege = "none"; components = $components })
}

# True when there is a terminal to draw on: a real console, not a pipe, a redirect or the ISE.
function Test-Terminal {
    if ($Host.Name -eq "Windows PowerShell ISE Host") { return $false }
    return [Environment]::UserInteractive -and -not [Console]::IsInputRedirected -and -not [Console]::IsOutputRedirected
}

# The installer screen: the agent and Node.js go to a temporary folder first (the screen runs on both),
# the person chooses there, and the screen starts the setup when it is done. This script does the
# installing itself, driven by the screen through -Plan and -Only ... -Events.
function Start-InstallerScreen {
    Write-Host "Kvist installer: downloading the agent and Node.js $NodeVersion into a temporary folder (about 100 MB)."
    Write-Host "Nothing is installed until you choose it on the next screen."
    $core = Get-Asset $BinaryAsset
    Initialize-Node
    $node = Join-Path $script:NodeHome "node.exe"
    # Say here, not as a loader error from inside the temporary folder, that the binary cannot run.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $core --help *> $null } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { throw "the downloaded kvist-core.exe cannot run on this system" }
    # Run through `irm | iex` there is no file to hand over: the script is fetched again.
    $installScript = $PSCommandPath
    if (-not $installScript -or -not (Test-Path -LiteralPath $installScript -PathType Leaf)) {
        $installScript = Join-Path $WorkDir "install.ps1"
        Invoke-WebRequest -UseBasicParsing -Uri "$ReleaseUrl/install.ps1" -OutFile $installScript
    }
    $names = @("KVIST_INSTALL_SCRIPT", "KVIST_INSTALL_CACHE", "KVIST_NODE_HOME", "KVIST_NODE")
    # `iex` runs in the person's own session, so what is set here must not outlive the install.
    $saved = @{}
    foreach ($name in $names) { $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process") }
    try {
        $env:KVIST_INSTALL_SCRIPT = $installScript
        $env:KVIST_INSTALL_CACHE = $WorkDir
        $env:KVIST_NODE_HOME = $script:NodeHome
        $env:KVIST_NODE = $node
        & $core --installer
    } finally {
        foreach ($name in $names) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
    }
}

# One component: announces it, runs it, reports it. A failure inside a component is a warning of its
# own, so the others still run.
$Failed = @()
function Invoke-Component([string]$Id, [scriptblock]$Action) {
    Send-Event "component" @{ id = $Id; status = "start" }
    try {
        & $Action
        Send-Event "component" @{ id = $Id; status = "done" }
    } catch {
        $script:Failed += $Id
        Write-Warn "$Id was not installed: $($_.Exception.Message)"
        Send-Event "component" @{ id = $Id; status = "failed" }
    }
}

try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw "The release binary is built for 64-bit Windows." }
    if ($Plan) {
        Write-Plan
        return
    }
    # A short name: Windows PowerShell 5.1 cannot open paths over 260 characters.
    $WorkDir = Join-Path ([IO.Path]::GetTempPath()) ("kvist-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $WorkDir | Out-Null

    if (-not $OnlyGiven -and -not $From -and -not $env:KVIST_INSTALL_PLAIN -and -not $Events -and (Test-Terminal)) {
        Start-InstallerScreen
        return
    }

    # Every selected asset is fetched before anything is installed, so a missing one stops the run with
    # the machine untouched. Each component announces itself as it is fetched, so the screen shows what
    # is being waited for.
    $binary = $null; $jetBrainsZip = $null; $vsix = $null; $appBinary = $null
    $nodeReady = $false; $ffmpegReady = $false
    if (Test-Wanted "cli") {
        Send-Event "component" @{ id = "cli"; status = "start" }
        $binary = Get-Asset $BinaryAsset
    }
    if (Test-Wanted "node") {
        Send-Event "component" @{ id = "node"; status = "start" }
        Initialize-Node
        $nodeReady = $true
    }
    if (Test-Wanted "ffmpeg") {
        Send-Event "component" @{ id = "ffmpeg"; status = "start" }
        $ffmpegReady = Initialize-Ffmpeg
        if (-not $ffmpegReady) { Send-Event "component" @{ id = "ffmpeg"; status = "failed" } }
    }
    if (Test-Wanted "app") {
        Send-Event "component" @{ id = "app"; status = "start" }
        $appBinary = Get-OptionalAsset $AppAsset
        if (-not $appBinary) { Send-Event "component" @{ id = "app"; status = "failed" } }
    }
    # An IDE plugin is fetched only when there is an IDE to take it.
    if (Test-Wanted "jetbrains") {
        Send-Event "component" @{ id = "jetbrains"; status = "start" }
        if (Find-JetBrainsIdes) { $jetBrainsZip = Get-Asset $JetBrainsAsset }
    }
    if (Test-Wanted "vscode") {
        Send-Event "component" @{ id = "vscode"; status = "start" }
        if (Find-VsCodeEditors) { $vsix = Get-Asset $VsCodeAsset }
    }

    if ($binary) { Invoke-Component "cli" { Install-Cli $binary } }
    if ($nodeReady) { Invoke-Component "node" { Install-Node } }
    if ($ffmpegReady) { Invoke-Component "ffmpeg" { Install-Ffmpeg } }
    if ($appBinary) { Invoke-Component "app" { Install-App $appBinary } }
    if (Test-Wanted "jetbrains") { Invoke-Component "jetbrains" { Install-JetBrains $jetBrainsZip } }
    if (Test-Wanted "vscode") { Invoke-Component "vscode" { Install-VsCode $vsix } }
    if (Test-Wanted "path") { Invoke-Component "path" { Install-Path } }

    if ($Events) {
        Send-Event "done"
        return
    }
    if ($Failed.Count -gt 0) { Write-Warn "not installed: $($Failed -join ', ')" }
    if (Test-OnUserPath $BinDir) {
        Write-Host "`nDone. Run kvist in any directory to start the agent there (in a new terminal if this one was open before)."
    } else {
        Write-Host "`nDone. $BinDir is not on your PATH. Run this installer again with the PATH item ticked, or add that folder in Settings > Environment Variables, then open a new terminal and run kvist."
    }
} catch {
    if (-not $Events) { throw }
    Send-Event "error" @{ message = $_.Exception.Message }
    exit 1
} finally {
    if ($WorkDir) { Remove-Item -LiteralPath $WorkDir -Recurse -Force -ErrorAction SilentlyContinue }
}
