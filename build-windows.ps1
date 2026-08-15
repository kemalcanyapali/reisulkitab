$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv)) {
    py -3.12 -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        py -3.11 -m venv .venv
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Python 3.11 or 3.12 is required."
    }
}
& .\.venv\Scripts\python.exe -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "Could not upgrade pip." }
& .\.venv\Scripts\python.exe -m pip install -r requirements-windows.txt
if ($LASTEXITCODE -ne 0) { throw "Could not install Windows dependencies." }
Remove-Item -Path @("Dikte.spec", "Reisulkuttab.spec") `
    -ErrorAction SilentlyContinue
& .\.venv\Scripts\pyi-makespec.exe `
    --onedir `
    --console `
    --name Reisulkuttab `
    --icon assets\reisulkuttab.ico `
    --version-file version-info.txt `
    --add-data "assets;assets" `
    --collect-all soundcard `
    reisulkuttab.py
if ($LASTEXITCODE -ne 0) { throw "Could not generate PyInstaller spec." }

$spec = Get-Content Reisulkuttab.spec -Raw
$spec = $spec.Replace(
    "    console=True,",
    "    console=True,`n    hide_console='hide-early',")
[IO.File]::WriteAllText((Join-Path $PSScriptRoot "Reisulkuttab.spec"), $spec)

& .\.venv\Scripts\pyinstaller.exe --noconfirm --clean Reisulkuttab.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
Copy-Item -Force -Path @("LICENSE", "README.md") -Destination "dist\Reisulkuttab"

Write-Host "Built $PSScriptRoot\dist\Reisulkuttab\Reisulkuttab.exe"
