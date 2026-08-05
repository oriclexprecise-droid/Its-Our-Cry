# 使用 Inno Setup 生成增量更新安装包（不含模型权重，覆盖安装时保留大文件）
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$iscc = Join-Path $PSScriptRoot 'tools\innosetup6\ISCC.exe'
$appVersion = (Select-String -Path (Join-Path $PSScriptRoot 'its_our_cry_update.iss') -Pattern '#define MyAppVersion "([^"]+)"').Matches[0].Groups[1].Value
if (-not (Test-Path $iscc)) { throw '未找到 Inno Setup，请先安装到 packaging\tools\innosetup6' }
$releaseApp = Join-Path $root "release\It's Our Cry"
if (-not (Test-Path (Join-Path $releaseApp 'ItsOurCry.exe'))) { throw '请先运行 build_app.ps1' }

Write-Host '== 组装更新载荷（排除权重目录） =='
$payload = Join-Path $PSScriptRoot 'installer\inno_update_payload\app'
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Force -Path $payload | Out-Null
$exclude = @('GPT_weights_v2ProPlus', 'SoVITS_weights_v2ProPlus', 'launcher.log', 'server.log', 'server.out.log', 'server.err.log', 'server_error.log', 'work', 'feedback', 'exports', 'output', 'outputs', 'logs', 'user_settings.json', 'user_models.json', 'model_aliases.json', 'ai_cache.json', 'ai_usage.json')
Get-ChildItem -LiteralPath $releaseApp -Force | Where-Object { $_.Name -notin $exclude } | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $payload -Recurse -Force
}
$size = (Get-ChildItem $payload -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("更新载荷大小: {0:N1} MB" -f $size)

Write-Host '== ISCC 编译增量更新包 =='
Set-Location $PSScriptRoot
& $iscc 'its_our_cry_update.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno 更新包编译失败' }

Remove-Item $payload -Recurse -Force
Write-Host '== 增量更新包完成 =='
Write-Host (Join-Path $root ('release\It-sOurCry-Update-V' + $appVersion + '-Inno.exe'))
