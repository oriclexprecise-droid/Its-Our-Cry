# Smoke test runner: starts a temporary server, runs tests/smoke_frontend.js, restores recent_results.json
param(
  [string]$Port = '5134',
  [string]$RuntimePython = 'E:\GPT-SoVITS-v2pro-20250604-nvidia50\GPT-SoVITS-v2pro-20250604-nvidia50\runtime\python.exe',
  [string]$Node = 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe',
  [string]$PlaywrightNodeModules = 'C:\Program Files\WindowsApps\OpenAI.Codex_26.727.6591.0_x64__2p2nqsd0c76g0\app\resources\cua_node\bin\node_modules'
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$recent = Join-Path $root 'work\recent_results.json'
$recentBak = Join-Path $env:TEMP 'ioc_smoke_recent_results.json'
$hadRecent = Test-Path $recent
if ($hadRecent) { Copy-Item $recent $recentBak -Force }

& $RuntimePython -m py_compile (Join-Path $root 'backend\server.py')
if ($LASTEXITCODE -ne 0) { throw 'py_compile failed' }
& $RuntimePython (Join-Path $PSScriptRoot 'check_prompt_consistency.py')
if ($LASTEXITCODE -ne 0) { throw 'prompt consistency check failed' }
& $Node --check (Join-Path $PSScriptRoot 'smoke_frontend.js')
if ($LASTEXITCODE -ne 0) { throw 'node --check failed' }

$serverPy = Join-Path $env:TEMP 'ioc_smoke_server.py'
$serverOut = Join-Path $env:TEMP 'ioc_smoke_server.out.log'
$serverErr = Join-Path $env:TEMP 'ioc_smoke_server.err.log'
@"
import os, sys
os.chdir(r"$root")
sys.path.insert(0, r"$root")
from backend.server import create_app
app = create_app("config.yaml")
app.run(host="127.0.0.1", port=$Port, debug=False)
"@ | Set-Content -Path $serverPy -Encoding ASCII

$psi = [System.Diagnostics.ProcessStartInfo]::new()
$psi.FileName = $RuntimePython
$psi.Arguments = '"' + $serverPy + '"'
$psi.WorkingDirectory = $root
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$proc = [System.Diagnostics.Process]::new()
$proc.StartInfo = $psi
$proc.Start() | Out-Null
$outTask = $proc.StandardOutput.ReadToEndAsync()
$errTask = $proc.StandardError.ReadToEndAsync()
$failed = $false
try {
  Start-Sleep -Seconds 5
  $env:BASE_URL = "http://127.0.0.1:$Port"
  if ($env:NODE_PATH) { $env:NODE_PATH = $PlaywrightNodeModules + ';' + $env:NODE_PATH } else { $env:NODE_PATH = $PlaywrightNodeModules }
  & $Node (Join-Path $PSScriptRoot 'smoke_frontend.js')
  if ($LASTEXITCODE -ne 0) { $failed = $true }
} catch {
  $failed = $true
  Write-Host $_
  if (Test-Path $serverErr) { Get-Content $serverErr -TotalCount 30 }
} finally {
  Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
  Start-Sleep -Milliseconds 500
  $outTask.Wait(1000) | Out-Null
  $errTask.Wait(1000) | Out-Null
  [System.IO.File]::WriteAllText($serverOut, $outTask.Result)
  [System.IO.File]::WriteAllText($serverErr, $errTask.Result)
  Remove-Item $serverPy, $serverOut, $serverErr -Force -ErrorAction SilentlyContinue
  if ($hadRecent) {
    if (Test-Path $recentBak) { Copy-Item $recentBak $recent -Force }
    Remove-Item $recentBak -Force -ErrorAction SilentlyContinue
  }
}
if ($failed) { throw 'SMOKE FAILED' }
Write-Host 'SMOKE PASS'
