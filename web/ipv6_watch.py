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
EXPECTED_STATE = BASE / "web" / "ipv6_expected.json"
DEFAULT_EXPECTED = os.environ.get("CLOUD_GENSHIN_EXPECTED_IPV6", "2001:db8::1")


def load_expected():
    try:
        data = json.loads(EXPECTED_STATE.read_text(encoding="utf-8"))
        value = data.get("expected_ipv6")
        if value:
            return str(ipaddress.IPv6Address(value))
    except Exception:
        pass
    return str(ipaddress.IPv6Address(DEFAULT_EXPECTED))


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, timeout=10, check=False)


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
    r = run("ip", "-o", "-6", "addr", "show", "dev", interface, "scope", "global")
    result = []
    for line in r.stdout.splitlines():
        m = re.search(r"\binet6\s+([0-9a-fA-F:]+)/\d+", line)
        if not m:
            continue
        ip = ipaddress.IPv6Address(m.group(1))
        if not ip.is_global:
            continue
        result.append({
            "address": str(ip),
            "temporary": " temporary " in f" {line} ",
            "deprecated": " deprecated " in f" {line} ",
            "raw": line,
        })
    return result


def choose_candidate(items, expected):
    expected_iid = ipaddress.IPv6Address(expected).packed[8:]
    stable = [x for x in items if not x["temporary"] and not x["deprecated"]]
    for item in stable:
        try:
            addr = ipaddress.IPv6Address(item["address"])
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
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


expected = load_expected()
interface = get_default_interface()
items = get_global_addresses(interface)
addresses = [x["address"] for x in items]
now = datetime.now().astimezone().isoformat(timespec="seconds")

if expected in addresses:
    status = "ok"
    changed = False
    current = expected
    reason = "configured IPv6 is still present"
elif addresses:
    status = "changed"
    changed = True
    current = choose_candidate(items, expected)
    reason = "configured IPv6 is no longer present"
else:
    status = "unavailable"
    changed = False
    current = None
    reason = "no global IPv6 found on default interface"

data = {
    "status": status,
    "changed": changed,
    "expected_ipv6": expected,
    "current_ipv6": current,
    "interface": interface,
    "all_global_ipv6": addresses,
    "checked_at": now,
    "reason": reason,
}

save(data)
print(json.dumps(data, ensure_ascii=False, indent=2))
