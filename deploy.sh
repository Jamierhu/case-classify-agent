#!/bin/bash
# =====================================================================
# 病例图片分类小工具 - 一键部署脚本
# 适用系统：Ubuntu / Debian / CentOS / Arch / Alpine / openSUSE 等 Linux 发行版 / macOS
# 功能：
#   1. 安装系统依赖（Python、Ollama）
#   2. 拉取视觉模型 qwen3-vl:8b
#   3. 配置 Ollama 并发/常驻显存等优化项
#   4. 创建 Python 虚拟环境并安装依赖
#   5. 注册服务（Linux: systemd / macOS: launchd），开机自启 + 局域网访问
#   6. 配置防火墙放行端口
# =====================================================================
set -e

# ---------- 颜色输出 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }
step()  { echo -e "\n${BLUE}========== $1 ==========${NC}"; }

# ---------- 系统检测 ----------
detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "Linux";;
        Darwin*) echo "macOS";;
        *)       echo "Unknown";;
    esac
}
OS=$(detect_os)

# macOS: 确保 Homebrew 路径在 PATH 中（Apple Silicon 和 Intel 路径不同）
if [[ "$OS" == "macOS" ]]; then
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -f /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi

# ---------- 变量 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="case-classify"
OLLAMA_MODEL="qwen3-vl:8b"
APP_PORT=8000
OLLAMA_PORT=11434
PYTHON_BIN="${PYTHON_BIN:-python3}"

# macOS LaunchAgent 标签
LAUNCHD_LABEL="com.${SERVICE_NAME}.agent"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
OLLAMA_PLIST="$HOME/Library/LaunchAgents/com.ollama.ollama.plist"

# 检测系统包管理器
detect_pkg_manager() {
    case "$OS" in
        macOS)
            if command -v brew &>/dev/null; then
                echo "brew"
            else
                echo ""
            fi
            ;;
        Linux)
            if command -v apt-get &>/dev/null; then
                echo "apt-get"
            elif command -v dnf &>/dev/null; then
                echo "dnf"
            elif command -v yum &>/dev/null; then
                echo "yum"
            elif command -v pacman &>/dev/null; then
                echo "pacman"
            elif command -v apk &>/dev/null; then
                echo "apk"
            elif command -v zypper &>/dev/null; then
                echo "zypper"
            else
                echo ""
            fi
            ;;
        *)
            echo ""
            ;;
    esac
}

PKG_MGR=$(detect_pkg_manager)

# ---------- 通用函数 ----------

# 等待 Ollama API 就绪（最多等待 max_wait 秒）
wait_for_ollama() {
    local max_wait="${1:-30}"
    info "等待 Ollama 服务就绪..."
    for ((i=1; i<=max_wait; i++)); do
        if curl -s http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
            info "Ollama 服务已就绪"
            return 0
        fi
        if [[ $i -eq $max_wait ]]; then
            if [[ "$OS" == "macOS" ]]; then
                error "Ollama 服务启动超时（${max_wait}秒），请检查日志: /tmp/ollama.log"
            else
                error "Ollama 服务启动超时（${max_wait}秒），请检查日志: sudo journalctl -u ollama -n 50 --no-pager"
            fi
            return 1
        fi
        sleep 1
    done
}

# 获取本机局域网 IP（兼容 macOS 和各 Linux 发行版）
get_lan_ip() {
    local ip=""
    if [[ "$OS" == "macOS" ]]; then
        # macOS: 依次尝试 en0(Wi-Fi)、en1(以太网)、ifconfig 兜底
        ip=$(ipconfig getifaddr en0 2>/dev/null)
        if [[ -z "$ip" ]]; then
            ip=$(ipconfig getifaddr en1 2>/dev/null)
        fi
        if [[ -z "$ip" ]]; then
            ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
        fi
    else
        # Linux: hostname -I → ip addr → ifconfig 逐级兜底
        ip=$(hostname -I 2>/dev/null | awk '{print $1}')
        if [[ -z "$ip" ]]; then
            ip=$(ip -4 addr 2>/dev/null | grep -oE 'inet [0-9]+\.[0-9]+\.[0-9]+\.[0-9]+' | grep -v '127.0.0.1' | awk '{print $2}' | head -1)
        fi
        if [[ -z "$ip" ]]; then
            ip=$(ifconfig 2>/dev/null | grep 'inet ' | grep -v 127.0.0.1 | awk '{print $2}' | head -1)
        fi
    fi
    echo "$ip"
}

# ---------- 前置检查 ----------
step "1/7 环境检查"

info "检测到系统：$OS"

if [[ -z "$PKG_MGR" ]]; then
    if [[ "$OS" == "macOS" ]]; then
        error "未检测到 Homebrew，请先安装：https://brew.sh"
    else
        error "未识别到支持的包管理器（apt/dnf/yum/pacman/apk/zypper），请手动安装依赖"
    fi
    exit 1
