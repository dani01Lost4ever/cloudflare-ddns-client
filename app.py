import os, json, subprocess
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

# import your renamed script
import cloudflare_ddns

# where the single file is mounted
CONFIG_FILE = os.environ.get("CONFIG_PATH", "/config.json")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change_me")

def load_config():
    try:
        cfg = json.load(open(CONFIG_FILE))
    except Exception:
        # empty default
        cfg = {"cloudflare": [], "a": True, "aaaa": True, "purgeUnknownRecords": False, "ttl": 300}

    # ensure at least one cloudflare entry exists
    if not cfg.get("cloudflare"):
        cfg["cloudflare"] = [{
            "authentication": {
                "api_token": "",
                "api_key": {"account_email": "", "api_key": ""}
            },
            "zone_id": "",
            "subdomains": [{"name": "", "proxied": False}]
        }]
    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # build the subdomains list from dynamic form fields
        subs = []
        i = 0
        while True:
            name = request.form.get(f"subdomain_name_{i}")
            if name is None:
                break
            prox = request.form.get(f"subdomain_proxied_{i}") == "on"
            if name.strip():
                subs.append({"name": name.strip(), "proxied": prox})
            i += 1

        cf0 = {
            "authentication": {
                "api_token": request.form.get("api_token", "").strip(),
                "api_key": {
                    "account_email": request.form.get("account_email", "").strip(),
                    "api_key": request.form.get("api_key", "").strip()
                }
            },
            "zone_id": request.form.get("zone_id", "").strip(),
            "subdomains": subs or [{"name": "", "proxied": False}]
        }

        cfg = {
            "cloudflare": [cf0],
            "a": request.form.get("enable_ipv4") == "on",
            "aaaa": request.form.get("enable_ipv6") == "on",
            "purgeUnknownRecords": request.form.get("purge") == "on",
            "ttl": int(request.form.get("ttl") or 300)
        }
        save_config(cfg)
        flash("config.json saved", "success")
        return redirect(url_for("index"))

    cfg = load_config()
    # work against the first cloudflare entry
    cf0 = cfg["cloudflare"][0]
    return render_template("index.html", cfg=cfg, cf=cf0)

@app.route("/update")
def update_now():
    # run one-shot update
    cp = subprocess.run(
        ["python3", "/app/cloudflare_ddns.py"],
        capture_output=True, text=True
    )
    return (
        "<h3>STDOUT</h3><pre>{}</pre>"
        "<h3>STDERR</h3><pre>{}</pre>"
        .format(cp.stdout, cp.stderr)
    )

@app.route("/status")
def status():
    # return { "ipv4": {...}, "ipv6": {...} }
    return jsonify(cloudflare_ddns.getIPs())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)