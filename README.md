# 云原神每日自动领取与日志管理

一个面向 Linux 服务器的云原神自动化工具：通过 Playwright 保存登录状态，每日访问云原神页面领取免费时长，并提供中文日志网站、邀请码注册、管理员后台。推荐使用 Cloudflare Tunnel 发布网站；IPv6 直连维护可选。

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

网站服务默认只监听回环地址。推荐由 Cloudflare Tunnel 将公开域名转发到本机 Nginx `127.0.0.1:8002`，公网 HTTPS 由 Cloudflare 提供，不需要开放入站端口。IPv6 更新功能使用固定的 root helper；浏览器不会向 helper 提交 IP，helper 会自行检测本机公网 IPv6。

## 快速开始

### 登录防护

日志网站的 `/login` 使用用户名与来源 IP 两层限制：同一用户名在 15 分钟内连续失败 8 次后冷却 5 分钟；同一来源 IP 在 15 分钟内累计失败 30 次后冷却 10 分钟。正常登录会清零该用户名的失败计数，但不会清零来源 IP 的失败预算。冷却期间返回 HTTP 429 和 `Retry-After`，页面统一提示稍后重试。

服务同时最多执行 16 个密码验证。并发请求会先预占名额，避免一起越过失败阈值。内存状态最多保存 4096 个用户名/IP 条目，30 分钟不活动且没有进行中的验证时清理；容量满时拒绝新增条目，保留既有冷却。状态属于单个 Web 进程，重启服务会清空；多进程部署需要共享限流存储。

只有 TCP 对端是回环地址时才信任代理提供的单个、格式有效的 `X-Real-IP`；不使用客户端传来的 `X-Forwarded-For`。反向代理必须覆盖 `X-Real-IP`，且 `8001` 必须保持仅回环监听。缺失或无效的来源头会按代理回环 IP 合并统计，因此公网发布前应确认来源头链路。

认证事件只输出事件类型、结果、来源 IP 和用户名的摘要；不输出密码、邀请码、请求 URL、表单正文或会话 token。既有账号、邀请码、权限和会话存储格式保持兼容。攻击者可能暂时触发目标账号冷却；冷却会自动到期，不永久锁定账号。同一公网 IP 的多个用户会共享 IP 预算。

针对性验证：`python3 -m unittest discover -s tests -v`，使用独立临时数据库测试正常登录、冷却到期、来源隔离、邀请码注册和修改密码。

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
- 启动日志、管理员与 IPv6 更新后台；IPv6 Watch 可按需开启

然后编辑：

```bash
sudo nano /etc/cloud-genshin/cloud-genshin.env
```

至少确认：

```text
CLOUD_GENSHIN_PUBLIC_URL=https://genshin.example.com
CLOUD_GENSHIN_PROTECTED_ACCOUNT=admin
```

将 `genshin.example.com` 替换为你的实际公开域名。不使用 IPv6 监控时让 `CLOUD_GENSHIN_EXPECTED_IPV6` 留空即可。

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

## Nginx / Cloudflare Tunnel

`deploy/nginx/` 包含三个模板：

- `cloud-genshin-origin.conf`：监听 `127.0.0.1:8002`，将请求转发给三个本机 Web 服务
- `cloud-genshin-acme.conf`：可选；监听公网 IPv6 TCP 80，仅服务 ACME HTTP-01
- `cloud-genshin-direct.conf`：可选；IPv6 `:8000` HTTPS 直连模板

示例：

```bash
sudo apt install nginx
sudo cp deploy/nginx/cloud-genshin-origin.conf /etc/nginx/sites-available/cloud-genshin-origin
sudo ln -s /etc/nginx/sites-available/cloud-genshin-origin /etc/nginx/sites-enabled/cloud-genshin-origin
sudo nginx -t && sudo systemctl reload nginx
```

先确认本机入口可访问：

```bash
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8002/login
```

应返回 `200`。随后将域名接入 Cloudflare，在 **Networking → Tunnels** 创建 Cloudflared Tunnel，并在服务器上按 Cloudflare 控制台给出的 Debian 安装命令安装 `cloudflared`，再执行控制台生成的 `sudo cloudflared service install <TOKEN>`。Token 是凭据，不要提交到仓库或聊天。

在 Tunnel 中添加 **Published application**：

```text
Hostname: genshin.example.com
Service URL: http://127.0.0.1:8002
```

Cloudflare 会创建相应 DNS 记录。确认 Tunnel 状态为 Healthy 后，用浏览器访问 `https://genshin.example.com/login` 和 `https://genshin.example.com/admin/`。登录页应正常加载，未登录访问管理后台应跳转到登录页。Tunnel 只需要服务器向外建立连接，不依赖公网 IPv6；不要把源站设为公网 IPv6 或 Tailscale 地址。

## 可选：IPv6 一键更新

只有需要公网 IPv6 `:8000` 直连时才配置这一节。Cloudflare Tunnel 不需要证书更新链路。

先在 `/etc/cloud-genshin/cloud-genshin.env` 中把 `CLOUD_GENSHIN_EXPECTED_IPV6` 设置为当前公网 IPv6，再启用 `sudo systemctl enable --now cloud-genshin-ipv6-watch.timer`。已有 `web/ipv6_expected.json` 时，Watch 会优先使用持久化的基准。

先安装可选的 Nginx 模板：

```bash
sudo cp deploy/nginx/cloud-genshin-acme.conf /etc/nginx/sites-available/cloud-genshin-acme
sudo cp deploy/nginx/cloud-genshin-direct.conf /etc/nginx/sites-available/cloud-genshin-direct
sudo ln -s /etc/nginx/sites-available/cloud-genshin-acme /etc/nginx/sites-enabled/cloud-genshin-acme
sudo nginx -t && sudo systemctl reload nginx
```

**不要手动启用 `cloud-genshin-direct` 模板。** 第一次成功运行 IPv6 updater 时，它会申请证书、写入真实证书路径并自行启用该站点。

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

更新 helper 读取现有 Nginx 站点，只替换 `:8000` 的监听地址与两条证书路径，不从静态模板重生成整份配置。现有访问日志、敏感路径规则、真实 IP 处理和代理头会保留；仓库测试覆盖这些配置的保留行为。部署模板只用于初次安装，已有运维安全配置的站点不要直接用模板覆盖。执行证书更新期间应避免同时编辑同一个站点文件。

公网 IPv6 前缀变化不影响 Cloudflare Tunnel 主入口。

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
