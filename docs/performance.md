# Performance characterisation

**Date:** 2026-04-30
**Commit:** `5e0b6ae`
**Target:** production deployment (single edge region, single uvicorn worker, single container).
**Test client:** Belgian residential connection → DE PoP, single source IP, authenticated via a labeled trusted token (revoked after the run).
**Tools:** `bombardier` v1.2.6, `vegeta` v12.12.0.
**Reproduction:** `scripts/perf_test.sh` (parameterised on `PC2NUTS_TARGET` and `PC2NUTS_TOKEN`).

---

## Headline

> **Sustained throughput ceiling: ~30 requests/second (~1,800 requests/minute).**
>
> **Recommended operating point: 27 RPS (~1,620/min), p99 < 200 ms.**

The current `60/minute` per-IP cap is therefore not the system bottleneck — the deployment can serve roughly **30× that volume in aggregate** before throughput plateaus. A single client could be permitted up to ~1,500/minute (25 RPS) without affecting overall headroom; the per-IP cap should be set well below the aggregate ceiling regardless.

---

## Latency curve (Scenario B — random valid lookups across 5 countries)

This is the realistic-input scenario and the basis for the headline number.

| Offered RPS | Achieved RPS | Success | p50 | p90 | p95 | p99 | Max |
|------------:|-------------:|--------:|----:|----:|----:|----:|----:|
|          10 |         10.0 |    100% |  46 ms |  53 ms |  63 ms |  74 ms | 104 ms |
|          20 |         20.0 |    100% |  45 ms |  54 ms |  60 ms |  96 ms | 136 ms |
|          25 |         25.1 |    100% |  46 ms |  54 ms |  73 ms | 151 ms | 228 ms |
|      **30** |     **30.0** |**100%** |**48 ms**|**109 ms**|**137 ms**|**193 ms**|**222 ms**|
|          35 |         32.2 |    100% |2.27 s |3.65 s |4.07 s |4.47 s |5.62 s |

The **knee is at 30 RPS**. From 30 → 35 the throughput barely moves (32.2 vs 30.0) but tail latencies jump 12-30×. Beyond the knee, queue depth grows without bound — the curve is sharp, not gradual.

## Saturation discovery (Scenario A — hot single key, BE 3080)

Throughput plateaus regardless of client concurrency, confirming the bottleneck is per-request work on the server (single event loop / single worker), not concurrency exhaustion on the client.

| Connections | Reqs/sec | p50 | p95 | p99 |
|------------:|---------:|----:|----:|----:|
|           5 |     29.6 | 169 ms | 225 ms | 267 ms |
|          10 |     31.0 | 325 ms | 443 ms | 479 ms |
|          20 |     31.8 | 617 ms | 795 ms | 1.00 s |
|          40 |     30.9 | 1.21 s | 1.63 s | 2.31 s |
|          80 |     30.4 | 2.30 s | 3.92 s | 6.92 s |

Throughput is bounded; concurrency just queues.

**At c≥100 the platform pushes back.** An exploratory pre-run at c=100, 200, 400, 800 produced widespread `5xx`, `dial tcp … connection timed out`, and `tls handshake timed out` errors — i.e. the edge platform aggressively refuses connections at very high concurrency from a single source. Stay well below c=100 in any scripted test against this deployment.

## Fallback-path cost (Scenario C — 50/50 hit/miss at 25 RPS)

Compared to Scenario B at the same rate (25/s), the 50/50 mix is statistically indistinguishable: p50 45 ms vs 46 ms; p99 136 ms vs 151 ms. The Tier 3 prefix-approximation path (taken on every "miss") imposes **no measurable latency cost** at this load. The hard work is per-request HTTP/TLS framing and JSON serialisation, not the lookup itself.

## FastAPI/uvicorn floor (Scenario D — `/health` at 25 RPS)

| Endpoint | p50 | p95 | p99 | Max |
|---|---:|---:|---:|---:|
| `/health` | **15 ms** | 19 ms | 27 ms | 62 ms |
| `/lookup` (Scenario B at 25/s) | 46 ms | 73 ms | 151 ms | 228 ms |

