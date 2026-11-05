# 联网查询MCP · Windows 开机自启（当前用户，无需管理员）
#
# 用法（在你自己的 PowerShell 窗口里跑，不要用受限沙箱）：
#   powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install-autostart.ps1 -Uninstall
#
# 做的事：在「启动」文件夹放一个快捷方式，登录后自动用 pythonw 起 web_config.py，
# 它会监听 8765 并负责拉起/守护 MCP 桥接进程。窗口不弹出，日志看 /logs 页面。

param([switch]$Uninstall)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$target = Join-Path $root "web_config.py"
if (-not (Test-Path $target)) {
    throw "找不到 $target，请在仓库根目录下运行本脚本"
}

$startup = [Environment]::GetFolderPath("Startup")
$lnk = Join-Path $startup "联网查询MCP.lnk"

if ($Uninstall) {
    if (Test-Path $lnk) {
        Remove-Item $lnk -Force
        Write-Host "已移除开机自启：$lnk"
    } else {
        Write-Host "本来就没有装开机自启"
    }
    return
}

$python = (Get-Command python -ErrorAction Stop).Source
$pythonw = Join-Path (Split-Path -Parent $python) "pythonw.exe"
if (-not (Test-Path $pythonw)) {
    $pythonw = $python
    Write-Host "警告：没找到 pythonw.exe，改用 python.exe（登录时会闪一个控制台窗口）"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnk)
$shortcut.TargetPath = $pythonw
$shortcut.Arguments = '"' + $target + '"'
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7           # 最小化
$shortcut.Description = "联网查询MCP 配置页与桥接守护（http://127.0.0.1:8765）"
$shortcut.Save()

Write-Host "已安装开机自启：$lnk"
Write-Host "解释器：$pythonw"
Write-Host "下次登录 Windows 后自动启动，打开 http://127.0.0.1:8765 即可。"
