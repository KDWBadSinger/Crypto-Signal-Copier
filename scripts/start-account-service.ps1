param([switch]$Bootstrap)
$ErrorActionPreference = 'Stop'
$pythonPath = Join-Path $PSScriptRoot '..\backend\.venv\Scripts\python.exe'
$servicePath = Join-Path $PSScriptRoot '..\backend\control_plane.py'
$dataPath = Join-Path $PSScriptRoot '..\local-services\accounts'
if ($Bootstrap) { & $pythonPath $servicePath --data-dir $dataPath --bootstrap }
else { & $pythonPath $servicePath --data-dir $dataPath }
