# One-click fix-up for the per-user EchoPortal install.
#
# Run allow-firewall.cmd (right-click -> Run as administrator) instead of this file directly.
# It does four things:
#   1. uninstalls the broken C:\Program Files\EchoPortal copy
#   2. replaces the old firewall rules with port rules for the new copy
#   3. repoints the "start when I sign in" shortcut at the per-user copy
#   4. starts the agent if it is not already running
#
# Output is also written to %TEMP%\echoportal-fixup.log so allow-firewall.cmd can show it.
$ErrorActionPreference = 'Continue'

$log = Join-Path $env:TEMP 'echoportal-fixup.log'
Start-Transcript -Path $log -Force | Out-Null

$app  = Join-Path $env:LOCALAPPDATA 'Programs\EchoPortal'
$exe  = Join-Path $app 'EchoPortalAgent.exe'
$lnk  = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup\EchoPortal Agent.lnk'

function Say($text) { Write-Host $text }

Say "EchoPortal fix-up"
Say ""

# --- 1. remove the broken Program Files copy ---------------------------------------------------
# Do this before touching the firewall: the old uninstaller deletes rules with the same names.
$old = Join-Path $env:ProgramFiles 'EchoPortal'
$unins = Join-Path $old 'unins000.exe'
if (Test-Path $unins) {
    Say "  removing old copy in $old ..."
    Get-Process EchoPortalAgent -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -like "$old*" } | Stop-Process -Force
    Start-Sleep -Seconds 1
    $u = Start-Process -FilePath $unins -ArgumentList '/SILENT', '/NORESTART' -Wait -PassThru
    Say "  old uninstaller exit=$($u.ExitCode)"
    Start-Sleep -Seconds 2
    # Inno can leave user files behind (for example config.json with the hub token).
    # Do not recursively delete them: they are user data, not installer output.
    if (Test-Path $old) { Say "  note: $old still has leftover files (left untouched)" }
    else { Say "  old folder removed" }
} else {
    Say "  no old copy to remove"
}

# --- 2. firewall ------------------------------------------------------------------------------
# The installer's rules were tied to the old .exe path. Port rules survive moving the install,
# and stay limited to private networks.
foreach ($name in @('EchoPortal agent (hub 8765)', 'EchoPortal agent (media 8766)')) {
    & "$env:SystemRoot\System32\netsh.exe" advfirewall firewall delete rule name="$name" | Out-Null
}
$rules = @(
    @{ Name = 'EchoPortal agent (hub 8765)';   Port = 8765 },
    @{ Name = 'EchoPortal agent (media 8766)'; Port = 8766 }
)
foreach ($r in $rules) {
    & "$env:SystemRoot\System32\netsh.exe" advfirewall firewall add rule `
        name="$($r.Name)" dir=in action=allow protocol=TCP localport=$($r.Port) profile=private | Out-Null
    if ($LASTEXITCODE -eq 0) { Say "  firewall: allowed TCP $($r.Port) on private networks" }
    else { Say "  firewall: FAILED for TCP $($r.Port) (exit $LASTEXITCODE)" }
}

# --- 3. startup shortcut (the uninstaller above deletes it) -----------------------------------
if (Test-Path $exe) {
    $ws = New-Object -ComObject WScript.Shell
    $s = $ws.CreateShortcut($lnk)
    $s.TargetPath = $exe
    $s.WorkingDirectory = $app
    $s.Description = 'EchoPortal agent'
    $s.IconLocation = "$exe,0"
    $s.Save()
    Say "  startup shortcut -> $exe"
} else {
    Say "  WARNING: $exe not found; skipping startup shortcut"
}

# --- 4. start the agent -----------------------------------------------------------------------
$running = Get-Process EchoPortalAgent -ErrorAction SilentlyContinue
if (-not $running -and (Test-Path $exe)) {
    Start-Process -FilePath $exe -WorkingDirectory $app
    Say "  started the agent"
} elseif ($running) {
    Say "  agent already running (pid $($running[0].Id))"
}

Say ""
Say "Done. The Spot should now reach ws://<this-PC-ip>:8765"
Say "Current LAN address:"
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    ForEach-Object { Say "  $($_.IPAddress)  ($($_.InterfaceAlias))" }

Stop-Transcript | Out-Null
