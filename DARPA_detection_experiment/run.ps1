param(
    [switch]$Download,
    [string]$Python = "python"
)
$ErrorActionPreference = 'Stop'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONHASHSEED = '0'
$env:PYTHONDONTWRITEBYTECODE = '1'
if ($Download) {
    & $Python "$PSScriptRoot/run_experiment.py" --download
} else {
    & $Python "$PSScriptRoot/run_experiment.py"
}
if ($LASTEXITCODE -ne 0) { throw 'Эксперимент завершился с ошибкой' }
