#!/bin/bash
# =====================================================================
# 病例图片分类小工具 - 卸载脚本
# 适用系统：Ubuntu / Debian / CentOS / Arch / Alpine / openSUSE 等 Linux 发行版 / macOS
# 停止并移除服务，保留代码和数据
# =====================================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }

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

SERVICE_NAME="case-classify"
APP_PORT=8000
OLLAMA_PORT=11434
LAUNCHD_LABEL="com.${SERVICE_NAME}.agent"
LAUNCHD_PLIST="$HOME/Library/LaunchAgents/${LAUNCHD_LABEL}.plist"
OLLAMA_PLIST="$HOME/Library/LaunchAgents/com.ollama.ollama.plist"

echo -e "${YELLOW}即将卸载病例分类服务（不会删除代码、数据库和已归档图片）${NC}"
read -p "确认卸载？(y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "已取消"
    exit 0
fi

# ---------- 停止并移除服务 ----------
if [[ "$OS" == "macOS" ]]; then
    # macOS: 卸载 LaunchAgent
    if launchctl list | grep -q "$LAUNCHD_LABEL"; then
        launchctl unload "$LAUNCHD_PLIST"
        info "LaunchAgent 已停止"
    fi
    if [[ -f "$LAUNCHD_PLIST" ]]; then
        rm -f "$LAUNCHD_PLIST"
        info "LaunchAgent plist 已删除"
    fi
else
    # Linux: 停止并禁用 systemd 服务
    if systemctl is-active --quiet ${SERVICE_NAME}; then
        sudo systemctl stop ${SERVICE_NAME}
        info "服务已停止"
    fi
    if systemctl is-enabled --quiet ${SERVICE_NAME} 2>/dev/null; then
        sudo systemctl disable ${SERVICE_NAME}
        info "服务已禁用"
    fi
    sudo rm -f /etc/systemd/system/${SERVICE_NAME}.service
    sudo systemctl daemon-reload
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
        sudo systemctl daemon-reload
        info "Ollama 已卸载（模型数据已清除）"
    fi
else
    info "保留 Ollama 安装"
fi

echo ""
info "✅ 卸载完成"
echo "代码、数据库(case_record.db)、归档图片(case_output/) 均已保留"
