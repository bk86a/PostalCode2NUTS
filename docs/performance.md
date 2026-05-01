# Performance characterisation

Two runs on file: a single-worker baseline at `5e0b6ae` and a multi-worker
re-baseline at `18e1908` after #68/#71 shipped (`PC2NUTS_WORKERS=2`,
Redis-backed shared rate-limit storage via a sidecar container).

| | Single-worker baseline | Multi-worker (current) |
|---|---|---|
| Date | 2026-04-30 | 2026-05-01 |
| Commit | `5e0b6ae` | `18e1908` |
| uvicorn workers | 1 | 2 |
| Rate-limit backend | per-process in-memory | Redis sidecar (`redis://localhost:6379/0`), shared across workers |
| Test client | BE residential → DE PoP, single source IP, authenticated via labeled trusted token (revoked after each run) | (same) |
| Tools | `bombardier` v1.2.6, `vegeta` v12.12.0 | (same) |
| Reproduction | `scripts/perf_test.sh` | (same) |

---

## Headline

> **Multi-worker plateau under realistic random-corpus load (Scenario B): ~35-38 RPS** before queue saturation. Hot-key with persistent connections (Scenario A) sustains **~50 RPS**. Single-worker baseline plateaued at ~30 RPS in both.
>
> **Recommended operating point: unchanged at 27 RPS.** The 3-minute sustained run holds 100% success, p99 162 ms — well inside the SLO. Multi-worker raises *headroom* above the operating point from ~10% to ~30-40%, not the operating point itself.
>
> **Rate-limit shared-storage verified.** 130 anonymous requests sequentially from a single source IP yielded exactly 120×`200` + 10×`429`. The Redis sidecar is reachable from both workers and the cap is enforced globally, not per-worker.

The aggregate ceiling roughly **scales 1.5×** with two workers (not 2×). Likely contributors: GIL contention on Pydantic serialisation, fresh-TLS overhead per request in vegeta's connection pattern, and shared platform-edge serialisation in front of the pod. Scenario A's higher ceiling (50 RPS with persistent connections) implies the per-request TLS handshake is part of the cap, not just per-request CPU.

---

## Latency curve (Scenario B — random valid lookups across 5 countries)

This is the realistic-input scenario and the basis for the headline numbers.

| Offered RPS | Achieved RPS | Success | p50 | p90 | p95 | p99 | Max |
|------------:|-------------:|--------:|----:|----:|----:|----:|----:|
|          10 |         10.0 |    100% |  57 ms |  68 ms |  71 ms | 102 ms | 120 ms |
|          20 |         20.0 |    100% |  60 ms |  70 ms |  83 ms | 111 ms | 211 ms |
|          25 |         25.1 |    100% |  56 ms |  71 ms |  79 ms | 112 ms | 181 ms |
|      **30** |     **30.0** |**100%** |**62 ms**|**75 ms**|**95 ms**|**122 ms**|**210 ms**|
|          35 |         34.8 |    100% |  63 ms |  97 ms | 110 ms | 150 ms | 170 ms |
|          40 |         38.3 |    100% | 1.71 s | 3.14 s | 3.61 s | 4.24 s | 4.60 s |
|          50 |         36.3 |   89.3% | 3.85 s | 6.63 s | 8.75 s | 9.86 s | 10.7 s |
|          60 |         52.2 |    100% | 1.60 s | 2.44 s | 2.65 s | 2.98 s | 3.14 s |

**The new knee sits between 35 and 40 RPS.** From 35 → 40 the throughput barely moves (38 vs 35) but tail latencies jump 30×. Beyond, behaviour is bimodal: 50 RPS hit transient platform back-pressure (107 × 503), while 60 RPS pushed through cleanly at higher achieved throughput than 50 — the platform-edge layer's overload mode is non-monotonic.

Compared to the single-worker baseline:

