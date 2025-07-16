import os, json, subprocess
from flask import (
    Flask, render_template, request,
    redirect, url_for, flash
)
import cloudflare_ddns

# path to the single mounted file
CONFIG_FILE = os.environ.get("CONFIG_PATH", "/config.json")
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "please-change-me")

def load_config():
    try:
        cfg = json.load(open(CONFIG_FILE))
    except Exception:
        # if missing or invalid, seed with minimal structure
        cfg = {
            "cloudflare": [{
                "authentication": {
                    "api_token": "",
                    "api_key": {"account_email": "", "api_key": ""}
                },
                "zone_id": "",
                "subdomains": []
            }],
            "a": True,
            "aaaa": True,
            "purgeUnknownRecords": False,
            "ttl": 300
        }
    # guarantee at least one zone entry
    if not cfg.get("cloudflare"):
        cfg["cloudflare"] = [{
            "authentication": {
                "api_token": "",
                "api_key": {"account_email": "", "api_key": ""}
            },
            "zone_id": "",
            "subdomains": []
        }]
    return cfg

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

def fetch_dns_records(cf0):
    """Use cloudflare_ddns.cf_api to list all A+AAAA records for our zone."""
    recs = []
    for typ in ("A", "AAAA"):
        answer = cloudflare_ddns.cf_api(
            f"zones/{cf0['zone_id']}/dns_records?per_page=100&type={typ}",
            "GET", cf0
        )
        if answer and answer.get("result"):
            recs += answer["result"]
    return recs

@app.route("/", methods=["GET"])
def index():
    cfg = load_config()
    cf0 = cfg["cloudflare"][0]
    records = fetch_dns_records(cf0)
    return render_template(
        "index.html",
        zone=cf0["zone_id"],
        subs=cf0["subdomains"],
        records=records
    )

@app.route("/add-subdomain", methods=["POST"])
def add_subdomain():
    new = request.form.get("new_subdomain", "").strip()
    if not new:
        flash("Subdomain cannot be empty", "error")
    else:
        cfg = load_config()
        cf0 = cfg["cloudflare"][0]
        names = [s["name"] for s in cf0["subdomains"]]
        if new in names:
            flash(f"‘{new}’ already in list", "error")
        else:
            cf0["subdomains"].append({"name": new, "proxied": False})
            save_config(cfg)
            flash(f"Added subdomain ‘{new}’", "success")
    return redirect(url_for("index"))

@app.route("/update")
def update_now():
    # fire off one‐shot update
    cp = subprocess.run(
        ["python3", "/app/cloudflare_ddns.py"],
        capture_output=True, text=True
    )
    out = cp.stdout or "(no stdout)"
    err = cp.stderr or "(no stderr)"
    return f"<h2>Update Complete</h2><h3>STDOUT</h3><pre>{out}</pre>" \
           f"<h3>STDERR</h3><pre>{err}</pre>" \
           f'<p><a href="/">◀ Back</a></p>'

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)