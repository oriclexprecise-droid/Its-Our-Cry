# 构建自包含 exe 并整理发布目录
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$runtime = 'E:\GPT-SoVITS-v2pro-20250604-nvidia50\GPT-SoVITS-v2pro-20250604-nvidia50\runtime'
$pyinstaller = Join-Path $runtime 'Scripts\pyinstaller.exe'
$releaseApp = Join-Path $root "release\It's Our Cry"

Write-Host '== 清理旧构建产物 =='
foreach ($d in @((Join-Path $root 'build'), (Join-Path $root 'dist'), (Join-Path $root 'release'))) {
  if (Test-Path $d) { Remove-Item $d -Recurse -Force }
}

Write-Host '== PyInstaller 构建 exe =='
Set-Location $root
& $pyinstaller `
  --noconfirm --clean --onedir --noconsole `
  --name ItsOurCry `
  --exclude-module torch --exclude-module torchaudio --exclude-module torchvision `
  --exclude-module GPT_SoVITS --exclude-module numpy --exclude-module scipy `
  --exclude-module librosa --exclude-module transformers --exclude-module soundfile `
  --exclude-module gradio --exclude-module faiss --exclude-module onnxruntime `
  launcher.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 构建失败' }

Write-Host '== 整理发布目录 =='
New-Item -ItemType Directory -Force -Path $releaseApp | Out-Null
Copy-Item (Join-Path $root 'dist\ItsOurCry\*') $releaseApp -Recurse -Force
Copy-Item (Join-Path $root 'frontend') $releaseApp -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $releaseApp 'backend') | Out-Null
Copy-Item (Join-Path $root 'backend\tts_worker.py') (Join-Path $releaseApp 'backend\tts_worker.py') -Force
Copy-Item (Join-Path $root 'GPT_weights_v2ProPlus') $releaseApp -Recurse -Force
Copy-Item (Join-Path $root 'SoVITS_weights_v2ProPlus') $releaseApp -Recurse -Force
Copy-Item (Join-Path $root 'reference_audio') $releaseApp -Recurse -Force
Copy-Item (Join-Path $root 'picture') $releaseApp -Recurse -Force
Copy-Item (Join-Path $PSScriptRoot 'release_config.yaml') (Join-Path $releaseApp 'config.yaml') -Force

$readme = @"
It's Our Cry!!!!! 配音工作台（自包含版）

使用步骤：
1. 双击 ItsOurCry.exe 打开工作台。
2. 首次使用前，到「部署」板块下载/填写 GPT-SoVITS 目录并安装依赖。
3. 到「设置」填写 DeepSeek API Key 后开始使用。

注意：GPT-SoVITS 运行时和模型推理依赖不随程序打包，请在部署板块完成。
"@
[System.IO.File]::WriteAllText((Join-Path $releaseApp '使用说明.txt'), $readme, (New-Object System.Text.UTF8Encoding($false)))

$size = (Get-ChildItem $releaseApp -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("== 发布目录完成: {0:N1} MB ==" -f $size)
Write-Host $releaseApp
