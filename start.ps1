param([int]$Port = 9127)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$layaDir = if ($env:SKILL_ROUTER_LAYA_DIR) { $env:SKILL_ROUTER_LAYA_DIR } else { Join-Path (Split-Path $PSScriptRoot) 'laya-runtime' }
$layaPython = Join-Path $layaDir '.venv\Scripts\python.exe'
$layaService = Join-Path $layaDir 'laya_service.py'
try {
    Invoke-WebRequest -Uri 'http://127.0.0.1:9130' -UseBasicParsing -TimeoutSec 1 | Out-Null
} catch {
    if ((Test-Path -LiteralPath $layaPython) -and (Test-Path -LiteralPath $layaService)) {
        Start-Process -FilePath $layaPython -ArgumentList @($layaService) -WorkingDirectory (Split-Path $layaService) -WindowStyle Hidden
    }
}
$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
$pyCommand = Get-Command py -ErrorAction SilentlyContinue

if ($pythonCommand) {
    & $pythonCommand.Source .\app.py serve --port $Port --open
} elseif ($pyCommand) {
    & $pyCommand.Source -3 .\app.py serve --port $Port --open
} else {
    throw 'Python 3 was not found. Install Python 3.10+ and run this script again.'
}
