#!/bin/bash
# =====================================================================
# 病例图片分类小工具 - 一键部署脚本
# 适用系统：Ubuntu / Debian / 其他 Linux 发行版
# 功能：
#   1. 安装系统依赖（Python、Ollama）
#   2. 拉取视觉模型 qwen3-vl:8b
#   3. 配置 Ollama 并发/常驻显存等优化项
#   4. 创建 Python 虚拟环境并安装依赖
#   5. 注册 systemd 服务，开机自启 + 局域网访问
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

# ---------- 变量 ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_NAME="case-classify"
OLLAMA_MODEL="qwen3-vl:8b"
APP_PORT=8000
OLLAMA_PORT=11434
PYTHON_BIN="${PYTHON_BIN:-python3}"

# 检测系统包管理器
detect_pkg_manager() {
    if command -v apt-get &>/dev/null; then
        echo "apt-get"
    elif command -v dnf &>/dev/null; then
        echo "dnf"
    elif command -v yum &>/dev/null; then
        echo "yum"
    elif command -v pacman &>/dev/null; then
        echo "pacman"
    else
        echo ""
    fi
}

PKG_MGR=$(detect_pkg_manager)

# ---------- 前置检查 ----------
step "1/7 环境检查"

if [[ "$EUID" -eq 0 ]]; then
    warn "建议以普通用户身份运行（脚本会在需要时通过 sudo 提权），当前为 root 也可继续"
fi

if [[ -z "$PKG_MGR" ]]; then
    error "未识别到支持的包管理器（apt/dnf/yum/pacman），请手动安装依赖"
    exit 1
fi
info "检测到包管理器：$PKG_MGR"

# 检测是否有 NVIDIA GPU（影响 Ollama 性能，非必需）
if command -v nvidia-smi &>/dev/null; then
    info "检测到 NVIDIA GPU，Ollama 将使用 GPU 加速"
else
    warn "未检测到 NVIDIA GPU，Ollama 将使用 CPU 推理（速度较慢，建议有 GPU 环境）"
fi

# 获取本机局域网 IP
LAN_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [[ -z "$LAN_IP" ]]; then
    LAN_IP="127.0.0.1"
    warn "无法自动获取局域网 IP，默认使用 127.0.0.1"
else
    info "本机局域网 IP：$LAN_IP"
fi

# ---------- 安装系统依赖 ----------
step "2/7 安装系统依赖（Python、curl、防火墙）"

install_pkgs() {
    case "$PKG_MGR" in
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
    curl -fsSL https://ollama.com/install.sh | sh
    info "Ollama 安装完成"
fi

# 启动 Ollama 服务（确保在拉取模型前已运行）
if ! systemctl is-active --quiet ollama; then
    sudo systemctl start ollama
    sleep 3
fi
sudo systemctl enable ollama
info "Ollama 服务已启动并设置开机自启"

# 配置 Ollama 优化项：并发推理 + 模型常驻显存 + Flash Attention
step "3.1 配置 Ollama 性能优化"

OLLAMA_OVERRIDE_DIR="/etc/systemd/system/ollama.service.d"
sudo mkdir -p "$OLLAMA_OVERRIDE_DIR"

# 监听所有网卡，允许局域网访问 Ollama API（如需）
# OLLAMA_NUM_PARALLEL=4 并发推理；OLLAMA_KEEP_ALIVE=-1 模型常驻；OLLAMA_FLASH_ATTENTION=1 加速
sudo tee "$OLLAMA_OVERRIDE_DIR/override.conf" > /dev/null <<'EOF'
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_KEEP_ALIVE=-1"
Environment="OLLAMA_FLASH_ATTENTION=1"
EOF

sudo systemctl daemon-reload
sudo systemctl restart ollama
sleep 3
info "Ollama 性能优化已配置（并发=4, 常驻显存, FlashAttention）"

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

# ---------- 注册 systemd 服务 ----------
step "5/7 注册 systemd 服务（开机自启 + 崩溃自动重启）"

# 获取当前用户（用于服务运行身份）
RUN_USER="${SUDO_USER:-$USER}"
RUN_USER_GROUP="$(id -gn "$RUN_USER")"

# 写入 systemd 服务文件
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

# ---------- 配置防火墙 ----------
step "6/7 配置防火墙放行端口"

open_firewall() {
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
}
open_firewall

# ---------- 完成 ----------
step "7/7 部署完成"

# 等待服务启动
sleep 3

# 检查服务状态
if systemctl is-active --quiet ${SERVICE_NAME}; then
    info "✅ 服务运行正常"
else
    error "❌ 服务启动失败，请查看日志：sudo journalctl -u ${SERVICE_NAME} -f"
    exit 1
fi

# 健康检查
if curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${APP_PORT}/" | grep -q "200"; then
    info "✅ Web 服务健康检查通过"
else
    warn "Web 服务可能还在启动中，请稍后访问"
fi

echo ""
echo -e "${GREEN}========================================================${NC}"
echo -e "${GREEN}          🎉 部署成功！${NC}"
echo -e "${GREEN}========================================================${NC}"
echo ""
echo -e "访问地址："
echo -e "  本机访问：  ${BLUE}http://127.0.0.1:${APP_PORT}${NC}"
echo -e "  局域网访问：${BLUE}http://${LAN_IP}:${APP_PORT}${NC}"
echo ""
echo -e "常用命令："
echo -e "  查看服务状态：${YELLOW}sudo systemctl status ${SERVICE_NAME}${NC}"
echo -e "  查看实时日志：${YELLOW}sudo journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "  重启服务：    ${YELLOW}sudo systemctl restart ${SERVICE_NAME}${NC}"
echo -e "  停止服务：    ${YELLOW}sudo systemctl stop ${SERVICE_NAME}${NC}"
echo -e "  查看应用日志：${YELLOW}tail -f /var/log/${SERVICE_NAME}.log${NC}"
echo ""
echo -e "Ollama 相关："
echo -e "  查看模型列表：${YELLOW}ollama list${NC}"
echo -e "  查看 Ollama 状态：${YELLOW}sudo systemctl status ollama${NC}"
echo ""
echo -e "${YELLOW}提示：请确保局域网内其他设备能 ping 通 ${LAN_IP}${NC}"
echo -e "${YELLOW}若无法访问，检查防火墙/云服务器安全组是否放行端口 ${APP_PORT}${NC}"