fi
info "检测到包管理器：$PKG_MGR"

# 检测 GPU（影响 Ollama 性能）
if [[ "$OS" == "macOS" ]]; then
    info "macOS 系统，Ollama 将使用 Metal GPU 加速"
elif command -v nvidia-smi &>/dev/null; then
    info "检测到 NVIDIA GPU，Ollama 将使用 GPU 加速"
else
    warn "未检测到 NVIDIA GPU，Ollama 将使用 CPU 推理（速度较慢，建议有 GPU 环境）"
fi

# 获取本机局域网 IP
LAN_IP=$(get_lan_ip)
if [[ -z "$LAN_IP" ]]; then
    LAN_IP="127.0.0.1"
    warn "无法自动获取局域网 IP，默认使用 127.0.0.1"
else
    info "本机局域网 IP：$LAN_IP"
fi

# 检查 Python 版本（需要 3.8+）
if ! $PYTHON_BIN -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
    error "Python 版本过低，需要 3.8+，当前版本：$($PYTHON_BIN --version 2>&1 || echo '未知')"
    exit 1
fi
info "Python 版本：$($PYTHON_BIN --version 2>&1)"

# ---------- 安装系统依赖 ----------
step "2/7 安装系统依赖（Python、curl、防火墙）"

install_pkgs() {
    case "$PKG_MGR" in
        brew)
            # macOS: 确保 Python 和相关工具已安装
            brew install python curl 2>/dev/null || true
            ;;
        apt-get)
            sudo apt-get update -y
            sudo apt-get install -y python3 python3-pip python3-venv curl ufw
            ;;
        dnf)
            sudo dnf install -y python3 python3-pip curl firewalld
            ;;
        yum)
            sudo yum install -y python3 python3-pip curl firewalld
            ;;
        pacman)
            sudo pacman -Sy --noconfirm python python-pip curl ufw
            ;;
        apk)
            # Alpine: 需要额外安装 py3-virtualenv 以支持 python3 -m venv
            sudo apk add --no-cache python3 py3-pip py3-virtualenv curl
            ;;
        zypper)
            sudo zypper install -y python3 python3-pip curl firewalld
            ;;
    esac
}
install_pkgs
info "系统依赖安装完成"

# ---------- 安装 Ollama ----------
step "3/7 安装 Ollama 并拉取模型"

if command -v ollama &>/dev/null; then
    info "Ollama 已安装，版本：$(ollama --version 2>/dev/null || echo '未知')"
else
    info "正在安装 Ollama..."
    if [[ "$OS" == "macOS" ]]; then
        brew install ollama
    else
        curl -fsSL https://ollama.com/install.sh | sh
    fi
    info "Ollama 安装完成"
fi

# 启动 Ollama 服务
if [[ "$OS" == "macOS" ]]; then
    # macOS: Ollama 将在步骤 3.1 通过 LaunchAgent 统一启动
    # （避免 nohup 临时进程与 LaunchAgent 同时运行导致端口冲突）
    info "Ollama 将通过 LaunchAgent 启动（见下一步配置）"
else
    # Linux: 使用 systemd 管理
    # 修复：部分环境下 ollama 用户的家目录（如 /usr/share/ollama）可能缺失，
    # 导致 ollama serve 启动时报错 "could not create directory ... permission denied"。
    # 这里在启动前确保家目录存在且归属正确。
    if id ollama &>/dev/null; then
        OLLAMA_HOME="$(getent passwd ollama | cut -d: -f6)"
        if [[ -n "$OLLAMA_HOME" && ! -d "$OLLAMA_HOME" ]]; then
            info "创建 Ollama 服务用户家目录：$OLLAMA_HOME"
            sudo mkdir -p "$OLLAMA_HOME"
            sudo chown -R ollama:ollama "$OLLAMA_HOME"
        fi
    fi

    if ! systemctl is-active --quiet ollama; then
        sudo systemctl start ollama
    fi
    sudo systemctl enable ollama
    info "Ollama 服务已启动并设置开机自启"
fi

# 配置 Ollama 优化项
step "3.1 配置 Ollama 性能优化（并发=4, 常驻显存, FlashAttention）"

if [[ "$OS" == "Linux" ]]; then
    OLLAMA_OVERRIDE_DIR="/etc/systemd/system/ollama.service.d"
    sudo mkdir -p "$OLLAMA_OVERRIDE_DIR"

    sudo tee "$OLLAMA_OVERRIDE_DIR/override.conf" > /dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_FLASH_ATTENTION=1"
EOF

    sudo systemctl daemon-reload
    sudo systemctl restart ollama

    # 等待 Ollama API 真正就绪
    wait_for_ollama 30 || exit 1
