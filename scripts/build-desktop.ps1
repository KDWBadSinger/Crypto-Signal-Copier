param(
    [string]$Python = "$PSScriptRoot\..\backend\.venv\Scripts\python.exe",
    [string]$Node = "node",
    [string]$OutputDirectory = "release"
)
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path "$PSScriptRoot\..").Path
Push-Location $projectRoot
try {
    Push-Location "$projectRoot\prototype"
    try {
        & $Node node_modules/vite/bin/vite.js build --logLevel warn
        if ($LASTEXITCODE -ne 0) { throw "Frontend build failed" }
        & $Node scripts/prepare-sites-build.mjs
        if ($LASTEXITCODE -ne 0) { throw "Frontend preparation failed" }
    } finally { Pop-Location }
    $env:PYINSTALLER_CONFIG_DIR = "$projectRoot\build\pyinstaller-cache"
    & $Python -m PyInstaller --noconfirm --clean --windowed --onedir `
        --name CryptoSignalCopier --distpath $OutputDirectory --workpath build/desktop `
        --specpath build --paths "$projectRoot\backend" --collect-all webview `
        --hidden-import uvicorn.logging --hidden-import uvicorn.loops.auto `
        --hidden-import uvicorn.protocols.http.auto --hidden-import uvicorn.protocols.websockets.auto `
        --hidden-import uvicorn.lifespan.on `
        --add-data "$projectRoot\prototype\dist\client;frontend" "$projectRoot\backend\desktop.py"
    if ($LASTEXITCODE -ne 0) { throw "EXE build failed" }
    Write-Host "Ready: $OutputDirectory\CryptoSignalCopier\CryptoSignalCopier.exe"
} finally { Pop-Location }
