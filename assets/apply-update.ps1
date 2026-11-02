# Applies a verified update package: waits for the old version to exit, swaps
# the installation folders in two atomic renames, starts the new version and
# keeps the old one as a backup until the new one answers.
#
# Never kills anything, and never touches anything outside the installation
# folder and the staging folder it was given, plus its own copy: not the
# user's recordings, settings, models or anything else. Every path arrives as
# an argument and is checked against what the updater would have built before
# anything — including cleanup — happens, so a helper run by hand on the
# wrong arguments touches nothing. The inherited environment is passed on
# untouched, so the restarted application computes the same IPC server name
# the readiness probe connects to.
#
# Kept to plain ASCII on purpose: Windows PowerShell 5.1 reads a script
# without a byte-order mark as ANSI, and the name it would mangle is ours.

param(
    [Parameter(Mandatory = $true)][int]$AppPid,
    [Parameter(Mandatory = $true)][string]$StagedDir,
    [Parameter(Mandatory = $true)][string]$InstallDir,
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$InstallDir = [IO.Path]::GetFullPath($InstallDir)
$StagedDir = [IO.Path]::GetFullPath($StagedDir)
$InstallParent = Split-Path -Parent $InstallDir
$StagedPackage = Join-Path $StagedDir "Reisulkuttab"
$BackupDir = $InstallDir + ".previous"
$ExpectedStaging = Join-Path $InstallParent "reisulkuttab.update-$AppPid"
$ExpectedInstall = Join-Path $env:LOCALAPPDATA "Programs\reisulkuttab"
$LogFile = Join-Path $InstallParent "reisulkuttab-update.log"
$ExeName = "Reisulkuttab.exe"
$ExitWaitSeconds = 120
$ReadyTries = 20
$script:Cleaned = $false
$script:PathsValidated = $false

function Log([string]$Text) {
    if (-not $script:PathsValidated) { return }
    try {
        "$(Get-Date -Format s)  $Text" |
            Add-Content -LiteralPath $LogFile -Encoding UTF8
    } catch {
        # The log is a courtesy; the message box carries the failure.
    }
}

function Explain([string]$Text) {
    Log $Text
    try {
        Add-Type -AssemblyName System.Windows.Forms
        $Message = $Text
        if ($script:PathsValidated) { $Message += "`n`nDetails are in $LogFile" }
        [System.Windows.Forms.MessageBox]::Show(
            $Message,
            "Reisulkuttab update",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
    } catch {
        # A session with no window station still gets the log entry.
    }
}

function CleanUp {
    # Exact owned paths only: never a directory listing, never a neighbour.
    if ($script:Cleaned) { return }
    $script:Cleaned = $true
    if ($StagedDir -and (Test-Path -LiteralPath $StagedDir)) {
        Remove-Item -Recurse -Force -LiteralPath $StagedDir `
            -ErrorAction SilentlyContinue
    }
    $TempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
    if ($PSScriptRoot -and
        (Split-Path -Parent $PSScriptRoot) -eq $TempRoot -and
        (Split-Path -Leaf $PSScriptRoot) -like "reisulkuttab-helper-*" -and
        (Split-Path -Leaf $PSCommandPath) -eq "apply-update.ps1" -and
        -not (Test-Reparse $PSScriptRoot)) {
        Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue
        try { [IO.Directory]::Delete($PSScriptRoot, $false) } catch {}
    }
}

function Reject([string]$Text) {
    # A door that did not hold: nothing has been touched, so nothing is.
    Explain $Text
    exit 1
}

function Fail([string]$Text) {
    Explain $Text
    CleanUp
    exit 1
}

function Test-Reparse([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    return (((Get-Item -LiteralPath $Path -Force).Attributes -band
             [IO.FileAttributes]::ReparsePoint) -ne 0)
}

function Restore-Backup {
    Move-Item -LiteralPath $BackupDir -Destination $InstallDir
    Log "The previous version was put back."
}

function Start-App([string]$Dir) {
    Start-Process -FilePath (Join-Path $Dir $ExeName) -ArgumentList "--gui" `
        -WorkingDirectory $Dir -WindowStyle Hidden
}

function Restart-IfWhole([string]$Why) {
    # Restart the old version only when it is whole again at the installation,
    # never from wherever a failed swap left it.
    if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $ExeName))) {
        return
    }
    try {
        Start-App $InstallDir
        Log "$Why The previous version is running again."
    } catch {
        Log "$Why Could not restart the previous version: $_"
    }
}

