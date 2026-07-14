# =====================================================================
# 病例图片分类小工具 - Windows 一键部署脚本 (PowerShell)
# 功能：
#   1. 安装系统依赖（Python、Ollama）
#   2. 拉取视觉模型 qwen3-vl:8b
#   3. 配置 Ollama 并发/常驻显存等优化项
#   4. 创建 Python 虚拟环境并安装依赖
#   5. 注册 Windows 服务 / 计划任务（开机自启 + 局域网访问）
#   6. 配置防火墙放行端口
# 用法：
#   以管理员身份运行 PowerShell，执行：
#   .\deploy.ps1
#   可选参数：
#   .\deploy.ps1 -Mode service    # 注册为 Windows 服务（需 NSSM）
#   .\deploy.ps1 -Mode task       # 注册为开机计划任务（默认）
#   .\deploy.ps1 -Mode dev        # 仅安装依赖，不注册自启（开发模式）
# =====================================================================
param(
    [string]$Mode = "task",
    [string]$PythonBin = "python",
    [string]$OllamaModel = "qwen3-vl:8b",
    [int]$AppPort = 8000,
    [int]$OllamaPort = 11434
)

# ---------- 管理员权限检查 ----------
$currentUser = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $currentUser.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "[ERROR] 请以管理员身份运行 PowerShell" -ForegroundColor Red
    Write-Host "  右键 PowerShell → '以管理员身份运行'，然后重新执行此脚本" -ForegroundColor Yellow
    exit 1
}

# ---------- 颜色输出 ----------
function Info($msg)  { Write-Host "[INFO] $msg" -ForegroundColor Green }
function Warn($msg)  { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function ErrorMsg($msg) { Write-Host "[ERROR] $msg" -ForegroundColor Red }
function Step($msg)  { Write-Host "`n========== $msg ==========" -ForegroundColor Cyan }

# ---------- 变量 ----------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ServiceName = "case-classify"
$VenvDir = Join-Path $ScriptDir ".venv"
$TaskName = "CaseClassifyAgent"

# ---------- 前置检查 ----------
Step "1/7 环境检查"

Info "检测到系统：Windows"

# 检查 Python
try {
    $pyVersion = & $PythonBin --version 2>&1
    $pyVer = $pyVersion.ToString().Replace("Python ", "")
    $pyParts = $pyVer.Split('.')
    $pyMajor = [int]$pyParts[0]
    $pyMinor = [int]$pyParts[1]
    if ($pyMajor -lt 3 -or ($pyMajor -eq 3 -and $pyMinor -lt 8)) {
        ErrorMsg "Python 版本过低，需要 3.8+，当前版本：$pyVer"
        Write-Host "  请从 https://www.python.org/downloads/ 下载安装 Python 3.8+" -ForegroundColor Yellow
        exit 1
    }
    Info "Python 版本：$pyVer"
} catch {
    ErrorMsg "未检测到 Python，请先安装 Python 3.8+"
    Write-Host "  下载地址：https://www.python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  安装时请勾选 'Add Python to PATH'" -ForegroundColor Yellow
    exit 1
}

# 检测 GPU
$nvidiaSmi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
if ($nvidiaSmi) {
    Info "检测到 NVIDIA GPU，Ollama 将使用 GPU 加速"
} else {
    Warn "未检测到 NVIDIA GPU，Ollama 将使用 CPU 推理（速度较慢）"
}

# 获取本机局域网 IP
$lanIP = "127.0.0.1"
try {
    $ipConfig = Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.PrefixOrigin -eq "Dhcp" } | Select-Object -First 1
    if ($ipConfig) {
        $lanIP = $ipConfig.IPAddress
        Info "本机局域网 IP：$lanIP"
    } else {
        Warn "无法自动获取局域网 IP，默认使用 127.0.0.1"
    }
} catch {
    Warn "无法自动获取局域网 IP，默认使用 127.0.0.1"
}

# ---------- 安装系统依赖 ----------
Step "2/7 安装系统依赖（curl、防火墙）"

