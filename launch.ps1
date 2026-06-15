# Windows launcher for the Teamup Dispatch Map.
# Double-click launch.bat (which calls this). First run creates the venv and
# installs deps; demo mode unless .env has a TEAMUP_API_KEY. Opens the browser
# once the server is up; close this window (or Ctrl+C) to stop the server.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

Write-Host "================================================"
Write-Host "  Teamup Dispatch Map"
Write-Host "  $PSScriptRoot"
Write-Host "================================================"

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

try {
    # --- first-run bootstrap: venv + dependencies ---
    if (-not (Test-Path $py)) {
        Write-Host "[setup] first run: creating virtualenv + installing dependencies..."
        $launcher = "python"
        if (Get-Command py -ErrorAction SilentlyContinue) { $launcher = "py" }
        & $launcher -m venv .venv
        if (-not (Test-Path $py)) {
            throw "Could not create the virtualenv. Install Python 3 (and tick 'Add to PATH'): https://www.python.org/downloads/"
        }
        & $py -m pip install --upgrade pip | Out-Null
        & $py -m pip install -r requirements.txt
        if ($LASTEXITCODE -ne 0) { throw "Installing dependencies failed (see messages above)." }
    }

    # --- demo vs live ---
    $demo = $true
    if (Test-Path ".env") {
        if (Select-String -Path ".env" -Pattern '^\s*TEAMUP_API_KEY\s*=\s*\S' -Quiet) { $demo = $false }
    }
    if ($demo) {
        Write-Host "[mode] DEMO - no Teamup key in .env, showing sample data"
        $env:DEMO = "1"
        $env:DB_PATH = "demo.db"   # keep demo data out of the live DB
    } else {
        Write-Host "[mode] LIVE - using credentials from .env"
    }

    $port = if ($env:PORT) { $env:PORT } else { "8000" }
    $url = "http://127.0.0.1:$port"

    # open the browser a few seconds after the server starts
    Start-Job -ScriptBlock { param($u) Start-Sleep -Seconds 4; Start-Process $u } -ArgumentList $url | Out-Null

    Write-Host ""
    Write-Host ">>> Map will open at $url"
    Write-Host ">>> Close this window (or press Ctrl+C) to stop the server."
    Write-Host ""
    & $py -m uvicorn app.main:app --host 127.0.0.1 --port $port
}
catch {
    Write-Host ""
    Write-Host ("ERROR: " + $_.Exception.Message) -ForegroundColor Red
}
finally {
    Write-Host ""
    Read-Host "Press Enter to close"
}