function Test-Ready {
    # A live process proves nothing about a working one. The new version is
    # called the way the command line is, and only counts as up when it
    # answers with the release that was just installed.
    $Json = ""
    try {
        $Json = (& (Join-Path $InstallDir $ExeName) --json status `
            2>$null) -join "`n"
    } catch {
        return $false
    }
    if ($LASTEXITCODE -ne 0 -or -not $Json) {
        return $false
    }
    try {
        $Status = $Json | ConvertFrom-Json
    } catch {
        return $false
    }
    if (-not $Status) {
        return $false
    }
    if ($Version -and [string]$Status.version -ne $Version) {
        Log "Answered with version '$($Status.version)', expected '$Version'."
        return $false
    }
    return $true
}

# ---- the doors: nothing is touched, not even cleaned up, until these hold --

if (-not $env:LOCALAPPDATA -or $InstallDir -ne
    [IO.Path]::GetFullPath($ExpectedInstall)) {
    Reject "Refusing to touch a folder that is not the installation this updater owns: $InstallDir"
}
if ($StagedDir -ne $ExpectedStaging) {
    Reject "The staging folder is not this update's own ($ExpectedStaging); refusing to touch anything."
}
$Sep = [IO.Path]::DirectorySeparatorChar
foreach ($Pair in @(@($InstallDir, $StagedDir), @($InstallDir, $BackupDir),
                    @($StagedDir, $BackupDir))) {
    if ($Pair[0] -eq $Pair[1] -or
        $Pair[0].StartsWith($Pair[1] + $Sep) -or
        $Pair[1].StartsWith($Pair[0] + $Sep)) {
        Reject "The update paths overlap ($($Pair[0]), $($Pair[1])); refusing to touch anything."
    }
}
if ((Test-Reparse $InstallDir) -or (Test-Reparse $StagedDir) -or
    (Test-Reparse $InstallParent)) {
    Reject "A junction is in the update's way; refusing to touch anything."
}
if (-not (Test-Path -LiteralPath (Join-Path $InstallDir $ExeName))) {
    Reject "There is no installation at $InstallDir; nothing was changed."
}
if (-not (Test-Path -LiteralPath (Join-Path $StagedPackage $ExeName)) -or
    -not (Test-Path -LiteralPath (Join-Path $StagedPackage "_internal"))) {
    Reject "The staged update package at $StagedPackage is incomplete; leaving it alone."
}
if ([IO.Path]::GetPathRoot($StagedPackage) -ne
    [IO.Path]::GetPathRoot($InstallDir)) {
    Reject "The staged package is not on the installation's drive, so the swap would not be atomic."
}
$script:PathsValidated = $true

# ---- the swap --------------------------------------------------------------

Log "Waiting up to $ExitWaitSeconds s for process $AppPid to exit."
$Deadline = (Get-Date).AddSeconds($ExitWaitSeconds)
while (Get-Process -Id $AppPid -ErrorAction SilentlyContinue) {
    if ((Get-Date) -gt $Deadline) {
        Fail "The old version did not exit within $ExitWaitSeconds s. Nothing was changed; try the update again."
    }
    Start-Sleep -Milliseconds 500
}

if (Test-Path -LiteralPath $BackupDir) {
    # The updater refuses to get this far with a backup in the way; if one
    # appeared anyway, the old version below is still whole and starts again.
    Restart-IfWhole "A backup from an earlier update is in the way."
    Fail "A backup from an earlier update is in the way: $BackupDir. It was left alone; move that folder out of the way once you no longer need it, then update again."
}

Log "Moving the current version aside."
try {
    Move-Item -LiteralPath $InstallDir -Destination $BackupDir
} catch {
    Restart-IfWhole "Could not move the current version aside: $_"
    Fail "Could not move the current version aside: $_ Nothing was changed."
}

$NewExe = Join-Path $InstallDir $ExeName
try {
    Move-Item -LiteralPath $StagedPackage -Destination $InstallDir
    Log "The new version is in place."
} catch {
    Log "Could not move the new version in: $_"
    $Restored = $false
    try {
        Restore-Backup
        $Restored = $true
    } catch {
        Log "Could not restore the previous version: $_"
    }
    if ($Restored) {
        Restart-IfWhole "Could not move the new version in."
        Fail "Could not move the new version in. The previous version was put back; try the update again."
    }
    Fail "Could not move the new version in, and the previous version could not be restored. It is kept at $BackupDir; move that folder back to $InstallDir and start Reisulkuttab from the Start menu."
}

try {
    Start-App $InstallDir
    Log "The new version was started."
} catch {
    Log "The new version could not be started: $_"
    $Restored = $false
    try {
        Remove-Item -Recurse -Force -LiteralPath $InstallDir `
            -ErrorAction SilentlyContinue
        Restore-Backup
        $Restored = $true
    } catch {
        Log "Rollback did not complete: $_"
    }
    if ($Restored) {
        Restart-IfWhole "The new version could not be started."
        Fail "The new version could not be started. The previous version was put back; try the update later."
    }
    Fail "The new version could not be started, and the previous version could not be restored. It is kept at $BackupDir; move that folder back to $InstallDir and start Reisulkuttab from the Start menu."
}

# ---- the new version has to answer ----------------------------------------

$Ready = $false
for ($Try = 1; $Try -le $ReadyTries; $Try++) {
    Start-Sleep -Milliseconds 1500
    if (Test-Ready) {
        $Ready = $true
        break
    }
    Log "The new version has not answered yet ($Try/$ReadyTries)."
}

if ($Ready) {
    Log "The new version answered; removing the backup."
    Remove-Item -Recurse -Force -LiteralPath $BackupDir -ErrorAction SilentlyContinue
    CleanUp
    Log "Update applied."
    exit 0
}

# The swap succeeded but the new version never said it was up. It is left in
# place — killing it is not ours to do — and the backup stays so the user can
# go back. That is a failure: exit 1, no success claimed.
Explain "The new version was installed but did not report ready in time. It was left running, and the previous version is kept at $BackupDir."
CleanUp
exit 1
