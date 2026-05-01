#!/usr/bin/env bash
# Performance test harness for the PostalCode2NUTS service.
#
# Discovers max sustainable RPS, characterises the latency curve, and verifies
# stability at the chosen operating point. See docs/performance.md for the
# methodology and the most recent measured results.
#
# Required env vars:
#   PC2NUTS_TARGET   Base URL of the service (no trailing slash). Example:
#                    https://example.invalid
#   PC2NUTS_TOKEN    Trusted-token value granting rate-limit bypass. Issue with:
#                    python -m scripts.tokens add --label "perf-test-YYYY-MM-DD"
#
# Optional env vars:
#   OUTDIR           Output directory for raw results (default: /tmp/perf).
#   CORPUS_COUNTRIES Space-separated CC list to pull from GISCO TERCET (default:
#                    "BE AT IE LU EE" — small files, fast download).
#   SCENARIOS        Subset of "warm A B C D E" (default: all).
#
# Required tools on PATH: bombardier, vegeta, curl, python3.
#
# Stop conditions: any 5xx >1%, p99 >5s, or any 429 → halt and inspect output.
set -euo pipefail

: "${PC2NUTS_TARGET:?PC2NUTS_TARGET (e.g. https://example.invalid) is required}"
: "${PC2NUTS_TOKEN:?PC2NUTS_TOKEN is required (use scripts/tokens.py add)}"
OUTDIR="${OUTDIR:-/tmp/perf}"
CORPUS_COUNTRIES="${CORPUS_COUNTRIES:-BE AT IE LU EE}"
SCENARIOS="${SCENARIOS:-warm A B C D E}"
HEADER="Authorization: Bearer ${PC2NUTS_TOKEN}"

mkdir -p "${OUTDIR}"
CORPUS_DIR="${OUTDIR}/corpus"
mkdir -p "${CORPUS_DIR}"

