#!/usr/bin/env bash
set -euo pipefail

BASE=/opt/cloud-genshin
SERVICE_USER=cloud-genshin
ENV_DIR=/etc/cloud-genshin
ENV_FILE=$ENV_DIR/cloud-genshin.env

if [ "${EUID}" -ne 0 ]; then
  echo "请用 sudo 运行：sudo bash deploy/install.sh"
  exit 1
fi

if [ ! -f "$BASE/requirements.txt" ]; then
  echo "请先把仓库放到 $BASE"
  exit 1
fi

id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/cloud-genshin --shell /usr/sbin/nologin "$SERVICE_USER"

mkdir -p "$BASE/accounts" "$BASE/logs" "$ENV_DIR" /var/lib/letsencrypt/.well-known/acme-challenge
chown -R "$SERVICE_USER:$SERVICE_USER" "$BASE"

if [ ! -f "$ENV_FILE" ]; then
  cp "$BASE/.env.example" "$ENV_FILE"
  chmod 640 "$ENV_FILE"
  echo "已创建 $ENV_FILE；请按实际公网地址修改其中配置。"
fi

sudo -u "$SERVICE_USER" python3 -m venv "$BASE/.venv"
sudo -u "$SERVICE_USER" "$BASE/.venv/bin/pip" install -r "$BASE/requirements.txt"
sudo -u "$SERVICE_USER" "$BASE/.venv/bin/playwright" install chromium

install -m 0755 "$BASE/deploy/helpers/cloud-genshin-timerctl" /usr/local/sbin/cloud-genshin-timerctl
install -m 0755 "$BASE/deploy/helpers/cloud-genshin-ipv6-update" /usr/local/sbin/cloud-genshin-ipv6-update
install -m 0440 "$BASE/deploy/sudoers/cloud-genshin" /etc/sudoers.d/cloud-genshin
visudo -cf /etc/sudoers.d/cloud-genshin >/dev/null

cp "$BASE"/deploy/systemd/*.service /etc/systemd/system/
cp "$BASE"/deploy/systemd/*.timer /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now cloud-genshin-logweb.service cloud-genshin-admin.service cloud-genshin-ipv6-update-web.service
systemctl enable --now cloud-genshin-ipv6-watch.timer

echo
echo "基础服务已安装。"
echo "下一步："
echo "  1) 编辑 $ENV_FILE"
echo "  2) python3 $BASE/web/user_admin.py create-admin admin"
echo "  3) 如需 Nginx/Tailscale/IPv6 直连，按 README 的反向代理章节安装 deploy/nginx 模板"
