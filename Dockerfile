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
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${PC2NUTS_WORKERS:-1}"]
