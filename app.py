import json
import os
import subprocess

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

import cloudflare_ddns

CONFIG_FILE = os.environ.get("CONFIG_FILE", "/config.json")
STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(CONFIG_FILE)), "status.json")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "please-change-me")


# ── Config helpers ────────────────────────────────────────────────────────────

def load_config():
    try:
        with open(CONFIG_FILE) as f:
            cfg = json.load(f)
    except FileNotFoundError:
        flash(f"config.json not found at {CONFIG_FILE}", "error")
        cfg = {}
    except json.JSONDecodeError as e:
        flash(f"Failed to parse config.json: {e}", "error")
        cfg = {}

    if not cfg.get("cloudflare"):
        cfg["cloudflare"] = [{
            "authentication": {
                "api_token": "",
                "api_key": {"account_email": "", "api_key": ""},
            },
            "zone_id": "",
            "subdomains": [],
        }]
    cfg.setdefault("a", True)
    cfg.setdefault("aaaa", True)
    cfg.setdefault("purgeUnknownRecords", False)
    cfg.setdefault("ttl", 300)
    cfg.setdefault("check_interval", 5)
    return cfg


def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def load_status():
    try:
        with open(STATUS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _norm_subs(subs):
    """Normalise subdomains so templates always see dicts."""
    return [s if isinstance(s, dict) else {"name": s, "proxied": False} for s in subs]


# ── Cloudflare helpers ────────────────────────────────────────────────────────

def fetch_dns_records(cf0):
    recs = []
    for typ in ("A", "AAAA"):
        answer = cloudflare_ddns.cf_api(
            f"zones/{cf0['zone_id']}/dns_records?per_page=100&type={typ}",
            "GET", cf0)
        if answer and answer.get("result"):
            recs += answer["result"]
    return recs


def get_base_domain(cf0):
    resp = cloudflare_ddns.cf_api(f"zones/{cf0['zone_id']}", "GET", cf0)
    if resp and resp.get("result"):
        return resp["result"].get("name", "")
    return ""


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    cfg     = load_config()
    cf0     = cfg["cloudflare"][0]
    records = fetch_dns_records(cf0)
    status  = load_status()
    return render_template(
        "index.html",
        zone    = cf0["zone_id"],
        subs    = _norm_subs(cf0["subdomains"]),
        records = records,
        status  = status,
        cfg     = cfg,
    )


@app.route("/add-subdomain", methods=["POST"])
def add_subdomain():
    new     = request.form.get("new_subdomain", "").strip()
    proxied = request.form.get("proxied") == "on"
    if not new:
        flash("Subdomain cannot be empty.", "error")
    else:
        cfg   = load_config()
        cf0   = cfg["cloudflare"][0]
        names = [s["name"] if isinstance(s, dict) else s for s in cf0["subdomains"]]
        if new in names:
            flash(f"'{new}' is already configured.", "error")
        else:
            cf0["subdomains"].append({"name": new, "proxied": proxied})
            save_config(cfg)
            flash(f"Added '{new}'" + (" (proxied)" if proxied else "") + ".", "success")
    return redirect(url_for("index"))


@app.route("/delete-subdomain/<int:idx>", methods=["POST"])
def delete_subdomain(idx):
    cfg  = load_config()
    subs = cfg["cloudflare"][0]["subdomains"]
    if 0 <= idx < len(subs):
        removed = subs.pop(idx)
        save_config(cfg)
        name = removed["name"] if isinstance(removed, dict) else removed
        flash(f"Removed '{name}'.", "success")
    else:
        flash("Invalid subdomain index.", "error")
    return redirect(url_for("index"))


@app.route("/sync-from-cloudflare", methods=["POST"])
def sync_from_cloudflare():
    """Import all existing A/AAAA records from Cloudflare as managed subdomains."""
    cfg  = load_config()
    cf0  = cfg["cloudflare"][0]
    base = get_base_domain(cf0)
    if not base:
        flash("Could not fetch zone info from Cloudflare.", "error")
        return redirect(url_for("index"))

    records        = fetch_dns_records(cf0)
    existing_names = {s["name"] if isinstance(s, dict) else s for s in cf0["subdomains"]}
    added          = 0
    for r in records:
        name = r["name"]
        if name == base:
            sub_name = "@"
        elif name.endswith("." + base):
            sub_name = name[: -(len(base) + 1)]
        else:
            continue
        if sub_name not in existing_names:
            cf0["subdomains"].append({"name": sub_name, "proxied": r.get("proxied", False)})
            existing_names.add(sub_name)
            added += 1

    if added:
        save_config(cfg)
        flash(f"Imported {added} subdomain(s) from Cloudflare.", "success")
    else:
        flash("No new subdomains to import – everything is already configured.", "info")
    return redirect(url_for("index"))


@app.route("/update")
def update_now():
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cloudflare_ddns.py")
    try:
        cp = subprocess.run(
            ["python3", script], capture_output=True, text=True, timeout=30)
        if cp.returncode == 0:
            flash("DNS records updated successfully.", "success")
        else:
            flash(f"Update failed: {(cp.stderr or cp.stdout or 'unknown error').strip()}", "error")
    except subprocess.TimeoutExpired:
        flash("Update timed out after 30 s.", "error")
    return redirect(url_for("index"))


# ── JSON API (used by the dashboard for live auto-refresh) ────────────────────

@app.route("/api/status")
def api_status():
    return jsonify(load_status())


@app.route("/api/records")
def api_records():
    cfg = load_config()
    cf0 = cfg["cloudflare"][0]
    return jsonify(fetch_dns_records(cf0))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
