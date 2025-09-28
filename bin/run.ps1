# Requires PowerShell 5+
# Quick Start launcher for the data scraper on Windows.
# - Starts Chrome with remote debugging
# - Creates a local venv and installs deps
# - Runs windowsautomation.py

$ErrorActionPreference = 'Stop'

$PORT = if ($env:CHROME_REMOTE_DEBUG_PORT) { $env:CHROME_REMOTE_DEBUG_PORT } else { '9222' }
$PROFILE_DIR = if ($env:CHROME_REMOTE_PROFILE_DIR) { $env:CHROME_REMOTE_PROFILE_DIR } else { Join-Path (Get-Location) ".chrome-remote-$PORT" }
$PY = if ($env:PYTHON) { $env:PYTHON } else { 'python' }

Write-Host "Launching Chrome with remote debugging on port $PORT..."

# Try to start Chrome with remote debugging. chrome.exe should be on PATH, otherwise try common locations.
$chromeArgs = "--remote-debugging-port=$PORT --user-data-dir=$PROFILE_DIR"
$started = $false
try {
  Start-Process -FilePath "chrome.exe" -ArgumentList $chromeArgs -WindowStyle Minimized -ErrorAction Stop | Out-Null
  $started = $true
} catch {}
if (-not $started) {
  $common = @(
    "$Env:ProgramFiles\\Google\\Chrome\\Application\\chrome.exe",
    "$Env:ProgramFiles(x86)\\Google\\Chrome\\Application\\chrome.exe"
  )
  foreach ($path in $common) {
    if (Test-Path $path) {
      try {
        Start-Process -FilePath $path -ArgumentList $chromeArgs -WindowStyle Minimized -ErrorAction Stop | Out-Null
        $started = $true
        break
      } catch {}
    }
  }
}
if (-not $started) {
  Write-Warning "Could not auto-start Chrome. Please start it manually: chrome.exe $chromeArgs"
}

Write-Host "Waiting for Chrome DevTools on http://127.0.0.1:$PORT ..."
$deadline = (Get-Date).AddSeconds(15)
while ((Get-Date) -lt $deadline) {
  try {
    $resp = Invoke-WebRequest -Uri "http://127.0.0.1:$PORT/json/version" -UseBasicParsing -TimeoutSec 1
    if ($resp.StatusCode -ge 200 -and $resp.StatusCode -lt 300) { break }
  } catch {}
  Start-Sleep -Milliseconds 500
}

if (-not (Test-Path ".venv")) {
  Write-Host "Creating virtual environment (.venv)"
  & $PY -m venv .venv
}

$venvActivate = Join-Path ".venv" "Scripts/Activate.ps1"
if (Test-Path $venvActivate) {
  . $venvActivate
} else {
  Write-Warning "Could not find venv activation script at $venvActivate"
}

pip install -r requirements.txt

python windowsautomation.py
