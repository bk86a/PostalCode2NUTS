"""Enrich the organisations CSV with NUTS via the deployed PostalCode2NUTS API.

For a row range [start, end) of the input CSV, call the live API for each row:
  * /lookup (every row)            -> NUTS3_POSTAL, POSTAL_MATCH_TYPE, POSTAL_CONFIDENCE
  * /resolve (weak rows w/ address) -> NUTS3_GEOCODED, GEOCODE_STATUS  (address->Photon->PIP)
and derive NUTS3_FINAL + a clear RESOLUTION_METHOD per row.

"Weak" = postal match_type == not_found OR nuts3_confidence < 0.85.

Auth: sends the operator Bearer token on every call (bypasses the 1/s anon limit).
Resumable: if the output file already has rows, their OIDs are skipped.
Offline analysis tool — NOT imported by the served app; output holds addresses (PII)
so it belongs under local-data/ (gitignored).
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

OUT_FIELDS = [
    "OID", "COUNTRY_CD", "CITY", "STREET_NAME_AND_NUMBER", "POSTAL_CODE",
    "NUTS3_POSTAL", "POSTAL_MATCH_TYPE", "POSTAL_CONFIDENCE",
    "NUTS3_GEOCODED", "GEOCODE_STATUS",
    "NUTS3_FINAL", "RESOLUTION_METHOD",
]

WEAK_THRESHOLD = 0.85

# Per-country postal validation regex cache (fetched from the API's /pattern endpoint).
_PATTERN_CACHE: dict[str, str | None] = {}


def loose_extract_postal(regex: str | None, raw: str) -> str:
    """Pull a postal-code-shaped token out of a messy POSTAL_CODE field.

    The API's per-country regexes are anchored (``^...$``) so they only *validate*
    an already-clean code; a field that carries extra text — e.g.
    ``"37067 Valeggio Sul Mincio VR"`` or ``"Lorentzstraat 212 1971 HX Ijmuiden"`` —
    fails validation and the /lookup call returns 422. Here we drop the anchors and
    (1) match at the *start* of the field (the code almost always leads it), then
    (2) fall back to searching anywhere (codes that trail the street, NL-style).
    Returns the matched token, or "" if nothing matches. /lookup then does its own
    normalization on whatever token we hand back.
    """
    if not regex or not raw:
        return ""
    body = regex
    if body.startswith("^"):
        body = body[1:]
    if body.endswith("$"):
        body = body[:-1]
    try:
        pat = re.compile(body, re.IGNORECASE)
    except re.error:
        return ""
    up = raw.strip().upper()
    m = pat.match(up) or pat.search(up)
    return m.group(0) if m else ""


def accept_geocode(country: str, nuts3: str) -> bool:
    """Guard against cross-border geocode errors.

    A geocoded NUTS3 for a given country must carry that country's NUTS prefix.
    Photon occasionally matches an ambiguous address to the wrong country (observed:
    an Albanian street resolving to DEG01/Stuttgart, a Serbian one to PL217/Poland);
    such results are discarded rather than trusted, since a NUTS3 code always begins
    with its country's two-letter code.
    """
    return bool(nuts3) and bool(country) and nuts3.upper().startswith(country.upper())


def _country_regex(
    client: httpx.Client, base: str, headers: dict, timeout: float, country: str
) -> str | None:
    """Fetch (and cache) a country's postal validation regex from the /pattern endpoint."""
    if country in _PATTERN_CACHE:
        return _PATTERN_CACHE[country]
    rx: str | None = None
    try:
        st, body = _get(client, f"{base}/pattern", {"country": country}, headers, timeout)
        if st == 200 and body:
            rx = body.get("regex")
    except RuntimeError:
        rx = None
    _PATTERN_CACHE[country] = rx
    return rx


def _get(client: httpx.Client, url: str, params: dict, headers: dict, timeout: float):
    """GET with a few retries on transport errors; returns (status_code, json|None)."""
    last_exc = None
    for attempt in range(4):
        try:
            r = client.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code >= 500:
                last_exc = f"HTTP {r.status_code}"
                time.sleep(0.5 * (attempt + 1))
                continue
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, None
        except httpx.HTTPError as exc:
            last_exc = str(exc)
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"request failed after retries: {last_exc}")


