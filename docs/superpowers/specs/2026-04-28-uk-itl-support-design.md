# UK postcode and ITL support — design

**Status:** approved (brainstorming complete)
**Date:** 2026-04-28
**Issue:** [#7](https://github.com/bk86a/PostalCode2NUTS/issues/7)
**Scope:** add support for UK postcodes via the ONS NSPL dataset, mapping to ITL (International Territorial Level) codes, exposed through the existing `/lookup` API with a new `code_system` discriminator.

---

## 1. Goals and non-goals

### Goals

- Accept UK postcodes (e.g. `SW1A 2AA`) on `/lookup` and return ITL1/2/3 codes (e.g. `TLI`, `TLI3`, `TLI32`).
- Accept input under either `country=UK` or `country=GB`.
- Handle both full postcodes and outward-code-only input (`SW1A`).
- Make the NUTS-vs-ITL distinction explicit in API responses via a new `code_system` field.
- Reuse the existing five-tier lookup waterfall, the same in-memory dict, the same SQLite cache, and the same configuration model.
- Keep failure of the UK data path independent from TERCET (and vice versa) so neither blocks service startup.

### Non-goals

- Crown Dependencies and Gibraltar (JE, GG, IM, GI) — out of scope; rejected with the standard 400.
- ONSPD as an alternative source — NSPL is preferred for ONS-recommended best-fit allocation.
- Backwards-compatibility shim translating ITL codes to NUTS-2016 UK equivalents.
- Per-source TTL configuration (only added if conditional GET against ONS turns out unworkable).
- Implementation work — this document is the design only.

---

## 2. Background

### NSPL data source

**National Statistics Postcode Lookup** from the ONS Open Geography Portal:

- ~1.79 million live UK postcodes, each mapped to ITL3.
- Quarterly releases (February, May, August, November).
- ~178 MB compressed ZIP, no authentication, Open Government Licence v3.0.
- CSV columns of interest: `pcds` (formatted postcode), `itl` (ITL3 code), `doterm` (termination date — blank for live codes).
- ITL region names are not in NSPL — they come from three separate ONS "Names and Codes" CSVs (~232 rows total).
- NSPL chosen over ONSPD because best-fit allocation via Census Output Areas is the ONS-recommended approach for statistical purposes.

### ITL vs NUTS divergence

The Overview of issue #7 states "same boundaries, `UK` prefix changed to `TL`". This is no longer accurate:

| Level | NUTS 2016 (UK) | ITL 2021 | Delta |
|-------|---------------:|---------:|------:|
| L1    | 12             | 12       | 0     |
| L2    | 40             | 41       | +1    |
| L3    | 174            | 179      | +5    |

ONS published bidirectional NUTS↔ITL lookup files until 2023, after which they were discontinued. A 2025 ONS revision is also in preparation. Consumers comparing UK results against historical NUTS-UK data therefore cannot assume drop-in equivalence — hence the `code_system` discriminator.

### Existing architecture this design plugs into

- TERCET ZIPs downloaded per-country at startup, parsed by `_parse_csv_content`, stored in `_lookup[(country, normalised_postcode)] = nuts3_code`.
- SQLite cache scoped by NUTS version (e.g. `postalcode2nuts_NUTS-2024.db`); TTL-checked, atomically written.
- Five-tier lookup waterfall: exact → curated estimate → runtime prefix approximation → country-level majority vote → single-NUTS3 country fallback.
- `nuts_names` table loaded from a separate GISCO CSV.
- Country alias precedent: `GR → EL` already implemented for Greece.
- Eircode precedent for variable-length lookups: `tercet_map: truncate:3` slices the routing key.

---

## 3. Architecture

UK is treated as a parallel data channel rather than a 35th GISCO country. It uses the same in-memory `_lookup` dict and the same waterfall, but is loaded by a separate code path:

```
startup
├── TERCET loader (existing)
│   ├── discover countries from GISCO directory listing
│   ├── per-country ZIP download → _parse_csv_content → _lookup
│   └── failure → fall back to stale cache, set data_stale
└── NSPL loader (new)
    ├── fetch NSPL ZIP from configured URL (conditional GET)
    ├── _parse_csv_content with NSPL column aliases (pcds, itl) + doterm filter
    ├── populate _lookup[("UK", normalized_pc)] = ITL3
    ├── build outward-code index: _outward_lookup[("UK", outward)] = majority ITL3
    ├── load ITL names from ONS Names-and-Codes CSVs into _nuts_names
    └── failure → fall back to stale cache, do not block service
```

**Invariants:**

- Single unified lookup dict. `lookup()` does not branch on country except for the `GB → UK` alias and the new outward-code tier.
- NSPL failure must not block TERCET data from serving (and vice versa). Both already use the stale-cache fallback; NSPL hooks into the same mechanism.
- NSPL cache lives in the same SQLite DB as TERCET, tagged with its own source identifier so a TERCET-only deployment (NSPL URL unset) still works.
- The `code_system` of each lookup row is recorded at load time, not derived at query time.
- **`UK` is NOT added to `settings.countries`.** That list drives GISCO discovery and Strategy-2 URL guessing in the TERCET loader (`data_loader._guess_zip_urls_for_country`). Adding UK there would cause every cold start to attempt non-existent `pc{YYYY}_UK_NUTS-*.zip` URLs against GISCO, wasting startup latency and timeout budget. UK loading is gated on `settings.nspl_url` instead.

---

## 4. Data acquisition

| Concern | Decision |
|---------|----------|
| **NSPL URL source** | Configured via `nspl_url` in `app/settings.json`, overridable via `PC2NUTS_NSPL_URL`. Operator updates quarterly. If unset, NSPL loader is a no-op (TERCET-only deployment). |
| **Conditional GET** | Extend the existing TERCET downloader to send `If-Modified-Since` / `If-None-Match` based on cached `Last-Modified` / `ETag`. On `304 Not Modified`, skip re-parse. Applied to both TERCET and NSPL — free win for both. |
| **`doterm` filter** | `_parse_csv_content` gains a `skip_terminated: bool = False` flag. When true, rows where the `DOTERM` column is non-blank are skipped. NSPL loader sets it to true. |
| **Column aliases** | Extend the existing alias lists in `_parse_csv_content`: postal code adds `"PCDS"`; NUTS3 candidates add `"ITL"`, `"ITL3"`, `"ITL3CD"`. No separate parser path. |
| **ITL names** | Three ONS "Names and Codes" CSVs (one per ITL level, ~232 rows total). URLs supplied via `PC2NUTS_ITL_NAMES_URLS` (comma-separated) — exposed as a plain string field on `Settings` plus a `itl_names_url_list` property that splits it, mirroring the existing `extra_sources` / `extra_source_urls` pattern. Loaded into the existing `nuts_names` table. The current `_NUTS3_RE` pattern (`^[A-Z]{2}[A-Z0-9]{1,3}$`) already accepts `TLxNN`. |
| **TTL** | Shared `PC2NUTS_DB_CACHE_TTL_DAYS`. Conditional GET keeps the wasted-refresh cost near zero. Per-source TTL deferred unless conditional GET is unsupported by ONS. |

---

## 5. Lookup waterfall — new tier 3.5

Insert between Tier 3 (runtime prefix approximation) and Tier 4 (country-level majority vote):

> **Tier 3.5: Outward-code lookup (`match_type: "estimated"`)**
>
> If the input postcode is shorter than a full UK postcode (no inward portion) **or** Tier 3 prefix approximation found nothing for UK, look up `(country, outward_code)` in `_outward_lookup`. Returns the majority-vote ITL3 for that outward code with `medium`-tier confidence (NUTS1 0.90, NUTS2 0.80, NUTS3 0.70).

The outward-code index is built once at NSPL load time:

1. For each NSPL row, slice off the last 3 chars of the normalised postcode → outward.
2. Group by `(UK, outward)` → list of ITL3 codes → majority vote → store winner + agreement ratio.

Implementation:

- New `tercet_map` action `outward_only` on the UK pattern, evaluated in `extract_postal_code` to produce both the full normalised form (for Tier 1) and the outward form (for Tier 3.5).
- The tier is parameterised by country; UK enables it. Easy to extend to IE later if desired.

---

## 6. Response schema

Single additive field on `LookupResponse`:

```python
code_system: Literal["NUTS", "ITL"]
```

- All 34 GISCO countries → `"NUTS"`.
- `UK` (or `GB` via alias) → `"ITL"`.

`nuts1/2/3` field names stay; for UK they hold TL-prefixed values. No breaking changes for existing consumers (additive). README's example response gains the new field.

---

## 7. Configuration changes

| File | Change |
|------|--------|
| `app/settings.json` | Add `nspl_url: ""` (default empty). **Do NOT add `"UK"` to `countries`** — that list is GISCO-only; adding UK there would trigger wasted GISCO URL guesses on every cold load. |
| `app/postal_patterns.json` | Add UK entry with the issue's proposed regex + new `tercet_map: "outward_only"`. Bump `_meta.version` from `1.0` → `1.1`. |
| `app/config.py` | New string fields `nspl_url: str = ""` and `itl_names_urls: str = ""` (env vars `PC2NUTS_NSPL_URL`, `PC2NUTS_ITL_NAMES_URLS`). New property `itl_names_url_list` that splits the comma-separated string — mirrors the existing `extra_sources` / `extra_source_urls` pattern. No `Field(alias=...)` indirection. |
| `app/data_loader.py` | Column alias extension, `doterm` filter flag, NSPL loader, outward-code index builder, ITL names loader, conditional-GET in HTTP layer, `code_system` attribution per source. |
| `app/main.py` | `GB → UK` alias in `/lookup`; new field in response model. |
| `app/models.py` | Add `code_system` field with description. |

UK regex (from issue body, unchanged):

```json
"UK": {
    "regex": "^([A-Z]{1,2}[0-9][0-9A-Z]?\\s?[0-9][A-Z]{2})$",
    "example": "SW1A 2AA, EC1A 1BB, M1 1AA, B33 8TH",
    "tercet_map": "outward_only"
}
```

---

## 8. Documentation updates

In `README.md`:

- **Coverage**: split into "EU-27 / EFTA / Candidates / United Kingdom (ITL via NSPL)". The UK subsection makes the data-source distinction explicit.
- **Supported patterns table**: add UK row.
- **New ITL/NUTS divergence note** (1 paragraph): explain that ITL diverges from NUTS 2016 UK at L2 and L3 (41 vs 40 ITL2 regions; 179 vs 174 ITL3 regions); that ONS discontinued NUTS↔ITL lookups in 2023; that the `code_system` field on `/lookup` responses identifies which scheme applies.
- **Endpoints**: add a UK example response showing `code_system: "ITL"` and a `TLxxx` value.
- **Five-tier lookup → six-tier**: document new Tier 3.5 (outward-code).
- **Configuration table**: add `PC2NUTS_NSPL_URL`, `PC2NUTS_ITL_NAMES_URLS`.
- **Data source section**: add ONS/NSPL attribution under OGL v3.0 alongside the existing GISCO CC-BY-SA 4.0 line.
- **Adding a new country**: note that NSPL-style sources require a separate loader path, not as simple as adding to GISCO patterns.

---

## 9. Operational impact

- **Memory**: roughly +150-200 MB in-process (per issue body's estimate). Confirm during implementation.
- **SQLite cache**: roughly ×3 disk size (current ~830K codes → ~2.6M codes). Note in deployment docs; existing volume sizing handles this comfortably.
- **First-load startup**: +10-30s estimated to parse 1.79M extra rows on cold start. Warm restart unchanged thanks to SQLite cache.
- **Refresh bandwidth**: collapsed to ~304s after first full fetch via conditional GET (assuming ONS supports it; if not, fall back to per-source TTL).
- **Revalidation pass**: measure at implementation time; if >5s wall-clock, scope revalidation per-country rather than re-architect.

---

## 10. Out of scope (explicit)

- **Crown Dependencies and Gibraltar** (JE, GG, IM, GI) — rejected with standard 400; documented in README as out of scope. If ever needed, just extend the country list.
- **ONSPD** — NSPL chosen instead for ONS-recommended best-fit allocation; documented in data-source section.
- **NUTS↔ITL translation shim** — not implemented. Consumers branch on `code_system` instead.
- **ITL 2025 revision** — monitor; consume automatically once NSPL adopts it (no code change expected if columns stay stable).
- **Per-source TTL config** — not added unless conditional GET against ONS turns out unsupported.

---

## 11. Decisions log (from brainstorming session)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| Q1 | ITL parity framing | Honest + discriminator | ITL has empirically diverged from NUTS-2016 UK at L2/L3; mislabelling would surprise consumers. |
| Q2 | Outward-code fallback | Add as Tier 3.5 in v1 | High real-world UK input value; IE precedent makes implementation pattern clear. |
| Q3 | Crown Deps and Gibraltar | Reject with standard 400 | Not in NSPL; not in ITL geography; explicit failure preferred over silent miscategorisation. |
| Q4 | `code_system` discriminator | Yes (additive field) | Closes Q1 cleanly; no breaking change. |
| Q5 | Refresh cadence | Shared TTL + conditional GET | Avoids new config surface; benefits TERCET too. |
| Q6 | Licence attribution | README only | `/health` doesn't currently expose licence info; no need to add a new surface. |
| Q7 | `patterns_version` bump | `1.0 → 1.1` | Additive change (new country, no existing pattern altered). |
| Q8 | Country auto-discovery | Separate code path for NSPL | GISCO directory listing won't include UK; failure must not block TERCET startup. |
| Q9 | Estimates revalidation cost | Measure during implementation; per-country scoping if >5s | Low likelihood of regression; defer optimisation until measured. |

### Post-spec corrections (Codex review on PR #52, 2026-04-29)

| # | Concern | Fix |
|---|---------|-----|
| C1 | `Field(alias="ITL_NAMES_URLS")` interacts unreliably with `pydantic-settings` `env_prefix` — the operator-facing `PC2NUTS_ITL_NAMES_URLS` env var would not consistently populate the field. | Use the existing `extra_sources` precedent: a plain `str` field plus a separately-named property that parses the comma-separated value. No alias; env var name follows from the field name + prefix. |
| C2 | Adding `"UK"` to `settings.countries` makes the GISCO loader's Strategy-2 URL-guessing iterate over UK on every cold load, attempting non-existent `pc{YYYY}_UK_NUTS-*.zip` URLs. | UK is loaded by the dedicated NSPL path; do not add it to `settings.countries`. The new architecture invariant in §3 makes this explicit. |
