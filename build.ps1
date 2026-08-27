$ErrorActionPreference = "Stop"
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
}
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
.\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean leadharbor.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
if ((Get-Item ".\dist\KCC-LeadHarbor.exe").Length -lt 1MB) {
    throw "Built executable is unexpectedly small."
}
Write-Host "Build complete: dist\KCC-LeadHarbor.exe"
