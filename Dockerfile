# ---- Base image ----
FROM python:3.9-alpine AS base

# ensure local pip installs are on PATH
ENV PATH=/root/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    # tell both scripts where to find config.json
    CONFIG_PATH=/config

WORKDIR /app

# ---- Install deps ----
COPY requirements.txt /app/
RUN pip install --no-cache-dir --user -r requirements.txt

# ---- Copy application code ----
# your DDNS script
COPY cloudflare_ddns.py /app/
# the Flask glue
COPY app.py /app/
# UI templates
COPY templates/ /app/templates/

# ---- Expose the UI port ----
EXPOSE 5000

# ---- Declare config volume ----
# the user must mount their host folder here:
VOLUME ["/config.json"]

# ---- Start both DDNS and Web UI ----
# we use sh -c to fire-and-forget the ddns loop, then exec gunicorn
CMD ["sh", "-c", "\
     python /app/cloudflare-ddns.py --repeat & \
     exec gunicorn --bind 0.0.0.0:5000 app:app \
    "]