# Windows 自带 curl 和防火墙，检查即可
$curlOk = Get-Command curl -ErrorAction SilentlyContinue
if ($curlOk) {
    Info "curl 已就绪"
} else {
    Warn "未检测到 curl，部分功能可能受限"
}

Info "系统依赖检查完成"

# ---------- 安装 Ollama ----------
Step "3/7 安装 Ollama 并拉取模型"

$ollamaCmd = Get-Command ollama -ErrorAction SilentlyContinue
if ($ollamaCmd) {
    Info "Ollama 已安装，版本：$(ollama --version 2>$null)"
} else {
    Info "正在安装 Ollama..."
    $ollamaInstaller = "$env:TEMP\OllamaSetup.exe"
    try {
        Invoke-WebRequest -Uri "https://ollama.com/download/OllamaSetup.exe" -OutFile $ollamaInstaller -UseBasicParsing
        Start-Process -FilePath $ollamaInstaller -Wait -ArgumentList "/SILENT"
        # 刷新 PATH
        $env:Path = [System.Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [System.Environment]::GetEnvironmentVariable("Path", "User")
        Info "Ollama 安装完成"
    } catch {
        ErrorMsg "Ollama 下载安装失败：$_"
        Write-Host "  请手动从 https://ollama.com/download 下载安装" -ForegroundColor Yellow
        exit 1
    }
}

# 启动 Ollama 服务（Windows 版 Ollama 安装后会自动注册为服务）
$ollamaService = Get-Service -Name "Ollama" -ErrorAction SilentlyContinue
if ($ollamaService) {
    if ($ollamaService.Status -ne "Running") {
        Start-Service -Name "Ollama"
        Info "Ollama 服务已启动"
    } else {
        Info "Ollama 服务正在运行"
    }
    Set-Service -Name "Ollama" -StartupType Automatic
    Info "Ollama 服务已设置为开机自启"
} else {
    # 如果没有注册为服务，手动启动
    Warn "Ollama 未注册为系统服务，尝试手动启动..."
    $ollamaPath = (Get-Command ollama -ErrorAction SilentlyContinue).Source
    if ($ollamaPath) {
        Start-Process -FilePath $ollamaPath -ArgumentList "serve" -WindowStyle Hidden
        Info "Ollama 已手动启动（后台运行）"
    }
}

# 配置 Ollama 优化项（通过环境变量）
Step "3.1 配置 Ollama 性能优化（并发=4, 常驻显存, FlashAttention）"

# 设置系统环境变量（永久生效）
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:$OllamaPort", "Machine")
[System.Environment]::SetEnvironmentVariable("OLLAMA_NUM_PARALLEL", "4", "Machine")
[System.Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "-1", "Machine")
[System.Environment]::SetEnvironmentVariable("OLLAMA_FLASH_ATTENTION", "1", "Machine")

# 当前会话也生效
$env:OLLAMA_HOST = "0.0.0.0:$OllamaPort"
$env:OLLAMA_NUM_PARALLEL = "4"
$env:OLLAMA_KEEP_ALIVE = "-1"
$env:OLLAMA_FLASH_ATTENTION = "1"

# 重启 Ollama 服务以应用新环境变量
if ($ollamaService) {
    Restart-Service -Name "Ollama" -Force
    Info "Ollama 服务已重启以应用性能优化配置"
}

# 等待 Ollama API 就绪
Info "等待 Ollama 服务就绪..."
$ollamaReady = $false
for ($i = 1; $i -le 30; $i++) {
    try {
        $null = Invoke-RestMethod -Uri "http://127.0.0.1:$OllamaPort/api/tags" -Method Get -TimeoutSec 3 -ErrorAction Stop
        $ollamaReady = $true
        Info "Ollama 服务已就绪"
        break
    } catch {
        if ($i -eq 30) {
            ErrorMsg "Ollama 服务启动超时（30秒）"
            Write-Host "  请检查 Ollama 是否正常运行" -ForegroundColor Yellow
            exit 1
        }
        Start-Sleep -Seconds 1
    }
}

# 拉取视觉模型
Step "3.2 拉取视觉模型 $OllamaModel（首次拉取体积较大，请耐心等待）"
$existingModels = ollama list 2>$null
if ($existingModels -match $OllamaModel) {
    Info "模型 $OllamaModel 已存在，跳过拉取"
} else {
    ollama pull $OllamaModel
    Info "模型 $OllamaModel 拉取完成"
}

# ---------- 创建 Python 虚拟环境 ----------
Step "4/7 创建 Python 虚拟环境并安装依赖"

if (-not (Test-Path $VenvDir)) {
    Info "创建虚拟环境：$VenvDir"
    & $PythonBin -m venv $VenvDir
}

# 激活虚拟环境
$activateScript = Join-Path $VenvDir "Scripts\Activate.ps1"
& $activateScript

# 升级 pip
pip install --upgrade pip -q

# 安装依赖
Info "安装 Python 依赖..."
pip install -r (Join-Path $ScriptDir "requirements.txt") -q
Info "Python 依赖安装完成"

# ---------- 注册服务 ----------
Step "5/7 注册服务（模式：$Mode）"

$uvicornPath = Join-Path $VenvDir "Scripts\uvicorn.exe"

if ($Mode -eq "dev") {
    Info "开发模式：跳过服务注册（使用 'python case_classify_agent.py' 手动启动）"
} elseif ($Mode -eq "service") {
    # Windows 服务模式：使用 NSSM（Non-Sucking Service Manager）
    $nssmPath = Get-Command nssm -ErrorAction SilentlyContinue
    if (-not $nssmPath) {
        Warn "未检测到 NSSM，正在尝试下载..."
        $nssmUrl = "https://nssm.cc/release/nssm-2.24.zip"
        $nssmZip = "$env:TEMP\nssm.zip"
        $nssmDir = "$env:TEMP\nssm"
        try {
            Invoke-WebRequest -Uri $nssmUrl -OutFile $nssmZip -UseBasicParsing
            Expand-Archive -Path $nssmZip -DestinationPath $nssmDir -Force
            # 根据系统架构选择
            $arch = if ([Environment]::Is64BitOperatingSystem) { "win64" } else { "win32" }
            $nssmExe = Get-ChildItem -Path $nssmDir -Recurse -Filter "nssm.exe" | Where-Object { $_.DirectoryName -match $arch } | Select-Object -First 1
            if ($nssmExe) {
                # 复制到系统目录
                Copy-Item $nssmExe.FullName "C:\Windows\System32\nssm.exe" -Force
                Info "NSSM 安装完成"
            } else {
                throw "未找到匹配的 NSSM 可执行文件"
            }
        } catch {
            Warn "NSSM 下载失败，回退到计划任务模式"
            $Mode = "task"
        }
    }

    if ($Mode -eq "service") {
        # 注册 Windows 服务
        nssm install $ServiceName $uvicornPath "case_classify_agent:app --host 0.0.0.0 --port $AppPort"
        nssm set $ServiceName AppDirectory $ScriptDir
        nssm set $ServiceName Start SERVICE_AUTO_START
        nssm set $ServiceName AppStdout "$ScriptDir\case-classify.log"
        nssm set $ServiceName AppStderr "$ScriptDir\case-classify.log"
        nssm start $ServiceName
        Info "Windows 服务 $ServiceName 已注册并启动"
    }
}

if ($Mode -eq "task") {
    # 计划任务模式：开机自启
    # 先删除已存在的任务
    $existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existingTask) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    }

    $action = New-ScheduledTaskAction `
        -Execute $uvicornPath `
        -Argument "case_classify_agent:app --host 0.0.0.0 --port $AppPort" `
        -WorkingDirectory $ScriptDir

    $trigger = New-ScheduledTaskTrigger -AtStartup

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1)

    $principal = New-ScheduledTaskPrincipal `
        -UserId "NT AUTHORITY\SYSTEM" `
        -LogonType ServiceAccount `
        -RunLevel Highest

    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Force

    # 立即启动一次
    Start-ScheduledTask -TaskName $TaskName
    Info "计划任务 $TaskName 已注册并启动（开机自启）"
}