def process_row(row: dict, client: httpx.Client, base: str, headers: dict, timeout: float) -> dict:
    country = (row.get("COUNTRY_CD") or "").strip()
    pc = (row.get("POSTAL_CODE") or "").strip()
    street = (row.get("STREET_NAME_AND_NUMBER") or "").strip()
    city = (row.get("CITY") or "").strip()

    out = {k: (row.get(k) or "") for k in
           ("OID", "COUNTRY_CD", "CITY", "STREET_NAME_AND_NUMBER", "POSTAL_CODE")}
    nuts3_postal = match_type = conf = ""
    nuts3_geocoded = geocode_status = ""

    # --- postal path (with one sanitization retry on a 422) ---
    st, body = _get(client, f"{base}/lookup", {"country": country, "postal_code": pc}, headers, timeout)
    if st == 422 and pc:
        # /lookup rejected the raw code — it likely carries extra text (city, street).
        # Pull out the code-shaped token and retry once with it.
        cand = loose_extract_postal(_country_regex(client, base, headers, timeout, country), pc)
        if cand and cand != pc:
            st2, body2 = _get(client, f"{base}/lookup",
                              {"country": country, "postal_code": cand}, headers, timeout)
            if st2 != 422:  # accept the sanitized retry only once it clears validation
                st, body, pc = st2, body2, cand
    if st == 200 and body:
        nuts3_postal = body.get("nuts3") or ""
        match_type = body.get("match_type") or ""
        c = body.get("nuts3_confidence")
        conf = "" if c is None else f"{c:.2f}"
    elif st == 404:
        match_type = "not_found"
    elif st == 400:
        match_type = "unsupported"
    else:
        match_type = f"error_{st}"

    # --- decide routing ---
    # "weak" now also covers residual postal errors (a malformed/rejected code) so a
    # row with a real street/city still gets a geocode attempt instead of being dropped.
    weak = (match_type == "not_found" or match_type.startswith("error_")
            or (conf != "" and float(conf) < WEAK_THRESHOLD))
    has_address = bool(street or city)
    routable = match_type != "unsupported"

    if weak and has_address and routable:
        st2, body2 = _get(
            client, f"{base}/resolve",
            {"country": country, "postal_code": pc, "street": street, "city": city},
            headers, timeout,
        )
        if st2 == 200 and body2:
            g = body2.get("geocode") or {}
            geocode_status = g.get("status") or ""
            if geocode_status in ("ok", "snapped"):  # snapped = nearest-polygon rescue
                cand = g.get("nuts3") or ""
                if accept_geocode(country, cand):
                    nuts3_geocoded = cand
                else:
                    geocode_status = "wrong_country"  # cross-border miss — discard
        else:
            geocode_status = f"error_{st2}"
    elif weak and not has_address and routable:
        geocode_status = "no_address"
    elif not routable:
        geocode_status = "unsupported"
    else:
        geocode_status = "not_attempted"

    # --- derive final + method ---
    if nuts3_geocoded:
        nuts3_final, method = nuts3_geocoded, "geocoded"
    elif nuts3_postal:
        nuts3_final, method = nuts3_postal, f"postal:{match_type}"
    elif match_type == "unsupported":
        nuts3_final, method = "", "unsupported"
    else:
        nuts3_final, method = "", "unresolved"

    out.update({
        "NUTS3_POSTAL": nuts3_postal, "POSTAL_MATCH_TYPE": match_type, "POSTAL_CONFIDENCE": conf,
        "NUTS3_GEOCODED": nuts3_geocoded, "GEOCODE_STATUS": geocode_status,
        "NUTS3_FINAL": nuts3_final, "RESOLUTION_METHOD": method,
    })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--base-url", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=15.0)
    args = ap.parse_args()

    base = args.base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {args.token}"}

    with open(args.input, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    end = args.end if args.end is not None else len(rows)
    chunk = rows[args.start:end]

    # resume: skip OIDs already present in the output
    done: set[str] = set()
    try:
        with open(args.output, newline="", encoding="utf-8") as f:
            done = {r["OID"] for r in csv.DictReader(f)}
    except FileNotFoundError:
        pass
    todo = [r for r in chunk if (r.get("OID") or "") not in done]
    print(f"range [{args.start}:{end}) = {len(chunk)} rows; {len(done)} already done; {len(todo)} to do",
          file=sys.stderr, flush=True)

    write_lock = threading.Lock()
    counts: dict[str, int] = {}
    processed = 0
    t0 = time.monotonic()

    new_file = not done
    with open(args.output, "a", newline="", encoding="utf-8") as fout:
        writer = csv.DictWriter(fout, fieldnames=OUT_FIELDS)
        if new_file:
            writer.writeheader()
            fout.flush()

        client = httpx.Client(
            limits=httpx.Limits(max_connections=args.concurrency + 2, max_keepalive_connections=args.concurrency + 2)
        )

        def work(row):
            return process_row(row, client, base, headers, args.timeout)

        with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
            for rec in ex.map(work, todo):
                with write_lock:
                    writer.writerow(rec)
                    counts[rec["RESOLUTION_METHOD"]] = counts.get(rec["RESOLUTION_METHOD"], 0) + 1
                    processed += 1
                    if processed % 500 == 0:
                        fout.flush()
                        rate = processed / (time.monotonic() - t0)
                        print(f"  {processed}/{len(todo)} ({rate:.0f}/s)", file=sys.stderr, flush=True)
        client.close()

    elapsed = time.monotonic() - t0
    print(f"DONE {processed} rows in {elapsed:.1f}s ({processed/max(elapsed,0.001):.0f}/s)", file=sys.stderr)
    print("method distribution:", file=sys.stderr)
    for m, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"  {m}: {n}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