`/health` is roughly **3× faster** than `/lookup`. About 15 ms of every request is the platform/network/TLS/uvicorn floor; the additional ~30 ms on `/lookup` is the endpoint logic plus Pydantic response serialisation. **Optimisation candidates** if a higher ceiling is needed: response serialisation (the dict access itself is microseconds), reducing JSON envelope size, or moving to multi-worker.

## Stability (Scenario E — sustained 27 RPS for 3 minutes)

| Metric | Value |
|---|---|
| Total requests | 4,860 |
| Achieved rate | 27.0/s |
| Success | 100.0% (200:4860) |
| p50 / p95 / p99 / max | 46 / 89 / 132 / 324 ms |
| <50 ms | 73.0% |
| <100 ms | 97.4% |
| <200 ms | 99.8% |
| 5xx | 0 |
| 429 | 0 |

No drift over the 3-minute window. p99 stayed well under 200 ms throughout.

---

## Methodology notes

- **Cooldown between runs.** A short pause (10 s) between scenarios is needed; without it, residual queueing from the previous run pollutes the next.
- **Bombardier default 2 s timeout is too aggressive** here — runs at near-saturation see legitimate 1-2 s tail latencies. Use `--timeout 30s` to avoid spurious "timeout" classifications.
- **Single-region edge means single-PoP measurements.** The platform allocates the deployment to one region (DE). Latency from clients elsewhere will differ accordingly, but the throughput ceiling is unaffected — every request still hits the same one container.
- **Single source IP test client.** Distributed traffic from many IPs would not change the aggregate ceiling (the bottleneck is the container) but would change the per-IP rate-limit behaviour, since slowapi keys per source.
- **No CDN cache between client and `/lookup`.** Verified by inspecting response headers — no `Cache-Status`, no `CDN-Cache-Status`, every request reaches the container.

---

## Recommendations

1. **Keep per-IP cap conservative relative to aggregate ceiling.** The current `60/minute` (1 RPS per IP) leaves comfortable headroom: even ~30 saturation-rate clients in parallel could sustain themselves before degrading the aggregate. No change needed unless trusted-token traffic patterns become heavy.

2. **Pick `p99 ≤ 200 ms` as the SLO** at the recommended 27 RPS operating point. The full 3-minute sustained run met this.

3. **Re-baseline after issues #7, #45, or any worker-count change land.** Specifically:
   - **#7 (UK NSPL, +1.79M postcodes)** — should not change per-request latency materially (still a dict lookup) but doubles in-memory state. Re-run to confirm.
   - **#45 (happyGISCO outbound geocoding)** — would add a network call to the lookup path; the saturation RPS will drop sharply. **Mandatory** re-baseline.
   - **Switching from single-worker to multi-worker** — likely the easiest large win. Each additional worker should approximately add another 30 RPS of headroom up to the container's CPU count.

4. **Don't run unattended high-concurrency tests.** Bombardier at c≥100 from a single source triggers platform-level connection refusal (`5xx`, dial timeouts) and risks short-term throttling. Keep scripted load below c=80.

---

## Reproducing

```bash
# 1. Issue a labeled trusted token (operator credentials required):
export PC2NUTS_TOKEN_DB_URL='libsql://...'
export PC2NUTS_TOKEN_DB_AUTH_TOKEN='...'
python -m scripts.tokens add --label "perf-test-$(date -I)"
# Token will become active in the running service after one refresh interval (default 60 s).

# 2. Run the suite:
export PC2NUTS_TARGET='https://example.invalid'
export PC2NUTS_TOKEN='<the value printed above>'
scripts/perf_test.sh

# 3. Revoke when done:
python -m scripts.tokens revoke <id>
```

Raw outputs are written to `/tmp/perf/`. The harness automatically downloads a fresh corpus from public GISCO TERCET ZIPs on first run.