# --- Build the corpus from public TERCET ZIPs --------------------------------
build_corpus() {
    echo "Building corpus from GISCO TERCET (${CORPUS_COUNTRIES})..."
    for cc in ${CORPUS_COUNTRIES}; do
        for yr in 2025 2024 2023; do
            url="https://gisco-services.ec.europa.eu/tercet/NUTS-2024/pc${yr}_${cc}_NUTS-2024_v1.0.zip"
            tmp="${CORPUS_DIR}/${cc}.zip"
            curl -sf -o "${tmp}" "${url}" || continue
            if [ "$(file -b --mime-type "${tmp}")" = "application/zip" ]; then
                (cd "${CORPUS_DIR}" && unzip -oq "${cc}.zip")
                break
            fi
            rm -f "${tmp}"
        done
    done
    python3 - <<'PYEOF'
import csv, os, random, re
random.seed(20260430)
corpus_dir = os.environ["CORPUS_DIR"]
target = os.environ["PC2NUTS_TARGET"]
gen_invalid = {
    "BE": lambda: f"{random.randint(1000,9999):04d}",
    "AT": lambda: f"{random.randint(1000,9999):04d}",
    "EE": lambda: f"{random.randint(10000,99999):05d}",
    "LU": lambda: f"{random.randint(1000,9999):04d}",
}
by_cc = {}
for fn in sorted(os.listdir(corpus_dir)):
    m = re.match(r"pc\d+_([A-Z]{2})_.*\.csv$", fn)
    if not m: continue
    cc = m.group(1)
    codes = set()
    with open(os.path.join(corpus_dir, fn), encoding="utf-8-sig") as f:
        r = csv.reader(f, delimiter=";")
        next(r, None)
        for row in r:
            if len(row) >= 2 and (code := row[1].strip().strip("'")):
                codes.add(code)
    by_cc[cc] = codes
print(f"Loaded: {[(cc, len(c)) for cc, c in by_cc.items()]}")
valid = []
per_cc = max(1, 5000 // len(by_cc))
for cc, codes in by_cc.items():
    valid.extend((cc, c) for c in random.sample(sorted(codes), min(per_cc, len(codes))))
random.shuffle(valid)
invalid, attempts = [], 0
ccs = [cc for cc in gen_invalid if cc in by_cc]
while len(invalid) < 500 and attempts < 50_000:
    attempts += 1
    cc = random.choice(ccs)
    pc = gen_invalid[cc]()
    if pc not in by_cc[cc]:
        invalid.append((cc, pc))
with open(os.path.join(corpus_dir, "..", "targets_B.txt"), "w") as f:
    for cc, pc in valid:
        f.write(f"GET {target}/lookup?country={cc}&postal_code={pc}\n\n")
mix = []
for i in range(max(len(valid), len(invalid))):
    if i < len(valid): mix.append(valid[i])
    if i < len(invalid): mix.append(invalid[i])
with open(os.path.join(corpus_dir, "..", "targets_C.txt"), "w") as f:
    for cc, pc in mix:
        f.write(f"GET {target}/lookup?country={cc}&postal_code={pc}\n\n")
with open(os.path.join(corpus_dir, "..", "targets_D.txt"), "w") as f:
    f.write(f"GET {target}/health\n")
print(f"valid={len(valid)} invalid={len(invalid)} mix={len(mix)}")
PYEOF
}

run_warm() {
    echo "=== warm: 500 sequential mixed lookups ==="
    # vegeta target files have 2-line entries (GET URL\n\n), so picking by raw
    # line number lands on a blank line half the time → curl gets an empty URL
    # → set -e kills the script. Extract just the GET URLs first.
    local urls=()
    mapfile -t urls < <(awk 'NR%2==1 && /^GET / {print substr($0, 5)}' "${OUTDIR}/targets_B.txt")
    if [ "${#urls[@]}" -eq 0 ]; then
        echo "warm: no targets in ${OUTDIR}/targets_B.txt" >&2
        return 1
    fi
    local errors=0
    for _ in $(seq 1 500); do
        local idx=$((RANDOM % ${#urls[@]}))
        local code
        code=$(curl -s -o /dev/null -w "%{http_code}" -H "${HEADER}" "${urls[$idx]}")
        [ "${code}" = "200" ] || errors=$((errors + 1))
    done
    echo "warm complete; errors=${errors}"
}

run_A() {
    echo "=== A: hot-key saturation sweep (BE 3080, c={5,10,20,40,80} × 20s) ==="
    local URL="${PC2NUTS_TARGET}/lookup?country=BE&postal_code=3080"
    for c in 5 10 20 40 80; do
        echo "-- A: -c ${c} --"
        bombardier -c "${c}" -d 20s -l --timeout 30s -H "${HEADER}" "${URL}" \
            | tee "${OUTDIR}/A_c${c}.txt" \
            | grep -E "Reqs/sec|Latency|^     [0-9]+%|HTTP codes|^    [0-9xa-z]" || true
        sleep 10
    done
}

run_B() {
    echo "=== B: random-corpus rate sweep (10/20/25/30/35 RPS × 20s) ==="
    for r in 10 20 25 30 35; do
        echo "-- B: ${r}/s --"
        vegeta attack -duration=20s -rate="${r}/s" -header="${HEADER}" \
            -targets="${OUTDIR}/targets_B.txt" > "${OUTDIR}/B_r${r}.bin"
        vegeta report -type=text "${OUTDIR}/B_r${r}.bin" | tee "${OUTDIR}/B_r${r}.txt"
        sleep 10
    done
}

run_C() {
    echo "=== C: 50/50 hit-miss mix at 25/s × 20s (Tier 3 fallback cost) ==="
    vegeta attack -duration=20s -rate=25/s -header="${HEADER}" \
        -targets="${OUTDIR}/targets_C.txt" > "${OUTDIR}/C_r25.bin"
    vegeta report -type=text "${OUTDIR}/C_r25.bin" | tee "${OUTDIR}/C_r25.txt"
    sleep 10
}

run_D() {
    echo "=== D: /health at 25/s × 20s (FastAPI/uvicorn floor) ==="
    vegeta attack -duration=20s -rate=25/s -header="${HEADER}" \
        -targets="${OUTDIR}/targets_D.txt" > "${OUTDIR}/D_r25.bin"
    vegeta report -type=text "${OUTDIR}/D_r25.bin" | tee "${OUTDIR}/D_r25.txt"
    sleep 10
}

run_E() {
    echo "=== E: sustained at 27/s for 3 min (90% of knee, stability check) ==="
    vegeta attack -duration=3m -rate=27/s -header="${HEADER}" \
        -targets="${OUTDIR}/targets_B.txt" > "${OUTDIR}/E_r27.bin"
    vegeta report -type=text "${OUTDIR}/E_r27.bin" | tee "${OUTDIR}/E_r27.txt"
    vegeta report -type='hist[0,50ms,100ms,200ms,500ms,1s,2s,5s]' \
        "${OUTDIR}/E_r27.bin" | tee -a "${OUTDIR}/E_r27.txt"
}

# --- main --------------------------------------------------------------------
export CORPUS_DIR PC2NUTS_TARGET
[ -s "${OUTDIR}/targets_B.txt" ] || build_corpus

for s in ${SCENARIOS}; do
    case "${s}" in
        warm) run_warm ;;
        A)    run_A ;;
        B)    run_B ;;
        C)    run_C ;;
        D)    run_D ;;
        E)    run_E ;;
        *)    echo "unknown scenario: ${s}" >&2; exit 2 ;;
    esac
done

echo
echo "Done. Raw outputs in ${OUTDIR}/"
echo "Remember to revoke the trusted token:"
echo "  python -m scripts.tokens revoke <id>"
