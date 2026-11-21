# Asks the terminal it runs in the questions Kvist Glass and Voice ask before they draw, and prints
# what came back. Run it in the terminal you want to check (PowerShell 5.1+ or pwsh):
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\probe_kitty_graphics.ps1
#
# It exists because on Windows every byte between a program and the terminal passes through ConPTY,
# and whether ConPTY forwards the kitty graphics sequence (an APC string) cannot be known from here.
# "graphics: YES" means a graphical Kvist interface can run in this terminal; "NO" with a device
# attributes reply means the terminal (or ConPTY) swallowed the graphics query.
$ErrorActionPreference = "Stop"

if ([Console]::IsInputRedirected -or [Console]::IsOutputRedirected) {
    throw "Run this in a terminal window, not through a pipe: it has to read the terminal's answer."
}

$esc = [string][char]27
$st = $esc + "\"

# Writes a query and collects the reply until the device attributes answer (which every terminal
# gives) arrives or two seconds pass.
function Read-Reply([string]$Query) {
    while ([Console]::KeyAvailable) { [void][Console]::ReadKey($true) }
    [Console]::Out.Write($Query)
    [Console]::Out.Flush()
    $reply = New-Object Text.StringBuilder
    $deadline = (Get-Date).AddSeconds(2)
    while ((Get-Date) -lt $deadline) {
        if (-not [Console]::KeyAvailable) {
            Start-Sleep -Milliseconds 10
            continue
        }
        [void]$reply.Append([Console]::ReadKey($true).KeyChar)
        if ($reply.ToString() -match "\x1b\[\?[0-9;]*c") { break }
    }
    return $reply.ToString()
}

function Show([string]$Text) { return $Text.Replace($esc, "^[") }

# The same first query kvist-glass sends (frontends/tui/glass/src/probe.rs), then Device Attributes.
$graphicsReply = Read-Reply ($esc + "_Gi=31,s=1,v=1,a=q,t=d,f=24;AAAA" + $st + $esc + "[c")
# The window's size in pixels: Glass needs the cell size to place pictures. Text area first, cell size second.
$pixelReply = Read-Reply ($esc + "[14t" + $esc + "[c")
$cellReply = Read-Reply ($esc + "[16t" + $esc + "[c")

$graphics = $graphicsReply.Contains($esc + "_Gi=31;OK")
$attributes = $graphicsReply -match "\x1b\[\?[0-9;]*c"
$area = [regex]::Match($pixelReply, "\x1b\[4;(\d+);(\d+)t")
$cell = [regex]::Match($cellReply, "\x1b\[6;(\d+);(\d+)t")

Write-Host ""
Write-Host ("terminal: TERM_PROGRAM={0} WT_SESSION={1} TERM={2}" -f $env:TERM_PROGRAM, $(if ($env:WT_SESSION) { "set" } else { "-" }), $env:TERM)
Write-Host ("graphics: {0}" -f $(if ($graphics) { "YES (the kitty graphics query was answered)" } else { "NO" }))
Write-Host ("device attributes answered: {0}" -f $attributes)
Write-Host ("text area in pixels: {0}" -f $(if ($area.Success) { "$($area.Groups[2].Value) x $($area.Groups[1].Value)" } else { "no answer" }))
Write-Host ("cell size in pixels: {0}" -f $(if ($cell.Success) { "$($cell.Groups[2].Value) x $($cell.Groups[1].Value)" } else { "no answer" }))
Write-Host ""
Write-Host ("raw graphics reply: {0}" -f (Show $graphicsReply))
Write-Host ("raw size replies:   {0} | {1}" -f (Show $pixelReply), (Show $cellReply))
