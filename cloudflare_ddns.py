#!/usr/bin/env python3
#   cloudflare_ddns.py
#   Keeps Cloudflare DNS records in sync with your current public IP.

__version__ = "1.1.0"

import collections
import ipaddress
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from string import Template

import requests

CONFIG_PATH = os.environ.get('CONFIG_PATH', os.getcwd())
ENV_VARS = {key: value for (key, value) in os.environ.items() if key.startswith('CF_DDNS_')}


# ── Rate limiter ──────────────────────────────────────────────────────────────

class RateLimiter:
    """Sliding-window rate limiter: at most max_calls within window_seconds."""

    def __init__(self, max_calls=1200, window_seconds=300):
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls = collections.deque()
        self._lock = threading.Lock()

    def _evict(self):
        cutoff = time.monotonic() - self.window
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()

    def acquire(self):
        """Block until a call slot is available, then reserve it."""
        while True:
            with self._lock:
                self._evict()
                if len(self._calls) < self.max_calls:
                    self._calls.append(time.monotonic())
                    return
                wait = self.window - (time.monotonic() - self._calls[0])
            print(f"⏱️  Rate limit reached – waiting {wait:.1f}s")
            time.sleep(max(wait, 0.1))

    def count(self):
        with self._lock:
            self._evict()
            return len(self._calls)


rate_limiter = None   # initialised in __main__; None when imported by Flask


# ── Graceful shutdown ─────────────────────────────────────────────────────────

class GracefulExit:
    def __init__(self):
        self.kill_now = threading.Event()
        signal.signal(signal.SIGINT, self.exit_gracefully)
        signal.signal(signal.SIGTERM, self.exit_gracefully)

    def exit_gracefully(self, signum, frame):
        print("🛑 Stopping main thread…")
        self.kill_now.set()


# ── Status file (written by the daemon, read by Flask) ────────────────────────

def write_status(data):
    path = os.path.join(CONFIG_PATH, "status.json")
    try:
        with open(path, "w") as f:
            json.dump(data, f)
    except Exception as e:
        print(f"⚠️  Could not write status.json: {e}")


# ── Cloudflare API ────────────────────────────────────────────────────────────

def cf_api(endpoint, method, config, headers=None, data=False):
    if headers is None:
        headers = {}
    if rate_limiter:
        rate_limiter.acquire()

    auth = config.get('authentication', {})
    api_token = auth.get('api_token', '')
    if api_token and api_token not in ('', 'api_token_here'):
        headers = {"Authorization": "Bearer " + api_token, **headers}
    else:
        key_cfg = auth.get('api_key', {})
        headers = {
            "X-Auth-Email": key_cfg.get('account_email', ''),
            "X-Auth-Key":   key_cfg.get('api_key', ''),
        }
    try:
        url = "https://api.cloudflare.com/client/v4/" + endpoint
        if data is False:
            response = requests.request(method, url, headers=headers, timeout=15)
        else:
            response = requests.request(method, url, headers=headers, json=data, timeout=15)
        if response.ok:
            return response.json()
        print(f"😡 Error {method} '{response.url}':\n{response.text}")
        return None
    except Exception as e:
        print(f"😡 Exception on {method} '{endpoint}': {e}")
        return None


# ── IP detection ──────────────────────────────────────────────────────────────

# Ordered list of IPv4 detection endpoints.
# Each entry is (url, parser) where parser(response_text) -> str | None.
# Non-Cloudflare sources are listed first to avoid the internal-routing issue
# where requests to 1.1.1.1 from a Cloudflare-networked host return a CF IP.
def _plain(text):
    return text.strip() or None

def _trace(text):
    for line in text.strip().splitlines():
        if line.startswith("ip="):
            return line.split("=", 1)[1].strip()
    return None

IPV4_SOURCES = [
    ("https://checkip.amazonaws.com",      _plain),  # AWS — reliable, non-CF
    ("https://api.ipify.org",              _plain),
    ("https://ipv4.icanhazip.com",         _plain),
    ("https://ip4.seeip.org",             _plain),
    ("https://1.1.1.1/cdn-cgi/trace",     _trace),  # CF last — may return CF IP
    ("https://1.0.0.1/cdn-cgi/trace",     _trace),
]

IPV6_SOURCES = [
    ("https://ipv6.icanhazip.com",                    _plain),
    ("https://[2606:4700:4700::1111]/cdn-cgi/trace",  _trace),
    ("https://[2606:4700:4700::1001]/cdn-cgi/trace",  _trace),
]

def _is_ipv4(addr):
    try:
        return isinstance(ipaddress.ip_address(addr), ipaddress.IPv4Address)
    except ValueError:
        return False

def _is_ipv6(addr):
    try:
        return isinstance(ipaddress.ip_address(addr), ipaddress.IPv6Address)
    except ValueError:
        return False

def _fetch_ip(sources, validator):
    for url, parser in sources:
        try:
            raw = requests.get(url, timeout=5).text
            candidate = parser(raw)
            if candidate and validator(candidate):
                return candidate
        except Exception:
            pass
    return None


def getIPs():
    a = aaaa = None

    if ipv4_enabled:
        a = _fetch_ip(IPV4_SOURCES, _is_ipv4)
        if a is None:
            print("🧩 IPv4 not detected – all sources failed")
            if purgeUnknownRecords:
                deleteEntries("A")

    if ipv6_enabled:
        aaaa = _fetch_ip(IPV6_SOURCES, _is_ipv6)
        if aaaa is None:
            print("🧩 IPv6 not detected – all sources failed")
            if purgeUnknownRecords:
                deleteEntries("AAAA")

    ips = {}
    if a:
        ips["ipv4"] = {"type": "A",    "ip": a}
    if aaaa:
        ips["ipv6"] = {"type": "AAAA", "ip": aaaa}
    return ips


