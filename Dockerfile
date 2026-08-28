FROM python:3.14-slim

WORKDIR /app

# gosu is used by docker-entrypoint.sh to drop privileges to appuser after
# fixing ownership on the /app/data mount (no-op on warm starts; required
# when the platform mounts a fresh root-owned persistent volume).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.lock ./requirements.lock
RUN pip install --no-cache-dir -r requirements.lock

RUN useradd -r -s /bin/false appuser \
    && mkdir -p /app/data \
    && chown appuser:appuser /app/data

COPY app/ ./app/
COPY tercet_missing_codes.csv ./tercet_missing_codes.csv
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
# --no-access-log: uvicorn's access log writes the full request line (including
# /resolve's street/city query params) to stdout. Disable it so address PII never
# reaches container logs; use PC2NUTS_ACCESS_LOG_FILE for sanitized access logging.
#
# --forwarded-allow-ips: ONLY the addresses listed here may set X-Forwarded-For.
# It must name the reverse proxy, never '*': with '*' uvicorn trusts the header
# from any peer and takes its LEFTMOST entry, which is the value the client sent
# — so any caller could mint a fresh per-IP rate-limit bucket per request (and
# forge the client IP in the access log) even behind a proxy that appends
# correctly. Set PC2NUTS_FORWARDED_ALLOW_IPS to the proxy's address or CIDR
# (e.g. "172.17.0.1", "10.0.0.0/8"); the default trusts only a loopback proxy.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${PC2NUTS_WORKERS:-1} --proxy-headers --forwarded-allow-ips \"${PC2NUTS_FORWARDED_ALLOW_IPS:-127.0.0.1}\" --no-access-log"]
