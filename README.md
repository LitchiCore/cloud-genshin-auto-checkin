# 云原神每日自动领取与日志管理

一个面向 Linux 服务器的云原神自动化工具：通过 Playwright 保存登录状态，每日访问云原神页面领取免费时长，并提供中文日志网站、邀请码注册、管理员后台和可选的 IPv6 直连维护。

> 本项目是非官方工具，与米哈游无关联。网页结构或服务规则变化可能导致自动化失效。使用前请自行了解并遵守相关服务条款，妥善保护账号数据。

## 功能

- 扫码建立独立账号的浏览器登录 Profile
- 每日自动访问并记录免费时长变化
- 多账号隔离运行
- 连续三次登录失效后自动暂停定时器
- 中文日志网站、邀请码注册和管理员后台
- 管理后台可开启/停止/立即运行账号任务
- 公网 IPv6 变化监控
- 管理后台一键更新 IPv6 直连证书与 Nginx
- systemd、sudoers、Nginx 与安装脚本示例

## 安全说明

`accounts/` 中的浏览器 Profile 等同于登录凭据，`web/users.db` 含网站账号信息，绝对不要公开或分享。仓库默认通过 `.gitignore` 排除这些运行时数据。

网站服务默认只监听回环地址，请通过 HTTPS 反向代理发布。IPv6 更新功能使用固定的 root helper；浏览器不会向 helper 提交 IP，helper 会自行检测本机公网 IPv6。

## 快速开始

### 最小运行

```bash
git clone https://github.com/LitchiCore/cloud-genshin-auto-checkin.git
cd cloud-genshin-auto-checkin
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/playwright install chromium
mkdir -p accounts logs
```

首次添加账号并扫码：

```bash
.venv/bin/python qr_login.py myaccount
```

手动执行一次：

```bash
.venv/bin/python daily_visit.py myaccount
```

启动日志网站：

```bash
python3 web/log_web.py
```

默认端口：

- 日志/登录：`127.0.0.1:8001`
- 管理后台：`127.0.0.1:8003`
- IPv6 更新接口：`127.0.0.1:8005`

详细使用方法见 **[HELP.md](HELP.md)**。

## 推荐的 systemd 部署

仓库里的 systemd 单元默认使用：

- 安装目录：`/opt/cloud-genshin`
- 服务用户：`cloud-genshin`
- 配置文件：`/etc/cloud-genshin/cloud-genshin.env`

推荐：

```bash
sudo git clone https://github.com/LitchiCore/cloud-genshin-auto-checkin.git /opt/cloud-genshin
cd /opt/cloud-genshin
sudo bash deploy/install.sh
```

安装脚本会：

- 创建 `cloud-genshin` 系统用户
- 建立 venv 并安装 Playwright Chromium
- 安装 systemd 单元
- 安装受限 timer / IPv6 root helper
- 安装 sudoers 规则
- 启动日志、管理员、IPv6 Watch 与更新后台

然后编辑：

```bash
sudo nano /etc/cloud-genshin/cloud-genshin.env
```

至少确认：

```text
CLOUD_GENSHIN_PUBLIC_URL=https://你的地址
CLOUD_GENSHIN_PROTECTED_ACCOUNT=admin
CLOUD_GENSHIN_EXPECTED_IPV6=你的当前公网IPv6
```

## 创建第一个管理员

全新安装时执行：

```bash
sudo -u cloud-genshin python3 /opt/cloud-genshin/web/user_admin.py create-admin admin
```

命令会交互式要求输入两次密码，密码使用与网站一致的 PBKDF2-SHA256 方案保存。

查看用户：

```bash
sudo -u cloud-genshin python3 /opt/cloud-genshin/web/user_admin.py list
```

## 账号定时任务

示例 timer 每天 04:10 执行，并增加最多 40 分钟随机延迟：

```bash
sudo systemctl enable --now cloud-genshin@myaccount.timer
systemctl list-timers 'cloud-genshin@*'
```

## Nginx / Tailscale Funnel

`deploy/nginx/` 包含三个模板：

- `cloud-genshin-funnel.conf`：监听 `127.0.0.1:8002`，适合 Tailscale Funnel 反代
- `cloud-genshin-acme.conf`：监听公网 IPv6 TCP 80，仅服务 ACME HTTP-01
- `cloud-genshin-direct.conf`：IPv6 `:8000` HTTPS 直连模板

示例：

```bash
sudo apt install nginx
sudo cp deploy/nginx/cloud-genshin-funnel.conf /etc/nginx/sites-available/cloud-genshin-funnel
sudo cp deploy/nginx/cloud-genshin-acme.conf /etc/nginx/sites-available/cloud-genshin-acme
sudo cp deploy/nginx/cloud-genshin-direct.conf /etc/nginx/sites-available/cloud-genshin-direct
sudo ln -s /etc/nginx/sites-available/cloud-genshin-funnel /etc/nginx/sites-enabled/cloud-genshin-funnel
sudo ln -s /etc/nginx/sites-available/cloud-genshin-acme /etc/nginx/sites-enabled/cloud-genshin-acme
sudo nginx -t && sudo systemctl reload nginx
```

**不要手动启用 `cloud-genshin-direct` 模板。** 第一次成功运行 IPv6 updater 时，它会申请证书、写入真实证书路径并自行启用该站点。

Tailscale Funnel 可指向：

```text
http://127.0.0.1:8002
```

## IPv6 一键更新

依赖：

- Nginx
- Certbot（支持 Let's Encrypt IP short-lived profile）
- 公网 IPv6 TCP 80 可访问
- `/var/lib/letsencrypt/.well-known/acme-challenge/` 可由 Nginx 提供

管理后台检测到公网 IPv6 变化后会显示红色状态框，并提供“一键更新 IPv6 直连”。后台异步调用：

```text
/usr/local/sbin/cloud-genshin-ipv6-update
```

helper 会自行：

1. 检测当前公网 IPv6
2. 用 Certbot webroot 申请新的 IPv6 IP 证书
3. 把 Nginx 直连监听保持为 `[::]:8000 ssl`
4. 更新证书路径并 reload Nginx
5. 通过 `https://[::1]:8000/login` 做本地 HTTPS 自检
6. 写入 `web/ipv6_expected.json` 作为新的 Watch 基准

这样公网 IPv6 前缀变化不会因为 Nginx 绑定旧地址而拖垮 Funnel。

## 防火墙

IPv6 IP 证书 HTTP-01 验证需要 TCP 80。如果使用 UFW：

```bash
sudo ufw allow 80/tcp comment 'LetsEncrypt ACME'
```

`cloud-genshin-acme.conf` 对除 `/.well-known/acme-challenge/` 外的 HTTP 请求全部返回 404。

## 目录说明

- `daily_visit.py`：每日访问与时长检测
- `qr_login.py`：二维码登录及 Profile 初始化
- `run_monitored.py`：健康状态与登录失效熔断
- `web/log_web.py`：用户日志网站
- `web/admin_web.py`：管理员后台
- `web/ipv6_watch.py`：公网 IPv6 变化检测
- `web/ipv6_update_web.py`：异步 IPv6 更新 Web 入口
- `deploy/helpers/`：受限 root helper
- `deploy/systemd/`：systemd 示例
- `deploy/nginx/`：Nginx 模板
- `deploy/sudoers/`：sudoers 模板

## 隐私与备份

备份时请单独加密保存 `accounts/` 与 `web/users.db`，不要公开分享其中内容。

## 许可证

[MIT](LICENSE)
