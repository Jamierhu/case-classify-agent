# =====================================================================
# 病例图片分类小工具 - Windows 卸载脚本 (PowerShell)
# 停止并移除服务/计划任务，保留代码和数据
# 用法：以管理员身份运行 PowerShell，执行 .\uninstall.ps1
# =====================================================================

# ---------- 管理员权限检查 ----------
$currentUser = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentUser.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] 请以管理员身份运行 PowerShell" -ForegroundColor Red
    exit 1
}

# ---------- 颜色输出 ----------
function Info($msg)  { Write-Host "[INFO] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[WARN] $msg" -ForegroundColor Yellow }

# ---------- 变量 ----------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServiceName = "case-classify"
$TaskName = "CaseClassifyAgent"
$AppPort = 8000
$OllamaPort = 11434

Write-Host "[WARN] 即将卸载病例分类服务（不会删除代码、数据库和已归档图片）" -ForegroundColor Yellow
$confirm = Read-Host "确认卸载？(y/N)"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "已取消"
    exit 0
}

# ---------- 停止并移除服务/计划任务 ----------

# 1. 尝试移除 Windows 服务（NSSM 注册的）
$svc = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
if ($svc) {
    if ($svc.Status -eq "Running") {
        Stop-Service -Name $ServiceName -Force
        Info "服务已停止"
    }
    nssm remove $ServiceName confirm 2>$null
    Info "Windows 服务已移除"
} else {
    Info "未检测到 Windows 服务，跳过"
}

# 2. 尝试移除计划任务
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Info "计划任务已移除"
} else {
    Info "未检测到计划任务，跳过"
}

# ---------- 关闭防火墙端口 ----------
$webRule = Get-NetFirewallRule -DisplayName "Case-Classify Web ($AppPort)" -ErrorAction SilentlyContinue
if ($webRule) {
    Remove-NetFirewallRule -DisplayName "Case-Classify Web ($AppPort)"
    Info "Web 防火墙规则已移除"
}

$ollamaRule = Get-NetFirewallRule -DisplayName "Ollama API ($OllamaPort)" -ErrorAction SilentlyContinue
if ($ollamaRule) {
    Remove-NetFirewallRule -DisplayName "Ollama API ($OllamaPort)"
    Info "Ollama 防火墙规则已移除"
}

# ---------- 删除日志 ----------
$logFile = Join-Path $ScriptDir "case-classify.log"
if (Test-Path $logFile) {
    $delLog = Read-Host "是否删除应用日志 case-classify.log？(y/N)"
    if ($delLog -eq "y" -or $delLog -eq "Y") {
        Remove-Item $logFile -Force
        Info "日志文件已删除"
    }
}

# ---------- 是否卸载 Ollama ----------
$delOllama = Read-Host "是否同时卸载 Ollama？(y/N)"
if ($delOllama -eq "y" -or $delOllama -eq "Y") {
    # 停止 Ollama 服务
    $ollamaSvc = Get-Service -Name "Ollama" -ErrorAction SilentlyContinue
    if ($ollamaSvc) {
        Stop-Service -Name "Ollama" -Force -ErrorAction SilentlyContinue
        # 删除 Ollama 系统环境变量
        [System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", $null, "Machine")
        [System.Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", $null, "Machine")
        [System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", $null, "Machine")
        [System.Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", $null, "Machine")
    }

    # 卸载 Ollama（通过控制面板）
    $ollamaApp = Get-ItemProperty "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match "Ollama" }
    if ($ollamaApp) {
        $uninstallString = $ollamaApp.UninstallString
        if ($uninstallString) {
            Start-Process -FilePath "cmd.exe" -ArgumentList "/c", $uninstallString, "/SILENT" -Wait -ErrorAction SilentlyContinue
            Info "Ollama 已卸载"
        }
    } else {
        Warn "未在注册表中找到 Ollama，请手动从「设置 → 应用」卸载"
    }
} else {
    Info "保留 Ollama 安装"
}

Write-Host ""
Info "✅ 卸载完成"
Write-Host "代码、数据库(case_record.db)、归档图片(case_output/) 均已保留"