# ---------- 配置防火墙 ----------
Step "6/7 配置防火墙放行端口"

# Web 端口
$webRule = Get-NetFirewallRule -DisplayName "Case-Classify Web ($AppPort)" -ErrorAction SilentlyContinue
if (-not $webRule) {
    New-NetFirewallRule -DisplayName "Case-Classify Web ($AppPort)" `
        -Direction Inbound -Protocol TCP -LocalPort $AppPort -Action Allow | Out-Null
    Info "防火墙已放行 Web 端口 $AppPort"
} else {
    Info "防火墙规则已存在：Web 端口 $AppPort"
}

# Ollama 端口
$ollamaRule = Get-NetFirewallRule -DisplayName "Ollama API ($OllamaPort)" -ErrorAction SilentlyContinue
if (-not $ollamaRule) {
    New-NetFirewallRule -DisplayName "Ollama API ($OllamaPort)" `
        -Direction Inbound -Protocol TCP -LocalPort $OllamaPort -Action Allow | Out-Null
    Info "防火墙已放行 Ollama 端口 $OllamaPort"
} else {
    Info "防火墙规则已存在：Ollama 端口 $OllamaPort"
}

# ---------- 完成 ----------
Step "7/7 部署完成"

# 等待服务启动
Start-Sleep -Seconds 3

