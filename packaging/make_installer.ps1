# 使用 7-Zip SFX 生成安装包
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$sevenZip = 'C:\Program Files\7-Zip\7z.exe'
$sfxModule = 'C:\Program Files\7-Zip\7z.sfx'
$installerDir = Join-Path $PSScriptRoot 'installer'
$payloadDir = Join-Path $installerDir 'payload'
$archive = Join-Path $installerDir 'payload.7z'
$output = Join-Path $root "release\It-sOurCry-Setup.exe"
$releaseApp = Join-Path $root "release\It's Our Cry"

if (-not (Test-Path $sevenZip)) { throw '未找到 7-Zip' }
if (-not (Test-Path $releaseApp)) { throw '请先运行 build_app.ps1' }
if (Test-Path $payloadDir) { Remove-Item $payloadDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path (Join-Path $payloadDir 'app') | Out-Null

Write-Host '== 组装安装包载荷 =='
Copy-Item (Join-Path $releaseApp '*') (Join-Path $payloadDir 'app') -Recurse -Force
# 剔除运行期生成的本地数据，避免把开发者的 Key/日志/输出带进安装包
$appPayload = Join-Path $payloadDir 'app'
foreach ($f in @('user_settings.json','model_aliases.json','user_models.json','launcher.log','server.err.log','server.out.log','install_info.txt','backend\tts_worker.py')) {
  Remove-Item (Join-Path $appPayload $f) -Force -ErrorAction SilentlyContinue
}
foreach ($d in @('output','exports','feedback','backend')) {
  Remove-Item (Join-Path $appPayload $d) -Recurse -Force -ErrorAction SilentlyContinue
}
Copy-Item (Join-Path $installerDir 'setup.ps1') $payloadDir -Force

if (Test-Path $archive) { Remove-Item $archive -Force }
Write-Host '== 压缩载荷 =='
Set-Location $payloadDir
& $sevenZip a -t7z -mx=1 $archive '*'
if ($LASTEXITCODE -ne 0) { throw '7z 压缩失败' }
Set-Location $PSScriptRoot

Write-Host '== 生成 SFX 安装程序 =='
if (Test-Path $output) { Remove-Item $output -Force }
Copy-Item $sfxModule $output -Force
[System.IO.File]::AppendAllText($output, [System.IO.File]::ReadAllText((Join-Path $installerDir 'installer_config.txt')))
$outStream = [System.IO.File]::Open($output, [System.IO.FileMode]::Append)
$inStream = [System.IO.File]::OpenRead($archive)
$buffer = New-Object byte[] 1048576
try {
  while (($read = $inStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
    $outStream.Write($buffer, 0, $read)
  }
} finally {
  $inStream.Close()
  $outStream.Close()
}

$size = (Get-Item $output).Length / 1MB
Write-Host ("== 安装包完成: {0:N1} MB ==" -f $size)
Write-Host $output

Remove-Item $payloadDir -Recurse -Force
Remove-Item $archive -Force