else
    # macOS: 停止可能残留的 Ollama 进程（避免端口冲突）
    pkill ollama 2>/dev/null || true
    sleep 1

    # 创建 LaunchAgent plist 实现开机自启 + 环境变量
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$OLLAMA_PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.ollama.ollama</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which ollama)</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OLLAMA_HOST</key>
        <string>0.0.0.0:11434</string>
        <key>OLLAMA_NUM_PARALLEL</key>
        <string>4</string>
        <key>OLLAMA_KEEP_ALIVE</key>
        <string>-1</string>
        <key>OLLAMA_FLASH_ATTENTION</key>
        <string>1</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/ollama.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/ollama.log</string>
</dict>
</plist>
PLISTEOF

    # 先卸载再加载（确保用最新配置，兼容重复执行）
    launchctl unload "$OLLAMA_PLIST" 2>/dev/null || true
    launchctl load "$OLLAMA_PLIST"
    info "Ollama LaunchAgent 已配置（开机自启 + 性能优化）"

    # 等待 Ollama API 就绪
    wait_for_ollama 30 || exit 1
fi

# 拉取视觉模型
step "3.2 拉取视觉模型 $OLLAMA_MODEL（首次拉取体积较大，请耐心等待）"
if ollama list 2>/dev/null | grep -q "$OLLAMA_MODEL"; then
    info "模型 $OLLAMA_MODEL 已存在，跳过拉取"
else
    ollama pull "$OLLAMA_MODEL"
    info "模型 $OLLAMA_MODEL 拉取完成"
fi

# ---------- 创建 Python 虚拟环境 ----------
step "4/7 创建 Python 虚拟环境并安装依赖"

VENV_DIR="$SCRIPT_DIR/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "创建虚拟环境：$VENV_DIR"
    $PYTHON_BIN -m venv "$VENV_DIR"
fi

# 激活虚拟环境
source "$VENV_DIR/bin/activate"

# 升级 pip
pip install --upgrade pip -q

# 生成/更新 requirements.txt
cat > "$SCRIPT_DIR/requirements.txt" <<'EOF'
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
python-multipart>=0.0.9
requests>=2.31.0
Pillow>=10.0.0
opencv-python-headless>=4.8.0
numpy>=1.24.0
EOF

info "安装 Python 依赖..."
pip install -r "$SCRIPT_DIR/requirements.txt" -q
info "Python 依赖安装完成"

# ---------- 注册服务 ----------
step "5/7 注册服务（开机自启 + 崩溃自动重启）"

if [[ "$OS" == "macOS" ]]; then
    # macOS: 使用 LaunchAgent plist
    mkdir -p "$HOME/Library/LaunchAgents"
    cat > "$LAUNCHD_PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>${LAUNCHD_LABEL}</string>
    <key>ProgramArguments</key>
    <array>
        <string>${VENV_DIR}/bin/uvicorn</string>
        <string>case_classify_agent:app</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>${APP_PORT}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${SCRIPT_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/${SERVICE_NAME}.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/${SERVICE_NAME}.log</string>
</dict>
</plist>
PLISTEOF

    # 先卸载再加载（确保用最新配置，兼容重复执行）
    launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
    launchctl load "$LAUNCHD_PLIST"
    info "LaunchAgent 已注册并启动：$LAUNCHD_PLIST"

else
    # Linux: 使用 systemd
    RUN_USER="${SUDO_USER:-$USER}"
    RUN_USER_GROUP="$(id -gn "$RUN_USER")"

    sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Case Classify Agent (FastAPI + Ollama)
After=network.target ollama.service
Requires=ollama.service

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_USER_GROUP}
WorkingDirectory=${SCRIPT_DIR}
ExecStart=${VENV_DIR}/bin/uvicorn case_classify_agent:app --host 0.0.0.0 --port ${APP_PORT}
Restart=always
RestartSec=5
StandardOutput=append:/var/log/${SERVICE_NAME}.log
StandardError=append:/var/log/${SERVICE_NAME}.log

[Install]
WantedBy=multi-user.target
EOF

    # 创建日志文件并授权
    sudo touch /var/log/${SERVICE_NAME}.log
    sudo chown "$RUN_USER":"$RUN_USER_GROUP" /var/log/${SERVICE_NAME}.log

    sudo systemctl daemon-reload
    sudo systemctl enable ${SERVICE_NAME}
    sudo systemctl restart ${SERVICE_NAME}
    info "服务 $SERVICE_NAME 已注册并启动"
fi

# ---------- 配置防火墙 ----------
step "6/7 配置防火墙放行端口"