# 健康检查（等待 Web 服务就绪，最多等 15 秒）
for ($i = 1; $i -le 15; $i++) {
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:$AppPort/" -Method Get -TimeoutSec 3 -ErrorAction Stop
        if ($response.StatusCode -eq 200) {
            Info "✅ Web 服务健康检查通过"
            break
        }
    } catch {
        if ($i -eq 15) {
            Warn "Web 服务可能还在启动中，请稍后访问"
        }
        Start-Sleep -Seconds 1
    }
}

Write-Host ""
Write-Host "========================================================" -ForegroundColor Green
Write-Host "          🎉 部署成功！" -ForegroundColor Green
Write-Host "========================================================" -ForegroundColor Green
Write-Host ""
Write-Host "访问地址："
Write-Host "  本机访问：  http://127.0.0.1:$AppPort" -ForegroundColor Cyan
Write-Host "  局域网访问：http://$lanIP:$AppPort" -ForegroundColor Cyan
Write-Host ""

Write-Host "常用命令（Windows）："
if ($Mode -eq "service") {
    Write-Host "  查看服务状态：Get-Service $ServiceName" -ForegroundColor Yellow
    Write-Host "  重启服务：    Restart-Service $ServiceName" -ForegroundColor Yellow
    Write-Host "  停止服务：    Stop-Service $ServiceName" -ForegroundColor Yellow
    Write-Host "  查看应用日志：Get-Content '$ScriptDir\case-classify.log' -Tail 50 -Wait" -ForegroundColor Yellow
} elseif ($Mode -eq "task") {
    Write-Host "  查看任务状态：Get-ScheduledTask -TaskName $TaskName" -ForegroundColor Yellow
    Write-Host "  启动任务：    Start-ScheduledTask -TaskName $TaskName" -ForegroundColor Yellow
    Write-Host "  停止任务：    Stop-ScheduledTask -TaskName $TaskName" -ForegroundColor Yellow
    Write-Host "  查看应用日志：Get-Content '$ScriptDir\case-classify.log' -Tail 50 -Wait" -ForegroundColor Yellow
} else {
    Write-Host "  手动启动：    python case_classify_agent.py" -ForegroundColor Yellow
}
Write-Host ""
Write-Host "Ollama 相关："
Write-Host "  查看模型列表：ollama list" -ForegroundColor Yellow
Write-Host "  查看 Ollama 服务：Get-Service Ollama" -ForegroundColor Yellow
Write-Host ""
Write-Host "提示：请确保局域网内其他设备能 ping 通 $lanIP" -ForegroundColor Yellow
Write-Host "若无法访问，检查防火墙/云服务器安全组是否放行端口 $AppPort" -ForegroundColor Yellow