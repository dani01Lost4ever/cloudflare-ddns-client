# Cloudflare DDNS — with Web UI Dashboard

Keep every Cloudflare DNS record in sync with your real public IP — automatically, efficiently, and with a live web dashboard to manage everything without touching a config file.

> Based on the original [timothyjmiller/cloudflare-ddns](https://github.com/timothymiller/cloudflare-ddns), extended with a full web UI, smart polling, rate-limit awareness, and a modern dashboard.

---

## How it works

Two processes run side-by-side inside the same container:

| Process | Role |
|---|---|
| `cloudflare_ddns.py --repeat` | Polls your public IP every N seconds. Calls the Cloudflare API **only when the IP changes**. Writes a `status.json` file for the dashboard. |
| `gunicorn app.py` | Serves the web dashboard on port **5000**. Reads `status.json` and `config.json`; never polls the IP itself. |

Because the IP check uses plain HTTP (not the Cloudflare API), you can poll every 2–5 seconds without ever coming close to Cloudflare's rate limit.

---

## Features

### DDNS daemon
- 🔄 **Smart polling** — checks your IP every `check_interval` seconds (default: 5 s); only hits the Cloudflare API when the IP actually changes
- 🛡️ **Rate-limit aware** — built-in sliding-window limiter (1 200 calls / 5 min); automatically waits if the limit is approached
- 🌐 **Multi-source IP detection** — tries multiple independent providers in order, validates the family (IPv4 vs IPv6), and discards wrong-family results:
  1. `checkip.amazonaws.com` (AWS)
  2. `api.ipify.org`
  3. `ipv4.icanhazip.com` / `ipv6.icanhazip.com`
  4. `ip4.seeip.org`
  5. `1.1.1.1/cdn-cgi/trace` (last resort — can return Cloudflare's own IPs in certain network setups)
- 📡 **Dual-stack** — independent IPv4 (A) and IPv6 (AAAA) record support, each toggleable
- 🏠 **Multiple zones** — manage several Cloudflare zones (domains) with one container
- 📠 **Multiple subdomains** — update as many subdomains as you need per zone
- 🔒 **Proxied records** — per-subdomain toggle for the Cloudflare orange-cloud proxy
- 🗑️ **Duplicate purging** — optional `purgeUnknownRecords` removes stale/duplicate records
- 📝 **Status file** — writes `status.json` next to `config.json` after every poll so the dashboard always has fresh data at zero API cost
- ✉️ **Env-variable substitution** — any `${CF_DDNS_*}` placeholder in `config.json` is replaced at startup from the environment (keeps secrets out of files)
- 🛑 **Graceful shutdown** — handles `SIGINT` / `SIGTERM` cleanly

### Web dashboard (port 5000)
- 📊 **Live stat cards** — current IPv4, current IPv6, last Cloudflare update time, API calls used in the last 5 minutes (with a visual rate bar)
- 🗂️ **DNS records table** — live view of all A/AAAA records in your zone, with type badge and proxied status
- ➕ **Add subdomains** — add a new subdomain (with optional proxy toggle) without editing `config.json`
- 🗑️ **Delete subdomains** — remove a managed subdomain with one click
- ☁️ **Import from Cloudflare** — one-click sync that reads all existing A/AAAA records from your Cloudflare zone and adds them as managed subdomains
- ▶️ **Manual update** — force an immediate DNS update from the navbar
- ⚙️ **Config summary card** — shows active `check_interval`, `ttl`, and which IP families are enabled
- 🔁 **Auto-refresh** — dashboard stats refresh every 5 seconds via `/api/status` (reads `status.json`, zero API calls)
- 🌑 **Dark theme** — Cloudflare-branded dark UI

### REST API (used by the dashboard, also scriptable)
| Endpoint | Method | Description |
|---|---|---|
| `/api/status` | GET | Returns `status.json` as JSON (current IPs, last update, API call count) |
| `/api/records` | GET | Fetches live A/AAAA records from Cloudflare and returns them as JSON |
| `/update` | GET | Triggers a one-shot `cloudflare_ddns.py` run, flashes result, redirects to dashboard |
| `/add-subdomain` | POST | Adds a subdomain to `config.json` |
| `/delete-subdomain/<idx>` | POST | Removes a subdomain from `config.json` by index |
| `/sync-from-cloudflare` | POST | Imports all existing CF records as managed subdomains |

---

## Quick start

### 1 — Get your Cloudflare credentials

**Zone ID** — Cloudflare Dashboard → your domain → Overview tab → right sidebar.

**API Token** (recommended) — [Cloudflare Profile → API Tokens](https://dash.cloudflare.com/profile/api-tokens) → Create Token → *Edit DNS* template. Scope it to the specific zone.

**Legacy API Key** (alternative) — your account email + the Global API Key from your profile.

---

### 2 — Create `config.json`

```json
{
  "cloudflare": [
    {
      "authentication": {
        "api_token": "YOUR_API_TOKEN_HERE",
        "api_key": {
          "api_key": "",
          "account_email": ""
        }
      },
      "zone_id": "YOUR_ZONE_ID_HERE",
      "subdomains": [
        { "name": "@",        "proxied": false },
        { "name": "home",     "proxied": false },
        { "name": "vpn",      "proxied": false }
      ]
    }
  ],
  "a": true,
  "aaaa": false,
  "purgeUnknownRecords": false,
  "ttl": 300,
  "check_interval": 5
}
```

> Use `"name": "@"` or `"name": ""` to target the root domain (`example.com`).
> Only provide one auth method — `api_token` takes priority if non-empty.

---

### 3 — Deploy with Docker Compose

**`docker-compose.yml`**
```yaml
services:
  cloudflare-ddns:
    image: dani01cs/cloudflare_ddns_updater:latest
    container_name: cloudflare-ddns
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    ports:
      - "5000:5000"
    volumes:
      - ${CONFIG_DIR}:/config        # mount the folder, not just the file
    environment:
      CONFIG_PATH: /config           # where cloudflare_ddns.py looks for config.json
      CONFIG_FILE: /config/config.json   # where app.py looks for config.json
      FLASK_SECRET: ${FLASK_SECRET:-please-change-me}
```

**`.env`**
```env
# Absolute path to the FOLDER that contains config.json on the host
CONFIG_DIR=/home/daniel/cloudflare-ddns

# Secret key for Flask session signing
FLASK_SECRET=some-long-random-string
```

> **Why mount the folder instead of the file?**
> Docker on some platforms (especially Windows/WSL) creates a *directory* when you bind-mount a file path that doesn't exist yet. Mounting the folder avoids this entirely.

```bash
docker compose up -d
# Dashboard available at http://your-host:5000
```

---

## Config reference

| Key | Type | Default | Description |
|---|---|---|---|
| `cloudflare` | array | — | One entry per zone (see below) |
| `a` | bool | `true` | Update A (IPv4) records |
| `aaaa` | bool | `true` | Update AAAA (IPv6) records |
| `ttl` | int | `300` | DNS record TTL in seconds (1 = auto, 30–86400) |
| `check_interval` | int | `5` | Seconds between IP polls (min 2, max 30) |
| `purgeUnknownRecords` | bool | `false` | Delete duplicate/stale records for managed names |

**Per-zone keys** (inside each `cloudflare[]` entry):

| Key | Description |
|---|---|
| `authentication.api_token` | Cloudflare API token with *Edit DNS* permission (preferred) |
| `authentication.api_key.api_key` | Legacy Global API Key |
| `authentication.api_key.account_email` | Account email for legacy key auth |
| `zone_id` | Zone ID from the Cloudflare dashboard |
| `subdomains` | Array of `{ "name": "subdomain", "proxied": true/false }` |

---

## Multiple zones

Add a second entry to the `cloudflare` array:

```json
{
  "cloudflare": [
    {
      "authentication": { "api_token": "TOKEN_A" },
      "zone_id": "ZONE_ID_FOR_EXAMPLE_COM",
      "subdomains": [
        { "name": "@",    "proxied": false },
        { "name": "home", "proxied": false }
      ]
    },
    {
      "authentication": { "api_token": "TOKEN_B" },
      "zone_id": "ZONE_ID_FOR_ANOTHER_COM",
      "subdomains": [
        { "name": "app",  "proxied": true }
      ]
    }
  ],
  "a": true,
  "aaaa": false,
  "ttl": 300,
  "check_interval": 5
}
```

---

## Environment-variable secrets

Keep credentials out of `config.json` by using `${CF_DDNS_*}` placeholders:

```json
{
  "cloudflare": [{
    "authentication": {
      "api_token": "${CF_DDNS_API_TOKEN}"
    },
    "zone_id": "${CF_DDNS_ZONE_ID}",
    ...
  }]
}
```

Then pass them in your compose file or shell:

```yaml
environment:
  CF_DDNS_API_TOKEN: your_token_here
  CF_DDNS_ZONE_ID: your_zone_id_here
```

---

## IPv4 vs IPv6

If your ISP or router only supports one family, disable the other:

```json
"a": true,
"aaaa": false
```

> **Docker + IPv6 note:** to expose an IPv6 address from inside a container, the Docker network must have IPv6 enabled (`enable_ipv6: true` in the compose network config) or `network_mode: host`.

---

## Building and publishing the image

```bash
# Build
docker build -t dani01cs/cloudflare_ddns_updater:latest .

# Push to Docker Hub
docker login
docker push dani01cs/cloudflare_ddns_updater:latest

# Tag a specific release
docker tag dani01cs/cloudflare_ddns_updater:latest dani01cs/cloudflare_ddns_updater:1.1.0
docker push dani01cs/cloudflare_ddns_updater:1.1.0
```

---

## Local development (without Docker)

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Copy and edit config
cp config-example.json config.json

# Run the daemon (single shot)
python cloudflare_ddns.py

# Run the daemon in loop mode
python cloudflare_ddns.py --repeat &

# Run the web UI
python app.py          # http://localhost:5000
```

---

## Helpful links

- [Cloudflare API Tokens](https://dash.cloudflare.com/profile/api-tokens)
- [How to find your Zone ID](https://developers.cloudflare.com/fundamentals/setup/find-account-and-zone-ids/)
- [Cloudflare DNS TTL docs](https://developers.cloudflare.com/dns/manage-dns-records/reference/ttl/)
- [Cloudflare API rate limits](https://developers.cloudflare.com/fundamentals/api/reference/limits/)

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).
