#!/bin/bash
# ============================================
# Missav Manager — 一键部署脚本
# JableTV & MissAV M3U8 视频下载器
# ============================================

set -e

REPO_URL="https://github.com/zym20192019/Missav_Manager.git"
INSTALL_DIR="/root/jable-downloader"
SERVICE_NAME="jable-downloader"
DEFAULT_PORT=8025

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

echo ""
echo -e "${BLUE}╔══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   🎬 Missav Manager — 一键部署脚本      ║${NC}"
echo -e "${BLUE}║   JableTV & MissAV 视频下载器            ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════╝${NC}"
echo ""

# ---- 检查 root ----
if [ "$(id -u)" -ne 0 ]; then
    error "请使用 root 用户运行此脚本: sudo bash deploy.sh"
fi

# ---- 检查 Python ----
info "检查 Python 环境..."
if command -v python3 &>/dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
    ok "Python $PYTHON_VERSION 已安装"
else
    error "未找到 python3，请先安装 Python 3.11+"
fi

# ---- 安装/更新项目 ----
if [ -d "$INSTALL_DIR/.git" ]; then
    info "检测到已有安装，更新代码..."
    cd "$INSTALL_DIR"
    git pull --rebase --strategy-option theirs 2>/dev/null || git pull
    ok "代码已更新"
else
    info "克隆项目..."
    git clone "$REPO_URL" "$INSTALL_DIR"
    cd "$INSTALL_DIR"
    ok "项目已克隆到 $INSTALL_DIR"
fi

# ---- 安装依赖 ----
info "安装 Python 依赖..."
pip3 install --break-system-packages -q -r requirements.txt 2>/dev/null || \
pip3 install -q -r requirements.txt 2>/dev/null || \
error "依赖安装失败，请手动运行: pip3 install -r requirements.txt"
ok "依赖安装完成"

# ---- 创建下载目录 ----
mkdir -p "$INSTALL_DIR/downloads"

# ---- 配置端口 ----
PORT="${1:-$DEFAULT_PORT}"
info "使用端口: $PORT"

# ---- 创建 systemd 服务 ----
info "配置 systemd 服务..."
cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=Jable/MissAV M3U8 Downloader - Liquid Glass UI
After=network.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=/usr/bin/python3 -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME} 2>/dev/null
ok "systemd 服务已配置"

# ---- 启动服务 ----
info "启动服务..."
systemctl restart ${SERVICE_NAME}
sleep 2

if systemctl is-active --quiet ${SERVICE_NAME}; then
    ok "服务启动成功"
else
    warn "服务可能未正常启动，查看日志: journalctl -u ${SERVICE_NAME} -n 20"
fi

# ---- 获取 IP ----
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║          ✅ 部署完成！                    ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  访问地址: http://${SERVER_IP}:${PORT}$(printf '%*s' $((15 - ${#SERVER_IP})) '')║${NC}"
echo -e "${GREEN}║  默认账号: admin                         ║${NC}"
echo -e "${GREEN}║  默认密码: jable2026                     ║${NC}"
echo -e "${GREEN}╠══════════════════════════════════════════╣${NC}"
echo -e "${GREEN}║  常用命令:                                ║${NC}"
echo -e "${GREEN}║  查看状态: systemctl status ${SERVICE_NAME}   ║${NC}"
echo -e "${GREEN}║  查看日志: journalctl -u ${SERVICE_NAME} -f  ║${NC}"
echo -e "${GREEN}║  重启服务: systemctl restart ${SERVICE_NAME}  ║${NC}"
echo -e "${GREEN}║  停止服务: systemctl stop ${SERVICE_NAME}     ║${NC}"
echo -e "${GREEN}║  更新版本: cd ${INSTALL_DIR} && git pull ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
