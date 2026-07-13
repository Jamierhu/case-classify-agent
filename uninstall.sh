#!/bin/bash
# =====================================================================
# 病例图片分类小工具 - 卸载脚本
# 停止并移除 systemd 服务，保留代码和数据
# =====================================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }

SERVICE_NAME="case-classify"
APP_PORT=8000
OLLAMA_PORT=11434

echo -e "${YELLOW}即将卸载病例分类服务（不会删除代码、数据库和已归档图片）${NC}"
read -p "确认卸载？(y/N): " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "已取消"
    exit 0
fi

# 停止并禁用服务
if systemctl is-active --quiet ${SERVICE_NAME}; then
    sudo systemctl stop ${SERVICE_NAME}
    info "服务已停止"
fi

if systemctl is-enabled --quiet ${SERVICE_NAME} 2>/dev/null; then
    sudo systemctl disable ${SERVICE_NAME}
    info "服务已禁用"
fi

# 删除 service 文件
sudo rm -f /etc/systemd/system/${SERVICE_NAME}.service
sudo systemctl daemon-reload
info "systemd 服务文件已删除"

# 关闭防火墙端口
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

# 询问是否删除日志
read -p "是否删除应用日志 /var/log/${SERVICE_NAME}.log？(y/N): " del_log
if [[ "$del_log" == "y" || "$del_log" == "Y" ]]; then
    sudo rm -f /var/log/${SERVICE_NAME}.log
    info "日志文件已删除"
fi

# 询问是否卸载 Ollama
read -p "是否同时卸载 Ollama？(y/N): " del_ollama
if [[ "$del_ollama" == "y" || "$del_ollama" == "Y" ]]; then
    sudo systemctl stop ollama 2>/dev/null || true
    sudo systemctl disable ollama 2>/dev/null || true
    sudo rm -f /etc/systemd/system/ollama.service
    sudo rm -rf /etc/systemd/system/ollama.service.d
    sudo rm -f /usr/local/bin/ollama
    sudo rm -rf /usr/share/ollama /var/lib/ollama /etc/ollama
    sudo systemctl daemon-reload
    info "Ollama 已卸载（模型数据已清除）"
else
    info "保留 Ollama 安装"
fi

echo ""
info "✅ 卸载完成"
echo "代码、数据库(case_record.db)、归档图片(case_output/) 均已保留"