| Offered RPS | p99 (single-worker) | p99 (multi-worker) | Δ |
|------------:|--------------------:|-------------------:|---:|
| 10 |  74 ms | 102 ms | +28 ms (within noise) |
| 20 |  96 ms | 111 ms | +15 ms |
| 25 | 151 ms | 112 ms | **−39 ms** |
| 30 | 193 ms | 122 ms | **−71 ms** |
| 35 |   4.5 s |  150 ms | **−4.3 s — single-worker collapsed here** |

At and below the operating point the curves are similar; the win shows up at and beyond the old knee, where the new system absorbs ~20% more sustained throughput before breaking down.

## Saturation discovery (Scenario A — hot single key, BE 3080)

Throughput plateaus regardless of client concurrency, confirming the bottleneck is per-request work, not concurrency exhaustion on the client. Plateau roughly **1.6× the single-worker baseline.**

| Connections | Reqs/sec (single) | Reqs/sec (multi) | p99 (single) | p99 (multi) |
|------------:|------------------:|-----------------:|-------------:|------------:|
|           5 | 29.6 | **46.9** |   267 ms |    186 ms |
|          10 | 31.0 | **50.8** |   479 ms |    338 ms |
|          20 | 31.8 | **47.6** |  1.00 s  |    746 ms |
|          40 | 30.9 | **51.4** |  2.31 s  |   1.20 s  |
|          80 | 30.4 | **47.9** |  6.92 s  |   2.78 s  |

Throughput is bounded around 50 RPS; concurrency just queues. **Tail latency at saturation is ~2.5× lower** under multi-worker — at c=80 the single-worker setup pushed p99 to 6.9 s, multi-worker holds it under 2.8 s.

**At c≥100 the platform pushes back** (unchanged from single-worker baseline). Stay below c=80 in any scripted test against this deployment.

## Fallback-path cost (Scenario C — 50/50 hit/miss at 25 RPS)

Compared to Scenario B at the same rate: p50 62 ms vs 56 ms; p99 115 ms vs 112 ms. The Tier 3 prefix-approximation path imposes **no measurable latency cost** at this load (matches the single-worker conclusion).

## FastAPI/uvicorn floor (Scenario D — `/health` at 25 RPS)

| Endpoint | p50 | p95 | p99 | Max |
|---|---:|---:|---:|---:|
| `/health` (multi-worker) | 18 ms | 37 ms | 63 ms | 91 ms |
| `/health` (single-worker baseline) | 15 ms | 19 ms | 27 ms | 62 ms |
| `/lookup` (Scenario B at 25 RPS, multi-worker) | 56 ms | 79 ms | 112 ms | 181 ms |

`/health` p99 is ~2× higher under multi-worker (63 ms vs 27 ms) — small absolute number, but the only place the second worker is visibly *worse*. Probable cause: process scheduling jitter when the OS load-balances incoming connections across two workers vs one. Worth re-measuring if `/health` ever becomes a hot path; not material for the `/lookup` ceiling.

## Stability (Scenario E — sustained 27 RPS for 3 minutes)

| Metric | Single-worker | Multi-worker |
|---|---|---|
| Total requests | 4,860 | 4,860 |
| Achieved rate | 27.0/s | 27.0/s |
| Success | 100.0% | 100.0% |
| p50 / p95 / p99 / max | 46 / 89 / 132 / 324 ms | 63 / 111 / 162 / 391 ms |
| <50 ms | 73.0% | 13.8% |
| <100 ms | 97.4% | 93.2% |
| <200 ms | 99.8% | 99.6% |
| 5xx | 0 | 0 |
| 429 | 0 | 0 |

No drift over the 3-minute window. p99 stayed under 200 ms throughout. Tail-latency distribution is tighter at the median under single-worker (much more <50 ms) but the >100 ms tail is slightly fatter under multi-worker — net p99 is ~30 ms higher. Within the SLO either way.

## Rate-limit shared-storage verification

