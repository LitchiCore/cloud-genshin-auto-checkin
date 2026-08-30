# 使用帮助

本文按当前源码说明账号登录、每日任务、日志网站和常见状态。

## 1. 添加云原神账号

账号名只允许字母、数字、下划线和连字符。

```bash
.venv/bin/python qr_login.py myaccount
```

程序会打开云原神页面并生成二维码：

```text
accounts/myaccount/screenshots/qr.png
```

请在 3 分钟内扫码并在手机端确认。成功后，浏览器登录状态保存在 `accounts/myaccount/profile/`，同时记录当时的免费时长作为比较基线。

浏览器 Profile 等同于登录凭据，请勿公开、复制给他人或放在可被网站直接下载的位置。

## 2. 手动运行一次

```bash
.venv/bin/python daily_visit.py myaccount
```

程序会访问云原神页面、等待每日奖励到账、尝试关闭奖励弹窗、读取“免费时长”，并保存最新完整页面截图：

```text
accounts/myaccount/screenshots/last-page.jpg
```

正常退出码为 `0`。其他退出码：

- `1`：执行异常，例如网络、浏览器或 Playwright 错误
- `2`：页面出现“开始游戏”，判定登录状态可能失效
- `3`：页面可以打开，但无法识别当前页面状态

退出码 `2` 或 `3` 时还会在账号截图目录保存诊断截图。

## 3. 定时任务

示例定时器是 `cloud-genshin@账号名.timer`。启用、停用和立即运行：

```bash
sudo systemctl enable --now cloud-genshin@myaccount.timer
sudo systemctl disable --now cloud-genshin@myaccount.timer
sudo systemctl start cloud-genshin@myaccount.service
```

查看下一次执行时间：

```bash
systemctl list-timers 'cloud-genshin@*'
```

`run_monitored.py` 会维护 `accounts/账号名/health.json`。如果连续三次判定登录失效，它会通过受限的 `cloud-genshin-timerctl` 自动暂停该账号的定时器，避免持续失败。重新扫码成功后，后台流程会尝试恢复先前自动暂停的任务。

## 4. 怎么看日志

### 在网站中查看

启动日志服务：

```bash
python3 web/log_web.py
```

服务监听 `127.0.0.1:8001`。登录后，首页会显示：

- 账号最近一条日志
- 登录健康状态
- systemd 报告的下一次执行时间
- “查看日志”入口

日志详情页展示最近 500 条记录，按“最新到最旧”排序；如果存在 `last-page.jpg` 或 `last-page.png`，页面也会显示上一次完整页面截图。

普通用户只能查看与自己同名的云原神账号；`admin` 角色可以查看全部账号，并能进入管理后台。

登录 Cookie 带有 `Secure` 属性。对外使用时应通过 HTTPS 反向代理访问，不要直接把回环端口暴露到公网。

### 在服务器中查看文本日志

每个账号的摘要日志位于：

```text
logs/账号名.log
```

例如：

```bash
tail -n 50 logs/myaccount.log
tail -f logs/myaccount.log
```

成功记录示例：

```text
2026-08-30 04:25:16 | myaccount | OK | 149→164 min | Δ+15 | 16s
```

字段依次表示执行时间、账号、结果、执行前后免费时长、变化量和耗时。首次成功只有基线，例如 `164 min | baseline`。

常见结果：

- `OK`：成功读取免费时长；`Δ+15` 表示比上次多 15 分钟
- `OK` 且 `Δ0`：页面读取成功，但免费时长没有变化，可能当天已经领取
- `LOGIN_EXPIRED`：登录状态失效，需要重新扫码
- `FAIL(1)`：发生异常，日志末尾会记录异常类型
- `FAIL(3)`：页面状态无法识别，优先查看诊断截图

### 查看完整运行输出

摘要日志不会保存 Playwright 的全部控制台输出。systemd 运行时可查看 journal：

```bash
journalctl -u cloud-genshin@myaccount.service -n 100 --no-pager
journalctl -u cloud-genshin@myaccount.service -f
```

查看日志网站自身状态：

```bash
systemctl status cloud-genshin-logweb --no-pager
journalctl -u cloud-genshin-logweb -n 100 --no-pager
```

## 5. 网站账号与邀请码

网站数据保存在 `web/users.db`。管理员在 `/admin/` 中添加用户后，后台会启动二维码任务。扫码成功会生成一个有效期 24 小时的邀请码；用户打开 `/register`，输入邀请码并设置至少 6 位密码。

已有用户记录也可以用命令重新生成邀请码：

```bash
python3 web/user_admin.py invite myaccount
python3 web/user_admin.py list
```

邀请码只能用于尚未设置密码的用户。用户设置或修改密码后，该用户已有网站 Session 会被清除，需要重新登录。网站登录 Session 默认有效期为 30 天。

## 6. 管理后台

管理员入口是 `/admin/`，默认由 `web/admin_web.py` 监听 `127.0.0.1:8003`。实际部署时通常由反向代理把 `/admin/` 转发到该端口。

管理员可以：

- 添加用户并生成登录二维码
- 查看二维码任务状态和邀请码
- 开启、停止或立即运行账号定时任务
- 查看登录健康状态
- 删除普通用户及其网站账户、Session、日志、Profile、二维码和截图

设置 `CLOUD_GENSHIN_PROTECTED_ACCOUNT` 后，对应账号不会出现在普通管理列表中，也不能通过后台创建或删除。

删除用户是不可恢复操作。后台会先停止其 Timer；停止失败时数据不会继续删除。

## 7. 常见问题

### 页面提示登录失效

重新执行二维码登录：

```bash
.venv/bin/python qr_login.py myaccount
```

扫码完成后，再手动运行一次 `daily_visit.py` 验证。

### 日志显示 `Δ0`

这不是运行失败。它表示成功读取到免费时长，但与上次记录相同，通常是当天已经领取，或奖励尚未更新。

### 网站显示“暂无运行日志”

先确认对应账号至少执行过一次 `daily_visit.py`，并检查 `logs/账号名.log` 是否存在以及服务用户是否有读取权限。

### 网站显示“未启用”或“下一次执行未知”

日志网站通过 `systemctl` 查询 `cloud-genshin@账号名.timer`。请确认定时器已经安装并启用，且网站服务所在系统可以调用 `systemctl`。

### 没有最新页面截图

截图只会在页面完成加载后保存。先查看 journal 中是否存在浏览器启动、超时或文件权限错误。

### Playwright 找不到浏览器

重新安装 Chromium：

```bash
.venv/bin/playwright install chromium
```

部分 Linux 发行版还需要安装浏览器系统依赖，可使用 Playwright 提供的 `install-deps chromium`，或按发行版安装对应依赖。
