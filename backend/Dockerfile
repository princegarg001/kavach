FROM python:3.12-slim

# curl needed for the container healthcheck; ca-certificates for outbound HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /code

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# NOTE: Playwright/Chromium is intentionally NOT installed in this image.
# ENABLE_SCREENSHOT_TOOL=false on memory-constrained hosts (Render free/512MB) —
# every other investigator tool still runs. On a host with >=2GB RAM, set
# ENABLE_SCREENSHOT_TOOL=true and add back:
#   RUN playwright install --with-deps chromium

# Copy application
COPY . .

# Warm the local embedding model into the image so the first request isn't slow
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')" || true

# Host injects $PORT — default to 8000
ENV PORT=8000
EXPOSE 8000

# Single worker: the SSE queue, rate-limit state, dedup cache and the APScheduler
# are all in-process. Scale with replicas, not workers.
# --proxy-headers + --forwarded-allow-ips: Render terminates TLS at its edge and
# proxies plain HTTP to the container. Without these, uvicorn reports scheme=http
# and request.url is wrong — which breaks Twilio's X-Twilio-Signature check
# (computed against the https:// URL Twilio actually posted to).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