if [[ "$OS" == "macOS" ]]; then
    # macOS 防火墙是应用级别的，不需要额外放行端口
    # 如果系统防火墙已开启，提示用户手动允许 Python
    if /usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate 2>/dev/null | grep -qi "on"; then
        warn "macOS 防火墙已开启，如无法访问请到「系统设置 → 网络 → 防火墙」放行 Python"
    else
        info "macOS 防火墙未开启，无需放行端口"
    fi
else
    if command -v ufw &>/dev/null; then
        sudo ufw allow ${APP_PORT}/tcp
        sudo ufw allow ${OLLAMA_PORT}/tcp
        info "ufw 已放行端口 $APP_PORT (Web) 和 $OLLAMA_PORT (Ollama)"
    elif command -v firewall-cmd &>/dev/null; then
        sudo systemctl start firewalld 2>/dev/null || true
        sudo firewall-cmd --permanent --add-port=${APP_PORT}/tcp
        sudo firewall-cmd --permanent --add-port=${OLLAMA_PORT}/tcp
        sudo firewall-cmd --reload
        info "firewalld 已放行端口 $APP_PORT (Web) 和 $OLLAMA_PORT (Ollama)"
    else
        warn "未检测到 ufw/firewalld，请手动放行端口 $APP_PORT 和 $OLLAMA_PORT"
    fi
fi

# ---------- 完成 ----------
step "7/7 部署完成"

# 等待服务启动
sleep 3

# 检查服务状态
if [[ "$OS" == "macOS" ]]; then
    if launchctl list | grep -q "$LAUNCHD_LABEL"; then
        info "✅ 服务运行正常"
    else
        error "❌ 服务启动失败，请查看日志：cat /tmp/${SERVICE_NAME}.log"
        exit 1
    fi
else
    if systemctl is-active --quiet ${SERVICE_NAME}; then
        info "✅ 服务运行正常"
    else
        error "❌ 服务启动失败，请查看日志：sudo journalctl -u ${SERVICE_NAME} -f"
        exit 1
    fi
fi

# 健康检查（等待 Web 服务就绪，最多等 15 秒）
for ((i=1; i<=15; i++)); do
    if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${APP_PORT}/" 2>/dev/null | grep -q "200"; then
        info "✅ Web 服务健康检查通过"
        break
    fi
    if [[ $i -eq 15 ]]; then
        warn "Web 服务可能还在启动中，请稍后访问"
    fi
    sleep 1
done

echo ""
echo -e "${GREEN}========================================================${NC}"
echo -e "${GREEN}          🎉 部署成功！${NC}"
echo -e "${GREEN}========================================================${NC}"
echo ""
echo -e "访问地址："
echo -e "  本机访问：  ${BLUE}http://127.0.0.1:${APP_PORT}${NC}"
echo -e "  局域网访问：${BLUE}http://${LAN_IP}:${APP_PORT}${NC}"
echo ""

if [[ "$OS" == "macOS" ]]; then
    echo -e "常用命令（macOS）："
    echo -e "  查看服务状态：${YELLOW}launchctl list | grep ${LAUNCHD_LABEL}${NC}"
    echo -e "  停止服务：    ${YELLOW}launchctl unload ${LAUNCHD_PLIST}${NC}"
    echo -e "  启动服务：    ${YELLOW}launchctl load ${LAUNCHD_PLIST}${NC}"
    echo -e "  查看应用日志：${YELLOW}tail -f /tmp/${SERVICE_NAME}.log${NC}"
else
    echo -e "常用命令（Linux）："
    echo -e "  查看服务状态：${YELLOW}sudo systemctl status ${SERVICE_NAME}${NC}"
    echo -e "  查看实时日志：${YELLOW}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
    echo -e "  重启服务：    ${YELLOW}sudo systemctl restart ${SERVICE_NAME}${NC}"
    echo -e "  停止服务：    ${YELLOW}sudo systemctl stop ${SERVICE_NAME}${NC}"
    echo -e "  查看应用日志：${YELLOW}tail -f /var/log/${SERVICE_NAME}.log${NC}"
fi

echo ""
echo -e "Ollama 相关："
echo -e "  查看模型列表：${YELLOW}ollama list${NC}"
if [[ "$OS" == "macOS" ]]; then
    echo -e "  查看 Ollama 状态：${YELLOW}launchctl list | grep ollama${NC}"
    echo -e "  查看 Ollama 日志：${YELLOW}tail -f /tmp/ollama.log${NC}"
else
    echo -e "  查看 Ollama 状态：${YELLOW}sudo systemctl status ollama${NC}"
fi
echo ""
echo -e "${YELLOW}提示：请确保局域网内其他设备能 ping 通 ${LAN_IP}${NC}"
echo -e "${YELLOW}若无法访问，检查防火墙/云服务器安全组是否放行端口 ${APP_PORT}${NC}"