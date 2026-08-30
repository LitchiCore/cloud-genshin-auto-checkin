#!/usr/bin/env python3

from pathlib import Path
from datetime import datetime
import ipaddress
import json
import os
import re
import subprocess

BASE = Path(__file__).resolve().parent.parent
STATE = BASE / "web" / "ipv6_status.json"

EXPECTED = os.environ.get("CLOUD_GENSHIN_EXPECTED_IPV6", "2001:db8::1")


def run(*args):
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def get_default_interface():
    r = run("ip", "-6", "route", "show", "default")

    for line in r.stdout.splitlines():
        m = re.search(r"\bdev\s+(\S+)", line)
        if m:
            return m.group(1)

    return None


def get_global_addresses(interface):
    if not interface:
        return []

    r = run(
        "ip", "-o", "-6",
        "addr", "show",
        "dev", interface,
        "scope", "global",
    )

    result = []

    for line in r.stdout.splitlines():
        m = re.search(
            r"\binet6\s+([0-9a-fA-F:]+)/\d+",
            line
        )

        if not m:
            continue

        ip = ipaddress.IPv6Address(
            m.group(1)
        )

        # Linux 的 scope global 也会包含 fc00::/7 ULA。
        # 这里只接受真正可在公网路由的 IPv6。
        if not ip.is_global:
            continue

        address = str(ip)

        result.append({
            "address": address,
            "temporary": " temporary " in f" {line} ",
            "deprecated": " deprecated " in f" {line} ",
            "raw": line,
        })

    return result


def choose_candidate(items):
    expected = ipaddress.IPv6Address(EXPECTED)
    expected_iid = expected.packed[8:]

    # 优先选择非 temporary / deprecated 地址
    stable = [
        x for x in items
        if not x["temporary"]
        and not x["deprecated"]
    ]

    # 如果只是运营商前缀变化，
    # 接口后 64 bit 往往仍然相同。
    for item in stable:
        try:
            addr = ipaddress.IPv6Address(
                item["address"]
            )

            if addr.packed[8:] == expected_iid:
                return item["address"]
        except Exception:
            pass

    if stable:
        return stable[0]["address"]

    if items:
        return items[0]["address"]

    return None


def save(data):
    tmp = STATE.with_suffix(".json.tmp")

    tmp.write_text(
        json.dumps(
            data,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8",
    )

    tmp.replace(STATE)


interface = get_default_interface()
items = get_global_addresses(interface)

addresses = [
    x["address"]
    for x in items
]

expected_normalized = str(
    ipaddress.IPv6Address(EXPECTED)
)

now = (
    datetime.now()
    .astimezone()
    .isoformat(timespec="seconds")
)

if expected_normalized in addresses:
    status = "ok"
    changed = False
    current = expected_normalized
    reason = "configured IPv6 is still present"

elif addresses:
    status = "changed"
    changed = True
    current = choose_candidate(items)
    reason = "configured IPv6 is no longer present"

else:
    status = "unavailable"
    changed = False
    current = None
    reason = "no global IPv6 found on default interface"

data = {
    "status": status,
    "changed": changed,
    "expected_ipv6": expected_normalized,
    "current_ipv6": current,
    "interface": interface,
    "all_global_ipv6": addresses,
    "checked_at": now,
    "reason": reason,
}

save(data)

print(
    json.dumps(
        data,
        ensure_ascii=False,
        indent=2
    )
)
