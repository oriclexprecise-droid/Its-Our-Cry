param([string]$InstallDir = "")
$ErrorActionPreference = 'Stop'
$silent = $env:MYGO_SILENT_INSTALL -eq '1'

$src = Join-Path $PSScriptRoot 'app'
if (-not (Test-Path $src)) { $src = Join-Path (Get-Location) 'app' }
if (-not (Test-Path $src)) { throw '未找到安装数据' }

$defaultInstallDir = Join-Path $env:USERPROFILE "It's Our Cry"
if ([string]::IsNullOrWhiteSpace($InstallDir) -and -not $silent) {
  Add-Type -AssemblyName System.Windows.Forms
  $folderDialog = New-Object System.Windows.Forms.FolderBrowserDialog
  $folderDialog.Description = "选择 It's Our Cry!!!!! 的安装位置（可安装到任意盘符）"
  $folderDialog.SelectedPath = $defaultInstallDir
  $folderDialog.ShowNewFolderButton = $true
  if ($folderDialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) {
    exit 1
  }
  $InstallDir = $folderDialog.SelectedPath
}
if ([string]::IsNullOrWhiteSpace($InstallDir)) {
  $InstallDir = $defaultInstallDir
}

if (Test-Path $InstallDir) {
  if ($silent) { Remove-Item $InstallDir -Recurse -Force }
  else {
    Add-Type -AssemblyName System.Windows.Forms
    $choice = [System.Windows.Forms.MessageBox]::Show(
      "安装目录已存在，是否覆盖安装？`n$InstallDir",
      "It's Our Cry!!!!!",
      [System.Windows.Forms.MessageBoxButtons]::YesNo,
      [System.Windows.Forms.MessageBoxIcon]::Question
    )
    if ($choice -ne [System.Windows.Forms.DialogResult]::Yes) { exit 1 }
    Remove-Item $InstallDir -Recurse -Force
  }
}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

Write-Host "正在复制文件到 $InstallDir ..."
Copy-Item -Path (Join-Path $src '*') -Destination $InstallDir -Recurse -Force

$exe = Join-Path $InstallDir 'ItsOurCry.exe'
if (Test-Path $exe) {
  $ws = New-Object -ComObject WScript.Shell

  $desktop = [Environment]::GetFolderPath('Desktop')
  $lnk1 = $ws.CreateShortcut((Join-Path $desktop "It's Our Cry!!!!!.lnk"))
  $lnk1.TargetPath = $exe
  $lnk1.WorkingDirectory = $InstallDir
  $lnk1.Description = "It's Our Cry!!!!! 配音工作台"
  $lnk1.Save()

  $menuDir = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\It's Our Cry"
  New-Item -ItemType Directory -Force -Path $menuDir | Out-Null
  $lnk2 = $ws.CreateShortcut((Join-Path $menuDir "It's Our Cry!!!!!.lnk"))
  $lnk2.TargetPath = $exe
  $lnk2.WorkingDirectory = $InstallDir
  $lnk2.Description = "It's Our Cry!!!!! 配音工作台"
  $lnk2.Save()
}

"install_dir=$InstallDir`ntime=$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))" | Out-File -FilePath (Join-Path $InstallDir 'install_info.txt') -Encoding UTF8

if (-not $silent) {
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.MessageBox]::Show("安装完成！`n$InstallDir`n`n点击确定启动程序", "It's Our Cry!!!!!") | Out-Null
  Start-Process $exe
}
Write-Host "安装完成: $InstallDir"
