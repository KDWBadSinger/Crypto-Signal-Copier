param(
    [string]$PackageDirectory = "$PSScriptRoot\..\release\CryptoSignalCopier"
)
$ErrorActionPreference = 'Stop'
$packagePath = (Resolve-Path -LiteralPath $PackageDirectory).Path
if (-not (Test-Path -LiteralPath (Join-Path $packagePath 'CryptoSignalCopier.exe'))) { throw '完整桌面包不存在' }
$installPath = Join-Path $env:LOCALAPPDATA 'Programs\CryptoSignalCopier'
$targetRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
if (-not [IO.Path]::GetFullPath($installPath).StartsWith($targetRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw '安装目录校验失败' }
if (Get-Process -Name CryptoSignalCopier -ErrorAction SilentlyContinue) { throw '请先关闭桌面应用再安装；不会强行中断模拟' }
if (Test-Path -LiteralPath $installPath) {
    $backupPath = $installPath + '-backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
    Move-Item -LiteralPath $installPath -Destination $backupPath
}
New-Item -ItemType Directory -Path $installPath -Force | Out-Null
Get-ChildItem -LiteralPath $packagePath | Copy-Item -Destination $installPath -Recurse
$shortcutPath = Join-Path ([Environment]::GetFolderPath('Programs')) 'Crypto Signal Copier.lnk'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = Join-Path $installPath 'CryptoSignalCopier.exe'
$shortcut.WorkingDirectory = $installPath
$shortcut.Save()
Write-Output "安装完成：$installPath。开始菜单已添加快捷方式，用户数据未修改。"