A separate probe with **no `Authorization` header** was used to exercise the
per-IP cap (the trusted-token bypass turns the cap off, so the perf scenarios
can't observe it). 130 sequential requests from a single source IP, against
the published cap of `120/minute`:

| Outcome | Count |
|---|---:|
| `200` | 120 |
| `429` | 10 |

Result is exact, not approximate. If both workers had used per-process
in-memory storage (the failure mode the startup validator at
`app/config.py:42-50` exists to prevent), the effective cap would have been
240 — and 130 requests from one IP would have produced 130 × `200`, zero
`429`s. The `120 + 10` split is conclusive evidence that:

1. `PC2NUTS_RATE_LIMIT_STORAGE_URI=redis://localhost:6379/0` is being read.
2. The Redis sidecar (`library/redis@sha256:84b07a33…5cf5b27`) is reachable
   from both workers via the shared pod network namespace.
3. slowapi's shared-counter increments are synchronised across workers.
4. The `120/minute` cap is honoured globally, not per-worker.

---

## Methodology notes

- **Tools, methodology, and corpus are unchanged from the single-worker baseline** — same `bombardier`/`vegeta` versions, same scenarios, same target file format. Numbers are directly comparable.
- **Cooldown between runs.** Same 10 s pause between scenarios as before; needed to keep residual queueing from one run from polluting the next.
- **Single source IP test client.** Aggregate ceiling reflects a single TCP/TLS termination path; distributed traffic from many IPs would push the ceiling up to where the actual per-pod work is the bottleneck — but the recommended operating point is set by single-client latency, so this is the realistic measurement.
- **Multi-worker container topology.** The deployment is now a single pod with two co-located containers (`api` running uvicorn with two workers; `redis:7-alpine` started with `--save "" --appendonly no` for in-memory rate-limit counters). Both share the pod network namespace, so `redis://localhost:6379/0` is the api-to-redis URI. Rate-limit counters reset every minute, so no persistence is needed.
- **Why the 1.6× and not 2×.** Two workers don't double throughput. Likely contributors, in rough order: shared edge-layer TLS termination in front of the single pod; Pydantic serialisation contending under the GIL when both workers are CPU-bound on JSON; vegeta's per-request fresh-connection pattern at higher rates putting more weight on TLS than on the lookup itself. Scenario A (persistent connections) sustains 50 RPS, Scenario B (fresh per-request connections) plateaus at 35-38 — the difference is the TLS handshake cost, which a third worker won't help with.

---

## Recommendations

1. **Recommended operating point unchanged at 27 RPS.** Scenario E meets the p99 ≤ 200 ms SLO with 100% success at this rate. The multi-worker headroom buys a wider safety margin to that operating point (~30-40% vs ~10%) — useful for absorbing bursts without rewriting the recommendation.

2. **Per-IP cap unchanged at `120/minute` (2 RPS per IP).** With aggregate ceiling now ~50 RPS, this is roughly 1/25 of the ceiling — comfortable margin, supports up to ~25 simultaneous full-rate anonymous clients before the aggregate degrades. Bumping the cap is reasonable if dashboards show consistent under-utilisation, but not required.

3. **Don't push above `PC2NUTS_WORKERS=2` yet.** The remaining gap between Scenario A (50 RPS) and Scenario B (35 RPS) suggests the bottleneck has shifted from pure compute to TLS+connection setup. Adding a third worker would help only if the platform's TLS termination scales with it — empirical question, but the cheapest first investigation is reusing connections client-side, not adding more workers.

4. **Re-baseline if the topology changes.** Specifically:
   - **Adding a second pod replica** (raising `autoScaling.max` above 1) — would multiply both ceilings, and the rate-limit storage already supports it (Redis is shared per pod today; would need to move to a cross-pod shared service if scaling out).
   - **#7 (UK NSPL, +1.79M postcodes)** — should not change per-request latency materially (still a dict lookup) but doubles in-memory state per worker. Re-run to confirm.

5. **Don't run unattended high-concurrency tests.** Bombardier at c≥100 from a single source still triggers platform-level connection refusal. The `B 50/s` result here (107 × 503) is a milder version of the same edge back-pressure. Keep scripted load below c=80 and below 50 RPS in B-style sweeps.

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
