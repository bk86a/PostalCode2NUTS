# Proposal: selectable NUTS version (2024 / 2027)

Status: **proposal** - no code changes yet. Analysis date 2026-11-19.

## Goal

Let callers choose the NUTS classification a lookup is answered in:

```
GET /lookup?country=BE&postal_code=1000&nuts_version=2027
GET /resolve?...&nuts_version=2024
```

- `nuts_version` is optional, `Literal["2024", "2027"]`, and defaults to a server
  setting (`PC2NUTS_DEFAULT_NUTS_VERSION`, initially `2024`) so existing clients
  see no change.
- The version used is echoed in every response (`nuts_version` field on
  `NUTSResult` / `ResolveResponse`); `/health` reports per-version counts.
- An unknown version is a 422 from FastAPI validation; a known but not loaded
  version (e.g. 2027 disabled by config) is a 503 with a clear message.

## Data availability (probed 2026-11-19)

| Source | NUTS 2024 | NUTS 2027 |
|---|---|---|
| TERCET postal code files | `pc2025_*_NUTS-2024_v1.0.zip` (FI, SI `_v2.0`), 34 countries | `pc2026_*_NUTS-2027_v1.0.zip`, dated 2026-10-23 (BG 2026-10-26), same 34 countries |
| TERCET methodological notes | `2025-...-MET-NOTES-V4.0.pdf` | listed (`2026-GISCO-NUTS2027-PC2026-MET-NOTES-V1.0.pdf`) but the link is dead |
| GISCO NUTS names (`NUTS_AT_<ver>.csv`) | yes | **404**; names are in Eurostat's `NUTS2024-NUTS2027.xlsx` |
| GISCO NUTS-3 polygons (PIP oracle for `/resolve`) | yes | **404** (`datasets.json` tops out at nuts-2024) |
| Estimates (`tercet_missing_codes.csv`) | 7,158 rows | none - must be regenerated |
| Albania block map (`app/albania_blocks.py`) | yes | needs a 2027 review (no TERCET AL file in either version) |
| UK (NSPL -> ITL bridge) | ITL, version-independent | same data can be shared |
| ME (`single_nuts3_fallback`) | ME000 | no TERCET file; fallback must be confirmed |

TERCET has no file for UK, ME or AL in either version; the TERCET page has
listed Albania and Montenegro as a known gap since 2020. NUTS 2027 is
Delegated Regulation (EU) 2026/195 and applies from 2027-01-01. For
comparison, NUTS 2024 geodata appeared on GISCO five months after NUTS 2024
went into force.

Consequence: 2027 can only be offered **without region names and without the
geocode/PIP path** until GISCO publishes the 2027 boundaries. Region names
can be loaded from Eurostat's NUTS 2024->2027 correspondence workbook
(`NUTS2024-NUTS2027.xlsx`, linked from the Eurostat NUTS history page), which
also lists the official code changes.

## Comparison: 2027 TERCET files vs the 2024 lookup table

Compared per postal code against the current 2024 cache (830,033 codes, UK
excluded), using the loader's normalisation (spaces/dashes and country prefix
stripped; IE/MT placeholder suffixes removed).

- 830,020 codes present in both; **11,822 (1.4%) map to a different NUTS3**.
- 29,062 codes are new in 2027, 13 dropped.
- Total 2027 entries: 859,082.

Countries with real boundary changes (new/removed NUTS3 codes):

| Country | Codes reassigned | Change |
|---|---|---|
| BG | 4,880 (100%) | full recode: BG3xx/BG4xx -> BG5xx (28 -> 28 regions) |
| NO | 3,776 (73%) | 12 regions replaced (NO020, NO06x-NO09x -> NO0Cx-NO0Ex) |
| BE | 285 (24%) | 11 regions replaced: Antwerpen BE21x -> BE26x, Oost-Vlaanderen BE23x -> BE27x, Limburg BE223/BE224 -> BE226/BE227 |
| IT | 147 (3%) | Sardinia: ITG2D/E/F/H -> ITG2I-O (4 -> 7); ITG2G unchanged |
| DE | 282 (3%) | 4 regions replaced by 5 (DE719 -> DE71F/G; DEG06/09/0R -> DEG0W-Y). DEB3A also disappears from the data, but that is a TERCET defect (see below) |
| PL | 917 (4%) | PL613 split into PL61A/PL61B |

