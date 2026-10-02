param([string]$AccountServer='http://127.0.0.1:9000')
$ErrorActionPreference = 'Stop'
$env:COPIER_REQUIRE_LICENSE='true'
$env:COPIER_ACCOUNT_SERVER=$AccountServer
& "$PSScriptRoot\..\release\CryptoSignalCopier\CryptoSignalCopier.exe"
