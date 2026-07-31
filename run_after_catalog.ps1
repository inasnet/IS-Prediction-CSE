param(
    [Parameter(Mandatory = $true)]
    [int]$CatalogProcessId
)

$ErrorActionPreference = "Stop"
$projectDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$logPath = Join-Path $projectDirectory "logs\coverage_audit.log"

Wait-Process -Id $CatalogProcessId -ErrorAction SilentlyContinue
Set-Location -LiteralPath $projectDirectory

"$(Get-Date -Format s) | Reconstruction terminée, lancement de l'audit." |
    Out-File -LiteralPath $logPath -Encoding utf8

& python "05_audit_coverage.py" 2>&1 |
    Tee-Object -LiteralPath $logPath -Append

"$(Get-Date -Format s) | Audit terminé avec le code $LASTEXITCODE." |
    Out-File -LiteralPath $logPath -Encoding utf8 -Append

exit $LASTEXITCODE
