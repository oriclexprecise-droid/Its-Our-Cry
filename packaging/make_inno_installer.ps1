# 使用 Inno Setup 生成标准安装包
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$iscc = Join-Path $PSScriptRoot 'tools\innosetup6\ISCC.exe'
$appVersion = (Select-String -Path (Join-Path $PSScriptRoot 'its_our_cry.iss') -Pattern '#define MyAppVersion "([^"]+)"').Matches[0].Groups[1].Value
if (-not (Test-Path $iscc)) { throw '未找到 Inno Setup，请先安装到 packaging\tools\innosetup6' }
$releaseApp = Join-Path $root "release\It's Our Cry"
if (-not (Test-Path (Join-Path $releaseApp 'ItsOurCry.exe'))) { throw '请先运行 build_app.ps1' }

Write-Host '== 组装 Inno 载荷 =='
$payload = Join-Path $PSScriptRoot 'installer\inno_payload\app'
if (Test-Path $payload) { Remove-Item $payload -Recurse -Force }
New-Item -ItemType Directory -Force -Path $payload | Out-Null
Get-ChildItem -LiteralPath $releaseApp -Force | Where-Object { $_.Name -notin @('launcher.log', 'work') } | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination $payload -Recurse -Force
}
# config.yaml 保留在载荷中，Inno 用 onlyifdoesntexist 安装，升级时保留用户配置

Write-Host '== ISCC 编译 Inno 安装包 =='
Set-Location $PSScriptRoot
& $iscc 'its_our_cry.iss'
if ($LASTEXITCODE -ne 0) { throw 'Inno 编译失败' }

Remove-Item $payload -Recurse -Force
Write-Host '== Inno 安装包完成 =='
Write-Host (Join-Path $root ('release\It-sOurCry-Setup-V' + $appVersion + '-Inno.exe'))