Other countries only show reassignments of individual codes (SK 149, SE 113,
PT 113, LT 101, AT 81, HR 79, CH 35, ES 33, ...) plus new codes (LT +9,916,
NL +6,811, PT +4,849, DE +2,871, TR +1,716). IS, LI, MT, MK, RO, LU, CY, SI
are unchanged apart from added codes. The 13 dropped codes look like clean-ups
(e.g. Monaco `98000`, Saint-Barthélemy `97133`).

Not all reassignments are improvements. About 1,930 codes moved between NUTS3
regions that NUTS 2027 does not touch. A cross-check against GeoNames
municipality data (not an authoritative reference) suggests:

- **DE:** about 145 of 207 such codes were right in 2024 and are wrong in 2027,
  against 17 corrections. Examples: `81479` München -> Landkreis München,
  `89233` Neu-Ulm -> Ulm, `40489` Düsseldorf -> Duisburg.
- **SE:** about 81 of 113, including 12 Hässelby codes (`165 xx`) moved from
  Stockholm to Södermanland.
- Smaller numbers in ES, IT, CZ, EE and FI. The SK, PL and CH reassignments are
  mostly corrections.
- **NL:** 644 Weesp codes (`1381`-`1384`) moved NL327 -> NL32B with no NL entry
  in the Eurostat change list; possibly intentional.

So 2027 lookups will be different, and not always better, for border postcodes.
These points have been raised with ESTAT GISCO.

### Defects and format changes in the 2027 files

Checked against the 2024 files as a baseline: properties the 2024 files share
(missing ME/AL/UK, zip layout, encoding, HTTP 200 for missing files) are not
listed here. Genuine 2027 defects, which the loader has to tolerate:

- **BG:** 175 rows (lines 2-176, all Sofia) lack the opening quote
  (`BG510';'1000'`); the 2024 files have no malformed rows. The CSV is also
  nested in a folder inside the zip (DE 2024 had the same). It parses fine with
  lenient quote handling and by picking the `.csv` member.
- **Combined file** `PCODE_2026_NUTS-2027_v1.0.zip` has `BG511`, which is not
  a NUTS 2027 code, on the same 175 rows. Use the per-country files.
- **DEB3A** (Zweibrücken) is unchanged in NUTS 2027 but has no postcode: its
  only code, `66482`, moved to DEB3K. It is the only NUTS3 region among the 34
  countries without a postcode.
- **SK:** 3 duplicate rows (`97401`, `97602`, `97634` alongside `974 01`,
  `976 02`, `976 34`, all SK032). Harmless after normalisation.

**Postal code formats.** Comparing the raw code shapes of 2024 and 2027, four
countries changed:

| Country | 2024 | 2027 | Impact |
|---|---|---|---|
| EL | `14100` | `141 00` (the format the 2024 notes already document) | none - `normalize_postal_code` strips the space |
| SK | `999 99` | `999 99`, plus 3 duplicates without space | none |
| IE | `A75` (routing key) | `A75 XXXX` (routing key + placeholder) | **all 139 IE lookups miss** if loaded as-is |
| MT | `ATD` (locality prefix) | `ATD 0000` (prefix + placeholder) | **all 74 MT lookups miss** if loaded as-is |

User input is reduced to the TERCET key by the `tercet_map` rule in
`app/postal_patterns.json` (IE `truncate:3`: `D02 X285` -> `D02`; MT
`keep_alpha`: `VLT 1010` -> `VLT`), so it never matches a stored key of
`A75XXXX` / `ATD0000`. Fix in the loader: run TERCET keys through the same
`tercet_map` at load time, which yields exactly the 2024 keys. With that, the
2027 IE and MT data match 2024 apart from 5 IE reassignments.

