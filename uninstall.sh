#!/bin/bash
# =====================================================================
# 病例图片分类小工具 - 卸载脚本
# 适用系统：Linux / macOS
# 停止并移除服务（含 Docker 容器），保留代码和数据
#
# 用法：
#   ./uninstall.sh              # 交互式卸载
#   ./uninstall.sh --mode docker  # 仅清理 Docker 容器/镜像
# =====================================================================
set -e

# ---------- 颜色输出 ----------
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---------- 参数解析 ----------
UNINSTALL_MODE="all"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            UNINSTALL_MODE="$2"
            shift 2
            ;;
        --mode=*)
            UNINSTALL_MODE="${1#*=}"
            shift
            ;;
        --help|-h)
            echo "用法：./uninstall.sh [--mode all|docker] [--help]"
            echo ""
            echo "卸载模式："
            echo "  all     清理系统服务 + Docker 容器/镜像（默认）"
            echo "  docker  仅清理 Docker 容器/镜像"
            exit 0
            ;;
        *)
            error "未知参数：$1"
            exit 1
            ;;
    esac
done

# ---------- 系统检测 ----------
detect_os() {
    case "$(uname -s)" in
        Linux*)  echo "Linux";;
        Darwin*) echo "macOS";;
        *)       echo "Unknown";;
    esac
}
OS=$(detect_os)

# macOS: 确保 Homebrew 路径在 PATH 中
if [[ "$OS" == "macOS" ]]; then
    if [[ -f /opt/homebrew/bin/brew ]]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [[ -f /usr/local/bin/brew ]]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
fi

SERVICE_NAME="case-classify"
APP_PORT=8000
OLLAMA_PORT=11434
LAUNCHD_LABEL="com.${SERVICE_NAME}.agent"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
OLLAMA_PLIST="$HOME/Library/LaunchAgents/com.ollama.ollama.plist"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo -e "${YELLOW}即将卸载病例分类服务（不会删除代码、数据库和已归档图片）${NC}"
read -p "确认卸载？(y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "已取消"
    exit 0
fi

# =====================================================================
# Docker 清理
# =====================================================================
cleanup_docker() {
    step_docker() { echo -e "\n${GREEN}========== Docker 清理 ==========${NC}"; }
    step_docker

    if ! command -v docker &>/dev/null; then
        info "未检测到 Docker，跳过 Docker 清理"
        return 0
    fi

    # 检测 docker compose 命令
    if docker compose version &>/dev/null; then
        COMPOSE_CMD="docker compose"
    elif command -v docker-compose &>/dev/null; then
        COMPOSE_CMD="docker-compose"
    else
        COMPOSE_CMD=""
    fi

    # 1. 通过 docker-compose down 停止并移除容器
    if [[ -n "$COMPOSE_CMD" && -f "$SCRIPT_DIR/docker-compose.yml" ]]; then
        info "通过 $COMPOSE_CMD 停止并移除容器..."
        cd "$SCRIPT_DIR"
        $COMPOSE_CMD down --remove-orphans 2>/dev/null || true
        info "Docker Compose 容器已停止并移除"
    else
        # 手动停止容器
        info "手动停止容器..."
        docker stop case-classify 2>/dev/null || true
        docker rm case-classify 2>/dev/null || true
    fi

    # 2. 询问是否删除 Docker 镜像
    read -p "是否删除 Docker 镜像？(y/N): " del_image
    if [[ "$del_image" == "y" || "$del_image" == "Y" ]]; then
        # 删除构建的镜像
        docker rmi case-classify-agent 2>/dev/null || true
        # 清理悬空镜像（<none> 标签）
        docker image prune -f 2>/dev/null || true
        info "Docker 镜像已删除"
    else
        info "保留 Docker 镜像"
    fi

    # 3. 询问是否删除 Docker 数据卷（Ollama 模型数据）
    read -p "是否删除 Ollama 模型数据卷（ollama_data）？删除后需重新拉取模型 (y/N): " del_volume
    if [[ "$del_volume" == "y" || "$del_volume" == "Y" ]]; then
        docker volume rm case-classify_ollama_data 2>/dev/null || true
        info "Ollama 模型数据卷已删除"
    else
        info "保留 Ollama 模型数据卷"
    fi
}

# 执行 Docker 清理
cleanup_docker

# 如果仅清理 Docker，则退出
if [[ "$UNINSTALL_MODE" == "docker" ]]; then
    echo ""
    info "✅ Docker 清理完成"
    exit 0
