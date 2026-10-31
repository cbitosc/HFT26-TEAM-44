# WildWatch launcher.   powershell -ExecutionPolicy Bypass -File src\run.ps1 dashboard
#   dashboard                  start the server on http://localhost:8000 and open the browser
#   test                       run the pytest suite (API, upload, persistence, Playwright)
#   import <folder> [camera]   copy photos into src\data\incoming so the folder watcher processes them
param([Parameter(Position = 0)][string]$Command = "dashboard", [Parameter(Position = 1, ValueFromRemainingArguments = $true)]$Rest)
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $src
$py = "D:\hackathon\venv\Scripts\python.exe"        # the venv with torch + megadetector + open_clip
if (-not (Test-Path $py)) { $py = "python" }

& $py -c "import fastapi, uvicorn, multipart, yaml, PIL" *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing dashboard packages (once)..."
    $env:TEMP = "D:\tmp"; $env:TMP = "D:\tmp"; New-Item -ItemType Directory -Force D:\tmp | Out-Null
    & $py -m pip install fastapi uvicorn python-multipart pyyaml pillow --no-cache-dir
}

switch ($Command) {
    "dashboard" {
        $port = if ($env:WILDWATCH_PORT) { $env:WILDWATCH_PORT } else { "8000" }
        $env:WILDWATCH_PORT = $port
        Start-Job -ScriptBlock { param($p) Start-Sleep -Seconds 4; Start-Process "http://localhost:$p/" } -ArgumentList $port | Out-Null
        Write-Host "WildWatch on http://localhost:$port  (Ctrl+C to stop). First start loads the models, about 30 s."
        & $py backend\server.py
    }
    "test" {
        & $py -m pip install pytest httpx playwright --no-cache-dir | Out-Null
        & $py -m playwright install chromium
        Set-Location (Split-Path -Parent $src)
        & $py -m pytest tests -v
    }
    "import" { & $py backend\import_folder.py @Rest }
    default { Write-Host "Usage: run.ps1 dashboard | test | import <folder> [camera]" }
}