**`postal_patterns.json` needs no change.** All 859,085 codes in the 2027
files were run through `extract_postal_code`:

- every 2027 code passes its country's regex (in 2024 the 139 IE and 74 MT
  entries, being bare routing keys, did not);
- the extracted key equals the stored key for every code except IE/MT, which
  is the loader fix above.

Only comments and test docstrings citing "TERCET NUTS-2024"
(`tests/test_postal_patterns.py`) would need version-neutral wording.

**Cross-border NUTS3 codes** (new in 2027; the 2024 files have none, and the
2024 notes say codes are matched to regions of the same country): NL `5111 WB`
-> `BE263` (Baarle enclave, NL415 in 2024), RS `38218`/`38220` -> `XK002`, TR
`99000`/`99460`/`99628` -> `CY000`. These are plausible, but may be revised.
The loader should accept them, but NUTS1/NUTS2 derivation and name lookup must
not assume the NUTS prefix matches the request country.

## Design

### Data layer

`app/data_loader.py` keeps all lookup state in module-level singletons
(`_lookup`, `_estimates`, `_prefix_index`, `_sorted_codes`, `_nuts_names`,
`_single_nuts3`, `_country_fallback`, `_outward_lookup`, ...). Proposal:

1. Move them into a `VersionData` dataclass; keep `dict[str, VersionData]`
   keyed by version. The existing accessors take an optional `version`
   argument defaulting to the configured default, so call sites and tests
   change minimally.
2. Replace `settings.tercet_base_url` + derived `nuts_version` with
   `PC2NUTS_NUTS_VERSIONS=2024,2027` and a URL template
   (`.../tercet/NUTS-{ver}/`). Per-version: estimates CSV path, names URL,
   polygon URL, `single_nuts3_fallback`.
3. One SQLite cache per version already works (`postalcode2nuts_NUTS-{ver}.db`).
   Polygon cache name (`ref-nuts-polygons.geojson.zip`) must gain the version.
4. Add `pc2026` to the guessed TERCET years (directory discovery would find
   it anyway).
5. Build per-version state into fresh objects and swap the dict entry
   atomically, instead of the current `.clear()` + refill.
6. UK/NSPL data is version-independent (ITL) and the largest table (~1.8M
   rows): share one copy across versions.

### API

- `nuts_version` query param on `/lookup` and `/resolve`; echoed in responses.
- `/resolve` with a version that has no polygons skips the PIP path and says
  so (`geocode: unavailable_for_version`), never falls back to 2024 silently.
- No version mixing: tiers (TERCET, estimates, Albania blocks, fallbacks)
  only ever use data of the requested version.
- HTTP caching is keyed on the full URL, so no change; Redis is used only for
  rate limiting.

### Memory

Today ~150-200 MB RSS per worker (6 workers in prod). Without UK the NUTS
tables are ~830k rows; a second version adds roughly the same again plus
another prefix index. With NSPL shared, expect +60-80 MB per worker; the
2027 polygons (when published) add another STRtree per worker. Needs a
measurement on the real image before enabling 2027 in production
(`/admin/memory` already reports table sizes).

## Open questions

1. Default version: stay on 2024 until 1 January 2027 (or until names and
   polygons exist), then switch? Or require callers to opt in for good?
2. Load 2027 region names from the Eurostat correspondence workbook, or wait
   for GISCO's `NUTS_AT_2027.csv`?
3. Estimates for 2027: regenerate with the same geocoding pipeline once
   polygons exist, or derive via the 2024->2027 correspondence?
4. Should responses for reassigned codes carry a hint (e.g. `changed_since`)?

## Rollout

1. Refactor to `VersionData` with 2024 only - no behaviour change, full test
   suite green.
2. Add the parameter + 2027 TERCET loading (lookup only, no names/PIP),
   behind `PC2NUTS_NUTS_VERSIONS`.
3. Measure memory on the prod image; enable 2027.
4. Add polygons and estimates for 2027 when GISCO publishes the boundaries;
   re-download the TERCET files if GISCO corrects the defects above.
