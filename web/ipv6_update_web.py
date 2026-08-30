#!/usr/bin/env python3

from http.server import (
    ThreadingHTTPServer,
    BaseHTTPRequestHandler,
)

from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import hashlib
import html
import json
import os
import sqlite3
import subprocess
import time


BASE = Path(__file__).resolve().parent.parent

DB = BASE / "web" / "users.db"

STATUS = (
    BASE
    / "web"
    / "ipv6_update_status.json"
)

JOB_LOG = (
    BASE
    / "web"
    / "ipv6_update_job.log"
)

HELPER = (
    "/usr/local/sbin/"
    "cloud-genshin-ipv6-update"
)

HOST = "127.0.0.1"
PORT = 8005


def esc(value):
    return html.escape(
        str(value),
        quote=True,
    )


def atomic_status(data):
    tmp = STATUS.with_suffix(
        ".json.webtmp"
    )

    tmp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    tmp.replace(STATUS)


def read_status():
    try:
        data = json.loads(
            STATUS.read_text(
                encoding="utf-8"
            )
        )

        if not isinstance(data, dict):
            raise ValueError()

        return data

    except Exception:
        return {
            "status": "unknown",
            "message": "尚无更新状态",
        }


def current_admin(headers):

    raw = headers.get(
        "Cookie"
    )

    if not raw:
        return None

    try:
        cookie = SimpleCookie()
        cookie.load(raw)

        item = cookie.get(
            "cg_session"
        )

        if not item:
            return None

        token_hash = hashlib.sha256(
            item.value.encode()
        ).hexdigest()

        db = sqlite3.connect(DB)

        row = db.execute(
            """
            SELECT
                u.username,
                u.role,
                s.expires

            FROM sessions s

            JOIN users u
              ON u.username = s.username

            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()

        db.close()

        if not row:
            return None

        username, role, expires = row

        if expires < int(time.time()):
            return None

        if role != "admin":
            return None

        return username

    except Exception:
        return None


STYLE = """
<style>

:root {
    color-scheme: dark;
}

body {
    max-width: 850px;
    margin: 36px auto;
    padding: 0 18px;

    background: #111;
    color: #eee;

    font-family:
        system-ui,
        -apple-system,
        sans-serif;
}

a {
    color: #8ab4f8;
}

.card {
    background: #1b1b1b;

    border:
        1px solid #333;

    border-radius: 12px;

    padding: 20px;
}

.status {
    font-size: 18px;
    margin: 16px 0;
}

.ok {
    color: #9be29b;
}

.err {
    color: #ff8c8c;
}

.running {
    color: #ffd580;
}

.small {
    opacity: .7;
    font-size: 13px;
}

pre {
    white-space: pre-wrap;
    word-break: break-word;

    background: #090909;

    padding: 14px;

    border-radius: 9px;

    max-height: 360px;
    overflow: auto;
}

.spinner {
    display: inline-block;

    width: 14px;
    height: 14px;

    border:
        2px solid #666;

    border-top-color:
        #ddd;

    border-radius:
        50%;

    animation:
        spin .8s linear infinite;

    vertical-align:
        -2px;

    margin-right:
        8px;
}

@keyframes spin {
    to {
        transform:
            rotate(360deg);
    }
}

button {
    padding:
        9px 14px;

    cursor:
        pointer;
}

</style>
"""


PROGRESS_BODY = """
<div class="card">

<h1>IPv6 直连更新</h1>

<div
    id="status"
    class="status running"
>
<span class="spinner"></span>
正在启动更新任务……
</div>

<p id="message">
请稍候。
</p>

<div id="details"></div>

<p
    id="admin-link"
    style="display:none"
>
<a href="/admin/">
← 返回管理员后台
</a>
</p>

</div>


<script>

const statusEl =
    document.getElementById("status");

const messageEl =
    document.getElementById("message");

const detailsEl =
    document.getElementById("details");

const adminLink =
    document.getElementById("admin-link");


function escapeHtml(value) {

    const div =
        document.createElement("div");

    div.textContent =
        String(value ?? "");

    return div.innerHTML;
}


async function poll() {

    try {

        const response =
            await fetch(
                "/admin/ipv6/update?status=1",
                {
                    cache: "no-store",
                    credentials: "same-origin"
                }
            );


        if (!response.ok) {

            throw new Error(
                "HTTP "
                + response.status
            );
        }


        const data =
            await response.json();


        const state =
            data.status || "unknown";

        const message =
            data.message || "";


        if (
            state === "queued"
            || state === "running"
            || state === "busy"
        ) {

            statusEl.className =
                "status running";

            statusEl.innerHTML =
                '<span class="spinner"></span>'
                + (
                    state === "queued"
                    ? "更新任务已排队"
                    : "IPv6 直连更新中"
                );

            messageEl.textContent =
                message
                || "后台任务正在运行……";


            let details = "";

            if (data.old_ipv6) {

                details +=
                    "<p class='small'>"
                    + "旧 IPv6："
                    + escapeHtml(
                        data.old_ipv6
                    )
                    + "</p>";
            }


            if (data.new_ipv6) {

                details +=
                    "<p class='small'>"
                    + "当前 IPv6："
                    + escapeHtml(
                        data.new_ipv6
                    )
                    + "</p>";
            }


            detailsEl.innerHTML =
                details;


            setTimeout(
                poll,
                2000
            );

            return;
        }


        if (state === "ok") {

            statusEl.className =
                "status ok";

            statusEl.textContent =
                "✓ IPv6 直连更新完成";

            messageEl.textContent =
                message
                || "更新成功";


            let details = "";


            if (data.new_ipv6) {

                details +=
                    "<p>当前公网 IPv6：</p>"
                    + "<pre>"
                    + escapeHtml(
                        data.new_ipv6
                    )
                    + "</pre>";
            }


            if (data.direct_url) {

                const safeUrl =
                    escapeHtml(
                        data.direct_url
                    );

                details +=
                    "<p>高速直连：</p>"
                    + "<p><a href='"
                    + safeUrl
                    + "'>"
                    + safeUrl
                    + "</a></p>";
            }


            if (data.cert_name) {

                details +=
                    "<p class='small'>证书："
                    + escapeHtml(
                        data.cert_name
                    )
                    + "</p>";
            }


            detailsEl.innerHTML =
                details;

            adminLink.style.display =
                "block";

            return;
        }


        if (state === "error") {

            statusEl.className =
                "status err";

            statusEl.textContent =
                "✗ IPv6 直连更新失败";

            messageEl.textContent =
                "Tailscale Funnel 不受影响。";


            let msg =
                message
                || "后台任务返回错误";


            if (msg.length > 5000) {

                msg =
                    msg.slice(0, 5000)
                    + "\\n\\n……错误信息过长，已截断";
            }


            detailsEl.innerHTML =
                "<pre>"
                + escapeHtml(msg)
                + "</pre>";

            adminLink.style.display =
                "block";

            return;
        }


        statusEl.className =
            "status running";

        statusEl.innerHTML =
            '<span class="spinner"></span>'
            + "等待后台状态";

        messageEl.textContent =
            message
            || "正在等待任务启动……";

        setTimeout(
            poll,
            2000
        );


    } catch (error) {

        statusEl.className =
            "status running";

        statusEl.innerHTML =
            '<span class="spinner"></span>'
            + "正在重新连接";

        messageEl.textContent =
            "状态查询暂时失败："
            + error;

        setTimeout(
            poll,
            3000
        );
    }
}


poll();

</script>
"""


def page(title, body):

    return f"""<!doctype html>

<html lang="zh-CN">

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width,initial-scale=1"
>

<title>{esc(title)}</title>

{STYLE}

</head>

<body>

{body}

</body>

</html>"""


class Handler(
    BaseHTTPRequestHandler
):

    def common_headers(self):

        self.send_header(
            "Cache-Control",
            "no-store",
        )

        self.send_header(
            "X-Frame-Options",
            "DENY",
        )

        self.send_header(
            "X-Content-Type-Options",
            "nosniff",
        )

        self.send_header(
            "Referrer-Policy",
            "no-referrer",
        )


    def send_html(
        self,
        content,
        status=200,
    ):

        data = content.encode(
            "utf-8"
        )

        try:

            self.send_response(
                status
            )

            self.send_header(
                "Content-Type",
                "text/html; charset=utf-8",
            )

            self.common_headers()

            self.send_header(
                "Content-Length",
                str(len(data)),
            )

            self.end_headers()

            self.wfile.write(
                data
            )

        except (
            BrokenPipeError,
            ConnectionResetError,
        ):

            # 客户端提前离开无需报 traceback。
            pass


    def send_json(
        self,
        obj,
        status=200,
    ):

        data = json.dumps(
            obj,
            ensure_ascii=False,
        ).encode(
            "utf-8"
        )

        try:

            self.send_response(
                status
            )

            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8",
            )

            self.common_headers()

            self.send_header(
                "Content-Length",
                str(len(data)),
            )

            self.end_headers()

            self.wfile.write(
                data
            )

        except (
            BrokenPipeError,
            ConnectionResetError,
        ):

            pass


    def redirect(
        self,
        target,
    ):

        self.send_response(303)

        self.send_header(
            "Location",
            target,
        )

        self.common_headers()

        self.end_headers()


    def do_GET(self):

        admin = current_admin(
            self.headers
        )

        if not admin:

            self.redirect(
                "/login"
            )

            return


        parsed = urlparse(
            self.path
        )

        query = parse_qs(
            parsed.query
        )


        if (
            query.get(
                "status",
                [""]
            )[0]
            == "1"
        ):

            data = read_status()

            # 不让一个特别巨大的错误 JSON
            # 每两秒反复通过 Funnel。
            message = str(
                data.get(
                    "message",
                    ""
                )
            )

            if len(message) > 5000:

                data["message"] = (
                    message[:5000]
                    + "\n\n……已截断"
                )

            self.send_json(
                data
            )

            return


        self.redirect(
            "/admin/"
        )


    def do_POST(self):

        admin = current_admin(
            self.headers
        )

        if not admin:

            self.redirect(
                "/login"
            )

            return


        current = read_status()


        # 已经在跑则直接进入进度页，
        # 不启动第二个 helper。
        if current.get(
            "status"
        ) in (
            "queued",
            "running",
        ):

            self.send_html(
                page(
                    "IPv6 更新中",
                    PROGRESS_BODY,
                )
            )

            return


        # POST 立即写 queued，
        # 浏览器一返回就有东西可以轮询。
        atomic_status({
            "status":
                "queued",

            "updated_at":
                time.strftime(
                    "%Y-%m-%dT%H:%M:%S%z"
                ),

            "message":
                "更新任务已启动，等待后台处理",

            "requested_by":
                admin,
        })


        try:

            JOB_LOG.parent.mkdir(
                parents=True,
                exist_ok=True,
            )


            log = open(
                JOB_LOG,
                "ab",
                buffering=0,
            )


            subprocess.Popen(
                [
                    "sudo",
                    "-n",
                    HELPER,
                ],

                stdin=
                    subprocess.DEVNULL,

                stdout=
                    log,

                stderr=
                    subprocess.STDOUT,

                start_new_session=True,

                close_fds=True,
            )


        except Exception as e:

            atomic_status({
                "status":
                    "error",

                "updated_at":
                    time.strftime(
                        "%Y-%m-%dT%H:%M:%S%z"
                    ),

                "message":
                    "无法启动后台更新任务："
                    + str(e),
            })


        # 这里立刻响应。
        # 不再等待 Certbot / Nginx。
        self.send_html(
            page(
                "IPv6 更新中",
                PROGRESS_BODY,
            )
        )


    def log_message(
        self,
        fmt,
        *args,
    ):
        pass


if __name__ == "__main__":

    ThreadingHTTPServer(
        (
            HOST,
            PORT,
        ),
        Handler,
    ).serve_forever()
