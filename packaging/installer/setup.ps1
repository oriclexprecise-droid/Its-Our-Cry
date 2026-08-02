param([string]$InstallDir = "")
$ErrorActionPreference = 'Stop'
$silent = $env:MYGO_SILENT_INSTALL -eq '1'

function Write-InstallLog {
  param([string]$Message)
  $logLine = "[{0}] {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
  Write-Host $logLine
  try {
    $logPath = Join-Path $env:TEMP 'ItsOurCry_install.log'
    Add-Content -LiteralPath $logPath -Value $logLine -Encoding UTF8
  } catch {}
}

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
  if ($folderDialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 1 }
  $InstallDir = $folderDialog.SelectedPath
}
if ([string]::IsNullOrWhiteSpace($InstallDir)) { $InstallDir = $defaultInstallDir }
$InstallDir = [System.IO.Path]::GetFullPath($InstallDir)

$driveRoot = [System.IO.Path]::GetPathRoot($InstallDir)
if ($InstallDir.TrimEnd('\') -eq $driveRoot.TrimEnd('\')) { throw '不能安装到磁盘根目录' }
if ($InstallDir.TrimEnd('\') -eq $PSScriptRoot.TrimEnd('\')) { throw '不能安装到安装包解压目录' }

Write-InstallLog ("开始安装: " + $InstallDir)

try {
  $srcBytes = (Get-ChildItem -LiteralPath $src -Recurse -File | Measure-Object Length -Sum).Sum
  $drive = New-Object System.IO.DriveInfo($driveRoot)
  if ($drive.IsReady -and ($drive.AvailableFreeSpace -lt ($srcBytes + 200MB))) {
    throw '磁盘空间不足'
  }
} catch { throw "磁盘空间检查失败: $_" }

$hadOld = Test-Path $InstallDir
if ($hadOld) {
  $oldExeCheck = Join-Path $InstallDir 'ItsOurCry.exe'
  $oldInfoCheck = Join-Path $InstallDir 'install_info.txt'
  $oldIsApp = (Test-Path $oldExeCheck) -or (Test-Path $oldInfoCheck)
  $oldHasFiles = (Get-ChildItem -LiteralPath $InstallDir -Force | Measure-Object).Count -gt 0
  if ($oldHasFiles -and -not $oldIsApp) {
    throw "安装目录已存在但不是本程序目录，为避免误删文件请换一个目录：$InstallDir"
  }
  $homeCheck = $env:USERPROFILE.TrimEnd('\')
  $desktopCheck = [Environment]::GetFolderPath('Desktop').TrimEnd('\')
  if ($InstallDir.TrimEnd('\') -eq $homeCheck -or $InstallDir.TrimEnd('\') -eq $desktopCheck) {
    throw '不能安装到用户主目录或桌面'
  }
}
if ($hadOld -and -not $silent) {
  Add-Type -AssemblyName System.Windows.Forms
  $choice = [System.Windows.Forms.MessageBox]::Show("安装目录已存在，是否覆盖安装？`n$InstallDir", "It's Our Cry!!!!!", [System.Windows.Forms.MessageBoxButtons]::YesNo, [System.Windows.Forms.MessageBoxIcon]::Question)
  if ($choice -ne [System.Windows.Forms.DialogResult]::Yes) { exit 1 }
}

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$staging = Join-Path (Split-Path $InstallDir -Parent) (".ItsOurCry_new_" + $stamp)
$backup = Join-Path (Split-Path $InstallDir -Parent) (".ItsOurCry_old_" + $stamp)
if (Test-Path $staging) { Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path $backup) { Remove-Item $backup -Recurse -Force -ErrorAction SilentlyContinue }

Write-InstallLog ("复制到临时目录: " + $staging)
New-Item -ItemType Directory -Force -Path $staging | Out-Null
try {
  Copy-Item -Path (Join-Path $src '*') -Destination $staging -Recurse -Force
} catch {
  Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
  Write-InstallLog ("复制失败: " + $_)
  throw "复制安装数据失败: $_"
}
$exe = Join-Path $staging 'ItsOurCry.exe'
if (-not (Test-Path $exe)) {
  Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
  throw '安装数据缺少 ItsOurCry.exe'
}

if ($hadOld) {
  Write-InstallLog ("备份旧目录: " + $backup)
  try {
    Move-Item -LiteralPath $InstallDir -Destination $backup
  } catch {
    Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
    Write-InstallLog ("备份旧目录失败: " + $_)
    throw "旧程序可能正在运行，请先关闭 It's Our Cry 再安装: $_"
  }
}

try {
  Write-InstallLog ("放置新版本: " + $InstallDir)
  Move-Item -LiteralPath $staging -Destination $InstallDir
  $exe = Join-Path $InstallDir 'ItsOurCry.exe'
} catch {
  if ($hadOld -and (Test-Path $backup)) {
    Move-Item -LiteralPath $backup -Destination $InstallDir -ErrorAction SilentlyContinue
  }
  Remove-Item $staging -Recurse -Force -ErrorAction SilentlyContinue
  Write-InstallLog ("替换失败: " + $_)
  throw "替换安装目录失败: $_"
}

if ($hadOld -and (Test-Path $backup)) {
  foreach ($keep in @('config.yaml','user_settings.json','model_aliases.json','user_models.json')) {
    $keepSrc = Join-Path $backup $keep
    $keepDst = Join-Path $InstallDir $keep
    if ((Test-Path $keepSrc) -and -not (Test-Path $keepDst)) {
      Copy-Item -LiteralPath $keepSrc -Destination $keepDst -Force
      Write-InstallLog ("保留用户文件: " + $keep)
    }
  }
  Write-InstallLog "删除旧版本备份"
  Remove-Item $backup -Recurse -Force -ErrorAction SilentlyContinue
}

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

"install_dir=$InstallDir`ntime=$([DateTime]::Now.ToString('yyyy-MM-dd HH:mm:ss'))" | Out-File -FilePath (Join-Path $InstallDir 'install_info.txt') -Encoding UTF8
Write-InstallLog "安装完成"

if (-not $silent) {
  Add-Type -AssemblyName System.Windows.Forms
  [System.Windows.Forms.MessageBox]::Show("安装完成！`n$InstallDir`n`n点击确定启动程序", "It's Our Cry!!!!!") | Out-Null
  Start-Process $exe
}
Write-Host "安装完成: $InstallDir"
