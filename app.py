import os, json, subprocess
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

# import your ddns functions
import cloudflare_ddns

# where config.json lives in the container
CONFIG_FILE = os.environ.get("CONFIG_PATH", "config.json")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change_me")

def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_config(cfg):
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)

@app.route("/", methods=["GET","POST"])
def index():
    if request.method == "POST":
        # Very simplistic form parsing.
        # Expand this to match your config schema.
        cfg = {
            "authentication": {
                "api_key": {
                    "account_email": request.form["account_email"],
                    "api_key": request.form["api_key"]
                }
            },
            "cloudflare": [
                {
                    "zone_id": request.form["zone_id"],
                    "subdomains": [{"name": request.form["subdomain"], "proxied": request.form.get("proxied")=="on"}]
                }
            ],
            "a": request.form.get("enable_ipv4")=="on",
            "aaaa": request.form.get("enable_ipv6")=="on",
            "purgeUnknownRecords": request.form.get("purge")=="on",
            "ttl": int(request.form["ttl"] or 300)
        }
        save_config(cfg)
        flash("Saved config.json", "success")
        return redirect(url_for("index"))

    cfg = load_config()
    return render_template("index.html", cfg=cfg)

@app.route("/update")
def update_now():
    # run your ddns script once
    cp = subprocess.run(
        ["python3","cloudflare_ddns.py"],
        cwd=os.getcwd(),
        capture_output=True, text=True
    )
    return (
        "<h3>STDOUT</h3><pre>{}</pre><h3>STDERR</h3><pre>{}</pre>"
        .format(cp.stdout, cp.stderr)
    )

@app.route("/status")
def status():
    return jsonify(cloudflare_ddns.getIPs())

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)