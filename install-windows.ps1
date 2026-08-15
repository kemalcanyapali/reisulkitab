$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$PackageDir = Join-Path $PSScriptRoot "dist\Reisulkuttab"
$Executable = Join-Path $PackageDir "Reisulkuttab.exe"
if (-not (Test-Path $Executable)) {
    throw "Build the package first with .\build-windows.ps1."
}

$DisplayName = "Reis" + [char]0x00FC + "lk" + [char]0x00FC + "ttab"
$MojibakeName = [Text.Encoding]::Default.GetString(
    [Text.Encoding]::UTF8.GetBytes($DisplayName))
$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\reisulkuttab"
$TransitionalInstallDir = Join-Path $env:LOCALAPPDATA ("Programs\" + $DisplayName)
$MojibakeInstallDir = Join-Path $env:LOCALAPPDATA ("Programs\" + $MojibakeName)
$OldInstallDir = Join-Path $env:LOCALAPPDATA "Programs\Dikte"
$StartMenuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$ShortcutPath = Join-Path $StartMenuDir ($DisplayName + ".lnk")
$StageDir = $InstallDir + ".installing"
$BackupDir = $InstallDir + ".previous"

foreach ($App in @(
    (Join-Path $InstallDir "Reisulkuttab.exe"),
    (Join-Path $TransitionalInstallDir "Reisulkuttab.exe"),
    (Join-Path $MojibakeInstallDir "Reisulkuttab.exe"),
    (Join-Path $OldInstallDir "Dikte.exe")
)) {
    if (Test-Path $App) {
        & $App quit 2>$null | Out-Null
    }
}
Start-Sleep -Milliseconds 500
$Processes = @(Get-Process Reisulkuttab, Dikte -ErrorAction SilentlyContinue)
if ($Processes.Count -gt 0) {
    $Processes | Wait-Process -Timeout 5 -ErrorAction SilentlyContinue
    $Processes = @(Get-Process Reisulkuttab, Dikte -ErrorAction SilentlyContinue)
}
if ($Processes.Count -gt 0) {
    $Processes | Stop-Process -Force
    $Processes | Wait-Process -Timeout 10 -ErrorAction SilentlyContinue
}

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $StageDir, $BackupDir
New-Item -ItemType Directory -Force $StageDir | Out-Null
Copy-Item -Recurse -Force (Join-Path $PackageDir "*") $StageDir
if (-not (Test-Path (Join-Path $StageDir "Reisulkuttab.exe"))) {
    throw "The staged package is incomplete."
}
if (Test-Path $InstallDir) {
    Move-Item $InstallDir $BackupDir
}
try {
    Move-Item $StageDir $InstallDir
} catch {
    if ((Test-Path $BackupDir) -and -not (Test-Path $InstallDir)) {
        Move-Item $BackupDir $InstallDir
    }
    throw
}
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue `
    $BackupDir, $TransitionalInstallDir, $MojibakeInstallDir, $OldInstallDir
New-Item -ItemType Directory -Force $StartMenuDir | Out-Null
$Shell = New-Object -ComObject WScript.Shell
$OwnedTargets = @(
    (Join-Path $InstallDir "Reisulkuttab.exe"),
    (Join-Path $TransitionalInstallDir "Reisulkuttab.exe"),
    (Join-Path $MojibakeInstallDir "Reisulkuttab.exe"),
    (Join-Path $OldInstallDir "Dikte.exe")
)
Get-ChildItem -LiteralPath $StartMenuDir -Filter "*.lnk" -File | ForEach-Object {
    try {
        $ExistingShortcut = $Shell.CreateShortcut($_.FullName)
        if ($OwnedTargets -contains $ExistingShortcut.TargetPath) {
            Remove-Item -LiteralPath $_.FullName -Force
        }
    } catch {
        # Ignore unrelated shortcuts that the shell cannot read.
    }
}
$Shortcut = $Shell.CreateShortcut($ShortcutPath)
$Shortcut.TargetPath = Join-Path $InstallDir "Reisulkuttab.exe"
$Shortcut.WorkingDirectory = $InstallDir
$Shortcut.IconLocation = "$($Shortcut.TargetPath),0"
$Shortcut.Description = "$DisplayName voice workspace"
$Shortcut.Save()

Start-Process -FilePath $Shortcut.TargetPath -ArgumentList "--gui" `
    -WorkingDirectory $InstallDir
Write-Host "Installed $DisplayName to $InstallDir"
Write-Host "Start menu shortcut: $ShortcutPath"
