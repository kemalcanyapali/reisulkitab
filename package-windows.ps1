$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$package = Join-Path $PSScriptRoot "dist\Reisulkuttab"
$exe = Join-Path $package "Reisulkuttab.exe"
if (-not (Test-Path $exe)) { throw "Run build-windows.ps1 first." }
$version = & "$PSScriptRoot\.venv\Scripts\python.exe" -c "import brand; print(brand.VERSION)"
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Cannot read a stable application version."
}
if ((Get-Item $exe).VersionInfo.ProductVersion -ne $version) {
    throw "Executable version differs from brand.VERSION; rebuild with matching version-info.txt."
}
$archive = Join-Path $PSScriptRoot "dist\Reisulkuttab-windows-x64.zip"
& "$PSScriptRoot\.venv\Scripts\python.exe" -m zipfile -c $archive $package
if ($LASTEXITCODE -ne 0) { throw "Could not create the release archive." }
$hash = (Get-FileHash $archive -Algorithm SHA256).Hash.ToLowerInvariant()
[System.IO.File]::WriteAllText(
    (Join-Path $PSScriptRoot "dist\SHA256SUMS"),
    "$hash  Reisulkuttab-windows-x64.zip`n",
    [System.Text.UTF8Encoding]::new($false)
)
Write-Output "Packaged Reisulkuttab $version; SHA256 $hash"
