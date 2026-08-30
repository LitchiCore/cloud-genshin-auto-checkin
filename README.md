# 云原神每日自动领取与日志管理

一个面向 Linux 服务器的云原神自动化工具：通过 Playwright 保存登录状态，每日访问云原神页面领取免费时长，并提供中文日志网站和管理员后台。正常领取时，免费时长通常增加 15 分钟，具体结果以页面实际显示为准。

> 本项目是非官方工具，与米哈游无关联。网页结构或服务规则变化可能导致自动化失效。使用前请自行了解并遵守相关服务条款，妥善保护账号数据。

## 功能

- 扫码建立独立账号的浏览器登录 Profile
- 每日自动访问并记录免费时长变化
- 多账号隔离运行
- 连续三次登录失效后自动暂停定时器
- 中文只读日志页面、邀请注册和管理员后台
- 可选的公网 IPv6 变化监控与更新入口
- systemd 服务和定时器示例

## 安全说明

`accounts/` 中的浏览器 Profile 等同于登录凭据，`web/users.db` 含网站账号信息，绝对不要公开或分享。建议网站只监听回环地址，再通过带 TLS 和访问控制的反向代理发布。

## 快速开始

要求：Linux、Python 3.10+、systemd（如需定时运行）。

```bash
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

启动只读日志网站：

```bash
python3 web/log_web.py
```

默认监听 `127.0.0.1:8001`。管理员后台默认监听 `127.0.0.1:8003`。

账号添加、日志查看、状态含义、管理员后台和常见故障请见 **[使用帮助](HELP.md)**。

## systemd 部署

示例单元文件位于 `deploy/systemd/`，默认安装目录为 `/opt/cloud-genshin`。复制项目后，请确认其中的用户、目录和 Python 路径符合你的环境，再安装：

```bash
sudo cp deploy/systemd/cloud-genshin@.service /etc/systemd/system/
sudo cp deploy/systemd/cloud-genshin@.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cloud-genshin@myaccount.timer
```

示例定时器每天 04:10 执行，并增加最多 40 分钟的随机延迟。可按需编辑 `OnCalendar`。

## 网站与配置

可通过环境变量设置：

- `CLOUD_GENSHIN_PUBLIC_URL`：生成邀请链接时使用的公开地址
- `CLOUD_GENSHIN_PROTECTED_ACCOUNT`：禁止从后台删除的账号
- `CLOUD_GENSHIN_EXPECTED_IPV6`：可选 IPv6 监控基准

网站首次运行会创建 SQLite 数据库。可使用：

```bash
python3 web/user_admin.py list
python3 web/user_admin.py invite myaccount
```

## 目录说明

- `daily_visit.py`：每日访问与时长检测
- `qr_login.py`：二维码登录及 Profile 初始化
- `run_monitored.py`：健康状态与登录失效熔断
- `web/log_web.py`：用户日志网站
- `web/admin_web.py`：管理员后台
- `deploy/`：systemd 与受限管理脚本示例

## 隐私与备份

备份时请单独加密保存 `accounts/` 与 `web/users.db`，不要公开分享其中内容。

## 许可证

[MIT](LICENSE)