# ── DNS management ────────────────────────────────────────────────────────────

def deleteEntries(record_type):
    for option in config["cloudflare"]:
        answer = cf_api(
            f"zones/{option['zone_id']}/dns_records?per_page=100&type={record_type}",
            "GET", option)
        if answer is None or answer.get("result") is None:
            return
        for record in answer["result"]:
            cf_api(f"zones/{option['zone_id']}/dns_records/{record['id']}",
                   "DELETE", option)
            print("🗑️  Deleted stale record " + record["id"])


def commitRecord(ip):
    for option in config["cloudflare"]:
        response = cf_api("zones/" + option['zone_id'], "GET", option)
        if response is None or not response["result"].get("name"):
            return
        base_domain = response["result"]["name"]

        # Fetch all existing records of this type once per zone per call
        dns_records_resp = cf_api(
            f"zones/{option['zone_id']}/dns_records?per_page=100&type={ip['type']}",
            "GET", option)
        existing = dns_records_resp["result"] if dns_records_resp else []

        for subdomain in option["subdomains"]:
            try:
                name    = subdomain["name"].lower().strip()
                proxied = subdomain["proxied"]
            except (TypeError, KeyError):
                name    = subdomain
                proxied = option.get("proxied", False)

            fqdn = base_domain if name in ('', '@') else f"{name}.{base_domain}"
            record = {
                "type": ip["type"], "name": fqdn,
                "content": ip["ip"], "proxied": proxied, "ttl": ttl,
            }

            identifier    = None
            modified      = False
            duplicate_ids = []
            for r in existing:
                if r["name"] == fqdn:
                    if identifier:
                        # second match → mark as duplicate
                        if r["content"] == ip["ip"]:
                            duplicate_ids.append(identifier)
                            identifier = r["id"]
                        else:
                            duplicate_ids.append(r["id"])
                    else:
                        identifier = r["id"]
                        if r['content'] != record['content'] or r['proxied'] != record['proxied']:
                            modified = True

            if identifier:
                if modified:
                    print(f"📡 Updating record {record}")
                    cf_api(f"zones/{option['zone_id']}/dns_records/{identifier}",
                           "PUT", option, {}, record)
            else:
                print(f"➕ Adding new record {record}")
                cf_api(f"zones/{option['zone_id']}/dns_records",
                       "POST", option, {}, record)

            if purgeUnknownRecords:
                for dup_id in duplicate_ids:
                    print(f"🗑️  Deleting duplicate record {dup_id}")
                    cf_api(f"zones/{option['zone_id']}/dns_records/{dup_id}",
                           "DELETE", option)
    return True


def updateIPs(ips):
    for ip in ips.values():
        commitRecord(ip)


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == '__main__':
    # Module-level globals used by the functions above
    ipv4_enabled      = True
    ipv6_enabled      = True
    purgeUnknownRecords = False
    ttl               = 300
    check_interval    = 5

    if sys.version_info < (3, 5):
        raise Exception("🐍 This script requires Python 3.5+")

    config = None
    try:
        with open(os.path.join(CONFIG_PATH, "config.json")) as f:
            raw = f.read()
            config = json.loads(Template(raw).safe_substitute(ENV_VARS) if ENV_VARS else raw)
    except Exception:
        print("😡 Error reading config.json")
        time.sleep(10)
        sys.exit(1)

    ipv4_enabled        = config.get("a", True)
    ipv6_enabled        = config.get("aaaa", True)
    purgeUnknownRecords = config.get("purgeUnknownRecords", False)
    ttl                 = max(1, int(config.get("ttl", 300)))
    # check_interval: how often to poll current IP (2–30 s). Cloudflare API is
    # only called when the IP actually changes, so this is safe at 2–5 s.
    check_interval      = max(2, min(30, int(config.get("check_interval", 5))))

    # Cloudflare allows 1 200 API calls per 5 minutes per token.
    rate_limiter = RateLimiter(max_calls=1200, window_seconds=300)

    if len(sys.argv) > 1 and sys.argv[1] == "--repeat":
        print(f"🕰️  Polling IP every {check_interval}s – Cloudflare API called only on IP change")
        killer   = GracefulExit()
        prev_ips = {}
        status   = {
            "ipv4": None, "ipv6": None,
            "last_check": None, "last_update": None,
            "api_calls_5min": 0, "check_interval": check_interval,
        }

        while True:
            current_ips = getIPs()
            now = datetime.now(timezone.utc).isoformat()
            status["last_check"]    = now
            status["ipv4"]          = current_ips.get("ipv4", {}).get("ip")
            status["ipv6"]          = current_ips.get("ipv6", {}).get("ip")

            if current_ips != prev_ips:
                if prev_ips:
                    changed = []
                    for k in current_ips:
                        if k not in prev_ips or current_ips[k]["ip"] != prev_ips[k]["ip"]:
                            changed.append(f"{k}={current_ips[k]['ip']}")
                    print(f"🔔 IP changed ({', '.join(changed)}) – updating Cloudflare records…")
                else:
                    print(f"🚀 First run – pushing current IPs to Cloudflare…")
                updateIPs(current_ips)
                prev_ips            = {k: v.copy() for k, v in current_ips.items()}
                status["last_update"] = now

            status["api_calls_5min"] = rate_limiter.count()
            write_status(status)

            if killer.kill_now.wait(check_interval):
                break

    elif len(sys.argv) > 1:
        print(f"❓ Unrecognised parameter '{sys.argv[1]}'. Stopping.")
    else:
        # Single-shot mode
        updateIPs(getIPs())