fi

# =====================================================================
# 系统服务清理
# =====================================================================

# ---------- 停止并移除服务 ----------
if [[ "$OS" == "macOS" ]]; then
    # macOS: 卸载 LaunchAgent
    if launchctl list | grep -q "$LAUNCHD_LABEL"; then
        launchctl unload "$LAUNCHD_PLIST" 2>/dev/null || true
        info "应用 LaunchAgent 已停止"
    fi
    if [[ -f "$LAUNCHD_PLIST" ]]; then
        rm -f "$LAUNCHD_PLIST"
        info "应用 LaunchAgent plist 已删除"
    fi
else
    # Linux: 停止并禁用 systemd 服务
    if systemctl is-active --quiet ${SERVICE_NAME} 2>/dev/null; then
        sudo systemctl stop ${SERVICE_NAME}
        info "系统服务已停止"
    fi
    if systemctl is-enabled --quiet ${SERVICE_NAME} 2>/dev/null; then
        sudo systemctl disable ${SERVICE_NAME}
        info "系统服务已禁用"
    fi
    sudo rm -f /etc/systemd/system/${SERVICE_NAME}.service
    sudo systemctl daemon-reload 2>/dev/null || true
    info "systemd 服务文件已删除"
fi

# ---------- 关闭防火墙端口 ----------
if [[ "$OS" == "macOS" ]]; then
    info "macOS 防火墙是应用级别的，端口规则无需手动移除"
else
    if command -v ufw &>/dev/null; then
        sudo ufw delete allow ${APP_PORT}/tcp 2>/dev/null || true
        sudo ufw delete allow ${OLLAMA_PORT}/tcp 2>/dev/null || true
        info "ufw 端口规则已移除"
    elif command -v firewall-cmd &>/dev/null; then
        sudo firewall-cmd --permanent --remove-port=${APP_PORT}/tcp 2>/dev/null || true
        sudo firewall-cmd --permanent --remove-port=${OLLAMA_PORT}/tcp 2>/dev/null || true
        sudo firewall-cmd --reload 2>/dev/null || true
        info "firewalld 端口规则已移除"
    fi
fi

# ---------- 删除日志 ----------
if [[ "$OS" == "macOS" ]]; then
    if [[ -f /tmp/${SERVICE_NAME}.log ]]; then
        read -p "是否删除应用日志 /tmp/${SERVICE_NAME}.log？(y/N): " del_log
        if [[ "$del_log" == "y" || "$del_log" == "Y" ]]; then
            rm -f /tmp/${SERVICE_NAME}.log
            info "日志文件已删除"
        fi
    fi
else
    read -p "是否删除应用日志 /var/log/${SERVICE_NAME}.log？(y/N): " del_log
    if [[ "$del_log" == "y" || "$del_log" == "Y" ]]; then
        sudo rm -f /var/log/${SERVICE_NAME}.log
        info "日志文件已删除"
    fi
fi

# ---------- 是否卸载 Ollama ----------
read -p "是否同时卸载 Ollama？(y/N): " del_ollama
if [[ "$del_ollama" == "y" || "$del_ollama" == "Y" ]]; then
    if [[ "$OS" == "macOS" ]]; then
        # macOS: 停止并移除 Ollama LaunchAgent + brew uninstall
        launchctl unload "$OLLAMA_PLIST" 2>/dev/null || true
        rm -f "$OLLAMA_PLIST"
        pkill ollama 2>/dev/null || true
        if command -v brew &>/dev/null; then
            brew uninstall ollama 2>/dev/null || true
        fi
        info "Ollama 已卸载（如使用 brew 安装）"
        warn "如使用官方 .app 安装，请手动从「应用程序」拖入废纸篓"
    else
        sudo systemctl stop ollama 2>/dev/null || true
        sudo systemctl disable ollama 2>/dev/null || true
        sudo rm -f /etc/systemd/system/ollama.service
        sudo rm -rf /etc/systemd/system/ollama.service.d
        # 覆盖官方安装脚本可能放置的二进制路径
        sudo rm -f /usr/local/bin/ollama /usr/bin/ollama
        sudo rm -rf /usr/share/ollama /var/lib/ollama /etc/ollama
        sudo systemctl daemon-reload 2>/dev/null || true
        info "Ollama 已卸载（模型数据已清除）"
    fi
else
    info "保留 Ollama 安装"
fi

echo ""
info "✅ 卸载完成"
echo "代码、数据库(case_record.db)、归档图片(case_output/) 均已保留"