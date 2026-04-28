# UK postcode and ITL support — implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add UK postcode support backed by the ONS NSPL dataset, with results returned as ITL codes via the existing `/lookup` endpoint and a new `code_system` discriminator.

**Architecture:** UK is a parallel data channel that reuses the same in-memory `_lookup` dict, the same SQLite cache, and the same lookup waterfall as TERCET. The NSPL loader runs alongside the TERCET loader at startup with independent failure handling. A new Tier 3.5 outward-code lookup catches partial UK input (`SW1A` instead of `SW1A 2AA`).

**Tech Stack:** Python 3.12, FastAPI, pydantic, httpx, sqlite3, pytest. Existing patterns mirrored throughout.

**Spec:** [`docs/superpowers/specs/2026-04-28-uk-itl-support-design.md`](../specs/2026-04-28-uk-itl-support-design.md)

**Issue:** [#7](https://github.com/bk86a/PostalCode2NUTS/issues/7)

---

## File-by-file overview

| File | Purpose | Touched by tasks |
|------|---------|------------------|
| `app/models.py` | Add `code_system` field to `NUTSResult` | 1 |
| `app/postal_patterns.json` | Add UK entry; bump `_meta.version` to `1.1` | 2 |
| `app/postal_patterns.py` | Implement `outward_only` action + `extract_outward` helper | 3 |
| `app/data_loader.py` | NSPL column aliases, `doterm` filter, conditional GET, NSPL loader, `_outward_lookup` index, `GB→UK` alias, `code_system` tagging, Tier 3.5, ITL names loader, isolation | 4–14 |
| `app/settings.json` | Add `nspl_url` only — **do NOT add UK to `countries`** (Codex review on PR #52: would trigger wasted GISCO URL guesses) | 6 |
| `app/config.py` | Surface NSPL config as `Settings` attributes | 6 |
| `app/main.py` | Forward `code_system` from lookup result; documentation cleanup | 12 |
| `tests/conftest.py` | Add UK mock data + outward-code fixture | 1, 9, 11 |
| `tests/test_postal_patterns.py` | UK regex tests + outward extraction | 2, 3 |
| `tests/test_data_loader.py` | NSPL parser, doterm filter, conditional GET, outward index, GB alias, code_system tagging, Tier 3.5, ITL names, isolation | 4–14 |
| `tests/test_api.py` | `code_system` in `/lookup` response, GB alias end-to-end | 1, 10, 12 |
| `README.md` | Coverage, six-tier waterfall, config table, attribution, divergence note | 15 |

---

## Task 1: Add `code_system` field to `NUTSResult`

**Goal:** Surface a discriminator so consumers know whether `nuts1/2/3` hold NUTS codes (GISCO) or ITL codes (NSPL). Backwards-compatible (additive only). Default `"NUTS"` everywhere; UK loader will override later.

**Files:**
- Modify: `app/models.py:1-20`
- Modify: `tests/test_api.py` (existing AT lookup test)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api.py` (extend an existing AT lookup assertion or add new):

```python
def test_lookup_response_includes_code_system_nuts(client, mock_data):
    resp = client.get("/lookup", params={"country": "AT", "postal_code": "1010"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code_system"] == "NUTS"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_api.py::test_lookup_response_includes_code_system_nuts -v
```

Expected: FAIL with `KeyError: 'code_system'` or `AssertionError`.

- [ ] **Step 3: Add the field to the model**

In `app/models.py`, modify `NUTSResult`:

```python
class NUTSResult(BaseModel):
    postal_code: str = Field(description="The queried postal code (normalized)")
    country_code: str = Field(description="ISO 3166-1 alpha-2 country code")
    code_system: Literal["NUTS", "ITL"] = Field(
        default="NUTS",
        description=(
            "Identifies the territorial coding scheme. 'NUTS' for GISCO-sourced "
            "EU/EFTA/candidate data; 'ITL' for UK data from the ONS NSPL."
        ),
    )
    match_type: Literal["exact", "estimated", "approximate"] = Field(
        description="How the result was determined"
    )
    # ... rest unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_api.py -v
```

Expected: PASS (the new test plus all existing tests).

- [ ] **Step 5: Commit**

```bash
git add app/models.py tests/test_api.py
git commit -m "feat: add code_system discriminator field to NUTSResult"
```

---

## Task 2: Add UK regex to `postal_patterns.json` and bump version

**Goal:** Validate UK input format. The regex matches all six valid outward-code shapes plus optional space.

**Files:**
- Modify: `app/postal_patterns.json` (add `UK` entry; update `_meta.version`)
- Modify: `tests/test_postal_patterns.py` (add UK regex tests)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_postal_patterns.py`:

```python
import pytest
from app.postal_patterns import extract_postal_code, PATTERNS_META


@pytest.mark.parametrize("raw, expected", [
    ("SW1A 2AA", "SW1A2AA"),
    ("sw1a 2aa", "SW1A2AA"),
    ("SW1A2AA",  "SW1A2AA"),
    ("M1 1AA",   "M11AA"),
    ("B33 8TH",  "B338TH"),
    ("W1A 1HQ",  "W1A1HQ"),
    ("CR2 6XH",  "CR26XH"),
    ("DN55 1PT", "DN551PT"),
    ("EC1A 1BB", "EC1A1BB"),
])
def test_uk_regex_extracts_normalized_full_postcode(raw, expected):
    assert extract_postal_code("UK", raw) == expected


def test_patterns_meta_version_bumped():
    # Adding UK is an additive coverage change; minor version bump.
    assert PATTERNS_META["version"] == "1.1"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_postal_patterns.py -v -k "uk_regex or version_bumped"
```

Expected: failures — UK pattern absent, version is `1.0`.

- [ ] **Step 3: Add the UK pattern entry**

In `app/postal_patterns.json`, change `_meta.version` from `"1.0"` to `"1.1"` and insert the `UK` entry (alphabetically after `TR`):

```json
"_meta": { "version": "1.1", "date": "2026-04-28" },
...
  "TR": { ... },
  "UK": {
    "regex": "^([A-Z]{1,2}[0-9][0-9A-Z]?\\s?[0-9][A-Z]{2})$",
    "example": "SW1A 2AA, EC1A 1BB, M1 1AA, B33 8TH",
    "tercet_map": "outward_only"
  }
```

(Note: `_meta.date` value is a hint; pick the date you commit on.)

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_postal_patterns.py -v -k "uk_regex or version_bumped"
```

Expected: regex tests PASS. The version test PASSES. The `tercet_map: outward_only` action will fail any tests in Task 3 — that is expected and Task 3 implements it.

- [ ] **Step 5: Commit**

```bash
git add app/postal_patterns.json tests/test_postal_patterns.py
git commit -m "feat: add UK postcode regex and bump patterns_version to 1.1"
```

---

## Task 3: Implement `outward_only` action and `extract_outward` helper

**Goal:** UK Tier 1 lookup uses the full postcode (`SW1A2AA`); Tier 3.5 lookup uses the outward portion (`SW1A`). The pattern entry's `tercet_map: outward_only` flags that the country supports outward-only fallback. `extract_postal_code` keeps returning the full normalised form (so Tier 1 still works); a new `extract_outward(country, raw_input)` returns the outward portion or `None`.

**Files:**
- Modify: `app/postal_patterns.py:70-108`
- Modify: `tests/test_postal_patterns.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_postal_patterns.py`:

```python
from app.postal_patterns import extract_outward


@pytest.mark.parametrize("raw, expected_outward", [
    ("SW1A 2AA", "SW1A"),
    ("sw1a2aa",  "SW1A"),
    ("M1 1AA",   "M1"),
    ("B33 8TH",  "B33"),
    ("EC1A 1BB", "EC1A"),
    ("DN55 1PT", "DN55"),
    ("SW1A",     "SW1A"),  # outward-only input
    ("M1",       "M1"),
])
def test_extract_outward_for_uk(raw, expected_outward):
    assert extract_outward("UK", raw) == expected_outward


def test_extract_outward_returns_none_for_country_without_flag():
    # AT does not declare outward_only; outward extraction is undefined.
    assert extract_outward("AT", "1010") is None


def test_extract_postal_code_unaffected_by_outward_only_flag():
    # Tier 1 lookup behaviour for UK must still be the full normalised postcode.
    assert extract_postal_code("UK", "SW1A 2AA") == "SW1A2AA"
```

- [ ] **Step 2: Run tests to verify failure**

```bash
pytest tests/test_postal_patterns.py -v -k "outward"
```

Expected: ImportError (no `extract_outward` symbol) plus assertion failures.

- [ ] **Step 3: Implement `outward_only` handling**

In `app/postal_patterns.py`, modify `_apply_tercet_map` to leave `outward_only` as a no-op (it's a flag, not a transform), and add `extract_outward`:

```python
def _apply_tercet_map(code: str, rule: str) -> str:
    """Apply a tercet_map transform rule to an extracted postal code."""
    action, _, arg = rule.partition(":")
    if action == "truncate":
        return code[: int(arg)]
    if action == "prepend":
        return arg + code
    if action == "keep_alpha":
        m = re.match(r"^([A-Z]+)", code)
        return m.group(1) if m else code
    if action == "outward_only":
        # Marker for countries that support outward-code-only fallback (Tier 3.5).
        # Has no effect on Tier 1 extraction; see extract_outward().
        return code
    return code


def extract_outward(country_code: str, raw_input: str) -> str | None:
    """Return the outward (district) portion for countries flagged outward_only.

    For UK postcodes, the outward portion is everything except the last 3 chars
    of the normalised form. If the input is shorter than 4 chars after
    normalisation, the input itself is treated as outward (handles cases like
    "SW1A" submitted alone).

    Returns None for countries that do not declare tercet_map=outward_only.
    """
    entry = POSTAL_PATTERNS.get(country_code)
    if not entry or entry.get("tercet_map") != "outward_only":
        return None
    normalised = normalize_postal_code(raw_input)
    if len(normalised) <= 4:
        return normalised
    return normalised[:-3]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_postal_patterns.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/postal_patterns.py tests/test_postal_patterns.py
git commit -m "feat: add outward_only action and extract_outward helper"
```

---

## Task 4: Extend `_parse_csv_content` column aliases for NSPL

**Goal:** Recognise NSPL's `pcds` column as a postal code source and `itl` / `itl3` / `itl3cd` as NUTS3-equivalent columns. No new parser path needed.

**Files:**
- Modify: `app/data_loader.py:215-260`
- Modify: `tests/test_data_loader.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_loader.py`:

```python
import io
from app import data_loader


def test_parse_csv_content_recognises_nspl_columns(monkeypatch):
    monkeypatch.setattr(data_loader, "_lookup", {})
    nspl_csv = (
        "pcds,itl,doterm\n"
        "SW1A 2AA,TLI32,\n"
        "EC1A 1BB,TLI32,\n"
    )
    rows = data_loader._parse_csv_content(nspl_csv, "UK")
    assert rows == 2
    assert data_loader._lookup[("UK", "SW1A2AA")] == "TLI32"
    assert data_loader._lookup[("UK", "EC1A1BB")] == "TLI32"
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_data_loader.py::test_parse_csv_content_recognises_nspl_columns -v
```

Expected: FAIL — parser logs "Could not identify columns" and returns 0.

- [ ] **Step 3: Extend the alias lists**

In `app/data_loader.py`, modify `_parse_csv_content` (around line 232 and line 239):

```python
    # Find the postal code column
    pc_col = None
    for candidate in ("CODE", "PC", "POSTAL_CODE", "POSTCODE", "PC_FMT", "PCDS"):
        if candidate in fieldnames:
            pc_col = candidate
            break

    # Find the NUTS3 column — prefer current version, never fall back to old versions
    nuts3_col = None
    for candidate in (
        f"NUTS3_{settings.nuts_version}",
        "NUTS3",
        "NUTS_ID",
        "NUTS",
        "ITL3CD",
        "ITL3",
        "ITL",
    ):
        if candidate in fieldnames:
            nuts3_col = candidate
            break
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_data_loader.py -v
```

Expected: PASS (new test plus all existing).

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/test_data_loader.py
git commit -m "feat: recognise NSPL pcds/itl columns in _parse_csv_content"
```

---

## Task 5: Add `skip_terminated` flag to `_parse_csv_content`

**Goal:** NSPL contains both live and terminated postcodes; rows with a non-blank `DOTERM` (date of termination) are skipped when the loader passes `skip_terminated=True`. Default behaviour is unchanged for TERCET.

**Files:**
- Modify: `app/data_loader.py:215`
- Modify: `tests/test_data_loader.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_loader.py`:

```python
def test_skip_terminated_filters_doterm_rows(monkeypatch):
    monkeypatch.setattr(data_loader, "_lookup", {})
    nspl_csv = (
        "pcds,itl,doterm\n"
        "SW1A 2AA,TLI32,\n"
        "M1 9NS,TLD46,202312\n"          # terminated, skip
        "EC1A 1BB,TLI32,\n"
    )
    rows = data_loader._parse_csv_content(nspl_csv, "UK", skip_terminated=True)
    assert rows == 2
    assert ("UK", "M19NS") not in data_loader._lookup


def test_skip_terminated_default_false_keeps_all_rows(monkeypatch):
    monkeypatch.setattr(data_loader, "_lookup", {})
    nspl_csv = (
        "pcds,itl,doterm\n"
        "SW1A 2AA,TLI32,\n"
        "M1 9NS,TLD46,202312\n"
    )
    rows = data_loader._parse_csv_content(nspl_csv, "UK")
    assert rows == 2
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_data_loader.py -v -k skip_terminated
```

Expected: TypeError (unknown kwarg) on first; second passes.

- [ ] **Step 3: Add the parameter**

Modify `_parse_csv_content` signature and body in `app/data_loader.py`:

```python
def _parse_csv_content(
    text: str,
    country_code: str,
    *,
    overwrite: bool = False,
    skip_terminated: bool = False,
) -> int:
    """Parse CSV/TSV content and populate the lookup table. Returns row count."""
    count = 0
    skipped = 0

    # ... (existing dialect detection, fieldnames, pc_col, nuts3_col, cc_col) ...

    # Detect optional DOTERM column for live-only filtering (NSPL)
    doterm_col = None
    if skip_terminated:
        for candidate in ("DOTERM", "DOT", "DATE_OF_TERMINATION"):
            if candidate in fieldnames:
                doterm_col = candidate
                break

    orig_fields = list(reader.fieldnames or [])
    pc_orig = orig_fields[fieldnames.index(pc_col)]
    nuts3_orig = orig_fields[fieldnames.index(nuts3_col)]
    cc_orig = orig_fields[fieldnames.index(cc_col)] if cc_col else None
    doterm_orig = orig_fields[fieldnames.index(doterm_col)] if doterm_col else None

    # ... (existing pre-loop checks) ...

    for row in reader:
        if doterm_orig and row.get(doterm_orig, "").strip():
            continue
        pc = row.get(pc_orig, "")
        nuts3 = row.get(nuts3_orig, "").strip()
        # ... (rest unchanged) ...
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_data_loader.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/test_data_loader.py
git commit -m "feat: add skip_terminated flag to filter NSPL doterm rows"
```

---

## Task 6: Add NSPL configuration

**Goal:** Operator-controlled NSPL ZIP URL and ITL Names-and-Codes URLs. Default empty (NSPL loader is a no-op when unset). Mirrors the existing `extra_sources` / `extra_source_urls` pattern in `Settings` — plain `str` field for the env var, separately-named `@property` to parse the comma-separated value. **No `Field(alias=...)` indirection** — that interacts unreliably with `env_prefix` in pydantic-settings.

> **Important:** Do NOT add `"UK"` to `settings.json` `countries`. That list is consumed by the GISCO loader's Strategy-2 URL guessing (`data_loader._guess_zip_urls_for_country`); adding UK there would attempt non-existent `pc{YYYY}_UK_NUTS-*.zip` URLs against GISCO on every cold load. UK loading is gated on `settings.nspl_url` being non-empty.

**Files:**
- Modify: `app/settings.json` (add `nspl_url` only; do NOT touch `countries`)
- Modify: `app/config.py`
- Modify: `tests/test_data_loader.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_loader.py`:

```python
def test_settings_expose_nspl_url_attribute():
    from app.config import settings
    # Default empty when env var unset
    assert settings.nspl_url == ""


def test_settings_expose_itl_names_urls_string():
    from app.config import settings
    # Raw env-backed string, default empty
    assert settings.itl_names_urls == ""


def test_settings_itl_names_url_list_parses_csv():
    from app.config import Settings
    s = Settings(itl_names_urls="https://a/x.csv, https://b/y.csv ,")
    assert s.itl_names_url_list == ["https://a/x.csv", "https://b/y.csv"]


def test_settings_itl_names_url_list_empty_when_unset():
    from app.config import settings
    assert settings.itl_names_url_list == []


def test_uk_NOT_in_settings_countries():
    """Regression guard: UK must not appear in the GISCO country list."""
    from app.config import settings
    assert "UK" not in settings.countries
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_data_loader.py -v -k "nspl_url or itl_names or uk_not"
```

Expected: AttributeError on `nspl_url` / `itl_names_urls` / `itl_names_url_list`. The `uk_NOT` test passes already (UK was never added).

- [ ] **Step 3: Update settings.json**

In `app/settings.json`, add `nspl_url` next to `tercet_base_url`. **Leave the `countries` list untouched.**

```json
{
  "tercet_base_url": "https://gisco-services.ec.europa.eu/tercet/NUTS-2024/",
  "nspl_url": "",
  "countries": [
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES",
    "FI", "FR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
    "NL", "PL", "PT", "RO", "SE", "SI", "SK",
    "CH", "IS", "LI", "NO",
    "MK", "RS", "TR"
  ],
  ...
}
```

(Operators set `PC2NUTS_NSPL_URL` to override the empty default; `nspl_url` in `settings.json` exists only so the field has a discoverable default location.)

- [ ] **Step 4: Update config.py**

In `app/config.py`, inside class `Settings`, add the two fields plus the parsing property — mirror exactly the existing `extra_sources` / `extra_source_urls` pattern at lines 19 / 36-40:

```python
    # NSPL (UK postcode → ITL3) — optional, no-op when both unset
    nspl_url: str = _defaults.get("nspl_url", "")
    itl_names_urls: str = ""

    @property
    def itl_names_url_list(self) -> list[str]:
        """Parse PC2NUTS_ITL_NAMES_URLS comma-separated string into URL list."""
        if not self.itl_names_urls.strip():
            return []
        return [u.strip() for u in self.itl_names_urls.split(",") if u.strip()]
```

`pydantic-settings` with the existing `env_prefix = "PC2NUTS_"` automatically reads:
- `PC2NUTS_NSPL_URL` → `Settings.nspl_url`
- `PC2NUTS_ITL_NAMES_URLS` → `Settings.itl_names_urls`

No `Field(alias=...)`, no extra imports. The property name `itl_names_url_list` (singular `url_list`) is intentionally distinct from the field name to avoid attribute shadowing.

- [ ] **Step 5: Run tests**

```bash
pytest tests/test_data_loader.py -v
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/settings.json app/config.py tests/test_data_loader.py
git commit -m "feat: add NSPL URL and ITL names URLs to settings"
```

---

## Task 7: Add conditional GET to the downloader

**Goal:** Skip re-downloading and re-parsing when the upstream ZIP hasn't changed (`If-Modified-Since` / `If-None-Match`). Benefits both TERCET and NSPL — implement once at the HTTP layer.

**Files:**
- Modify: `app/data_loader.py:299-340`
- Modify: `tests/test_data_loader.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_loader.py`:

```python
import httpx


def test_download_zip_sends_conditional_headers_when_etag_known(tmp_path, monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(304)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)

    cached_meta = {"etag": '"abc123"', "last_modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
    result = data_loader._download_zip_conditional(
        client, "https://example.com/foo.zip", cached_meta
    )
    assert result.status_code == 304
    assert captured["headers"]["if-none-match"] == '"abc123"'
    assert captured["headers"]["if-modified-since"] == "Wed, 01 Jan 2025 00:00:00 GMT"
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_data_loader.py::test_download_zip_sends_conditional_headers_when_etag_known -v
```

Expected: AttributeError (`_download_zip_conditional` undefined).

- [ ] **Step 3: Add the conditional download wrapper**

In `app/data_loader.py`, alongside `_download_zip` (around line 299), add:

```python
def _download_zip_conditional(
    client: httpx.Client, url: str, cached_meta: dict
) -> httpx.Response:
    """Download with conditional GET headers; returns the raw httpx.Response.

    cached_meta keys: 'etag' and 'last_modified' (either may be absent).
    Caller handles 200 (re-parse), 304 (use cache), and error statuses.
    """
    headers = {}
    if cached_meta.get("etag"):
        headers["If-None-Match"] = cached_meta["etag"]
    if cached_meta.get("last_modified"):
        headers["If-Modified-Since"] = cached_meta["last_modified"]
    return client.get(url, headers=headers, timeout=60, follow_redirects=True)
```

The integration into the existing TERCET cache flow (and later into NSPL) happens in subsequent tasks; this task only adds the primitive plus its unit test.

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_data_loader.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/test_data_loader.py
git commit -m "feat: add conditional GET wrapper for cached ZIP downloads"
```

---

## Task 8: Implement the NSPL loader

**Goal:** When `nspl_url` is set, download the NSPL ZIP, parse it via `_parse_csv_content` with `skip_terminated=True`, and populate `_lookup` with `("UK", normalised_postcode) → ITL3`. Failure must not raise — log and return.

**Files:**
- Modify: `app/data_loader.py` (new function `_load_nspl`, called from `load_data`)
- Modify: `tests/test_data_loader.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_loader.py`:

```python
def test_load_nspl_populates_lookup_from_zip(tmp_path, monkeypatch):
    import zipfile, io as _io

    monkeypatch.setattr(data_loader, "_lookup", {})

    csv_text = (
        "pcds,itl,doterm\n"
        "SW1A 2AA,TLI32,\n"
        "EC1A 1BB,TLI32,\n"
        "M1 9NS,TLD46,202312\n"  # terminated
    )
    buf = _io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("NSPL.csv", csv_text)

    def handler(request):
        return httpx.Response(200, content=buf.getvalue(), headers={"ETag": '"v1"'})

    client = httpx.Client(transport=httpx.MockTransport(handler))

    cache_dir = tmp_path
    count = data_loader._load_nspl(
        client,
        "https://example.com/NSPL.zip",
        cache_dir,
    )
    assert count == 2
    assert data_loader._lookup[("UK", "SW1A2AA")] == "TLI32"
    assert ("UK", "M19NS") not in data_loader._lookup


def test_load_nspl_returns_zero_when_url_unset(tmp_path):
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    count = data_loader._load_nspl(client, "", tmp_path)
    assert count == 0


def test_load_nspl_swallows_exceptions(tmp_path):
    def handler(request):
        raise httpx.ConnectError("boom")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    count = data_loader._load_nspl(client, "https://example.com/x.zip", tmp_path)
    assert count == 0
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_data_loader.py -v -k load_nspl
```

Expected: AttributeError on `_load_nspl`.

- [ ] **Step 3: Implement `_load_nspl`**

Add to `app/data_loader.py` (near other download helpers):

```python
def _load_nspl(client: httpx.Client, url: str, cache_dir: Path) -> int:
    """Fetch NSPL ZIP and load UK postcode → ITL3 entries into _lookup.

    Returns the number of rows added. Returns 0 when url is empty or any
    error occurs — failure must not block TERCET-only operation.
    """
    if not url:
        return 0
    cache_path = cache_dir / "nspl.zip"
    try:
        content = _download_zip(client, url)
        if content is None:
            logger.warning("NSPL download failed (404 or unreachable): %s", url)
            return 0
        if not zipfile.is_zipfile(io.BytesIO(content)):
            logger.warning("NSPL response is not a valid ZIP, skipping")
            return 0
        try:
            cache_path.write_bytes(content)
        except OSError as exc:
            logger.warning("Failed to cache NSPL ZIP: %s", exc)

        total = 0
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                # NSPL ships the postcode CSV under "Data/NSPL_*.csv"; accept any .csv
                if not name.lower().endswith(".csv"):
                    continue
                if "data/" not in name.lower() and "/data/" not in name.lower():
                    # Skip docs/userguide CSVs that aren't the main postcode file
                    if "nspl" not in name.lower():
                        continue
                raw = zf.read(name)
                for enc in ("utf-8-sig", "utf-8", "latin-1"):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                total += _parse_csv_content(
                    text, "UK", overwrite=False, skip_terminated=True
                )
        logger.info("NSPL loaded: %d UK postcodes", total)
        return total
    except (httpx.HTTPError, zipfile.BadZipFile) as exc:
        logger.warning("NSPL load failed: %s", exc)
        return 0
```

Hook it into `load_data` (around `app/data_loader.py:912-914`, just before `_download_nuts_names`):

```python
            # NSPL (UK postcodes via ITL) — optional, no-op when unset
            if not timed_out and settings.nspl_url:
                nspl_count = _load_nspl(client, settings.nspl_url, cache_dir)
                if nspl_count > 0:
                    logger.info("Loaded %d entries for UK from NSPL", nspl_count)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_data_loader.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/test_data_loader.py
git commit -m "feat: implement NSPL loader with isolated failure handling"
```

---

## Task 9: Build the outward-code index

**Goal:** After NSPL is loaded, build `_outward_lookup[("UK", outward)] = (majority_itl3, agreement_ratio)` for use by Tier 3.5.

**Files:**
- Modify: `app/data_loader.py` (new `_outward_lookup` dict + `_build_outward_index` function)
- Modify: `tests/conftest.py` (extend `mock_data` fixture with UK rows + outward index build)
- Modify: `tests/test_data_loader.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_loader.py`:

```python
def test_build_outward_index_majority_vote(monkeypatch):
    monkeypatch.setattr(data_loader, "_lookup", {
        ("UK", "SW1A2AA"): "TLI32",
        ("UK", "SW1A1AA"): "TLI32",
        ("UK", "SW1A0AA"): "TLI31",  # minority
        ("UK", "M11AA"):   "TLD45",
        ("UK", "M11AB"):   "TLD45",
    })
    monkeypatch.setattr(data_loader, "_outward_lookup", {})

    data_loader._build_outward_index("UK")

    assert data_loader._outward_lookup[("UK", "SW1A")] == ("TLI32", pytest.approx(2/3))
    assert data_loader._outward_lookup[("UK", "M1")] == ("TLD45", pytest.approx(1.0))


def test_build_outward_index_skips_short_codes(monkeypatch):
    monkeypatch.setattr(data_loader, "_lookup", {("UK", "AB1"): "TLC11"})
    monkeypatch.setattr(data_loader, "_outward_lookup", {})
    data_loader._build_outward_index("UK")
    # Codes shorter than 4 chars after normalisation cannot be split; skip.
    assert data_loader._outward_lookup == {}
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_data_loader.py -v -k build_outward
```

Expected: AttributeError on `_outward_lookup` / `_build_outward_index`.

- [ ] **Step 3: Implement the index**

In `app/data_loader.py`, add near the other module-level state (around line 41):

```python
# Outward-code index for Tier 3.5: (country_code, outward) -> (nuts3, agreement_ratio)
_outward_lookup: dict[tuple[str, str], tuple[str, float]] = {}
```

Add the build function (near `_build_prefix_index`):

```python
def _build_outward_index(country_code: str) -> None:
    """Populate _outward_lookup for one country using majority vote per outward code.

    Outward = full normalised postcode minus the last 3 chars (UK convention).
    Codes shorter than 4 chars are skipped (no meaningful split possible).
    """
    groups: dict[str, list[str]] = {}
    for (cc, code), nuts3 in _lookup.items():
        if cc != country_code:
            continue
        if len(code) < 4:
            continue
        outward = code[:-3]
        groups.setdefault(outward, []).append(nuts3)

    for outward, nuts3_list in groups.items():
        counts = Counter(nuts3_list)
        winner, count = counts.most_common(1)[0]
        agreement = count / len(nuts3_list)
        _outward_lookup[(country_code, outward)] = (winner, agreement)
```

Call it from `load_data` after the NSPL loader hook from Task 8:

```python
            if not timed_out and settings.nspl_url:
                nspl_count = _load_nspl(client, settings.nspl_url, cache_dir)
                if nspl_count > 0:
                    logger.info("Loaded %d entries for UK from NSPL", nspl_count)
                    _build_outward_index("UK")
```

Update `tests/conftest.py` to (a) include UK mock rows and (b) call `_build_outward_index` in the `mock_data` fixture:

```python
MOCK_LOOKUP = {
    # ... existing entries ...
    ("UK", "SW1A2AA"): "TLI32",
    ("UK", "SW1A1AA"): "TLI32",
    ("UK", "EC1A1BB"): "TLI32",
    ("UK", "M11AA"):   "TLD45",
}

# Inside mock_data fixture, after _build_prefix_index():
data_loader._outward_lookup.clear()
data_loader._build_outward_index("UK")

# In teardown, restore _outward_lookup similarly to other globals.
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/conftest.py tests/test_data_loader.py
git commit -m "feat: build outward-code majority-vote index for Tier 3.5"
```

---

## Task 10: Add `GB → UK` country alias

**Goal:** Accept ISO 3166-1 `GB` as input and normalise it to the canonical `UK` used internally and by NSPL.

**Files:**
- Modify: `app/data_loader.py:66-69`
- Modify: `tests/test_data_loader.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_loader.py`:

```python
def test_normalize_country_maps_gb_to_uk():
    assert data_loader.normalize_country("GB") == "UK"
    assert data_loader.normalize_country("gb") == "UK"


def test_normalize_country_preserves_existing_aliases():
    assert data_loader.normalize_country("GR") == "EL"
    assert data_loader.normalize_country("UK") == "UK"
    assert data_loader.normalize_country("AT") == "AT"
```

Add to `tests/test_api.py`:

```python
def test_lookup_accepts_gb_alias(client, mock_data):
    resp_uk = client.get("/lookup", params={"country": "UK", "postal_code": "SW1A 2AA"})
    resp_gb = client.get("/lookup", params={"country": "GB", "postal_code": "SW1A 2AA"})
    assert resp_uk.status_code == 200
    assert resp_gb.status_code == 200
    assert resp_uk.json() == resp_gb.json()
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -k "gb_alias or maps_gb"
```

Expected: failures (`UK` returned as `GB`, lookups not equivalent).

- [ ] **Step 3: Update `normalize_country`**

In `app/data_loader.py`:

```python
def normalize_country(country_code: str) -> str:
    """Normalize a country code: uppercase + map ISO/non-canonical aliases.

    GR → EL  (ISO vs GISCO convention)
    GB → UK  (ISO vs NSPL/internal convention)
    """
    cc = country_code.strip().upper()
    if cc == "GR":
        return "EL"
    if cc == "GB":
        return "UK"
    return cc
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/test_data_loader.py tests/test_api.py
git commit -m "feat: alias GB to UK for ISO 3166-1 input compatibility"
```

---

## Task 11: Add Tier 3.5 outward-code lookup to `lookup()`

**Goal:** When Tier 1 (exact) misses for UK, try the outward-code index before falling through to Tier 3 (prefix). Confidence uses the medium-tier numbers from `settings.json` (NUTS1=0.90, NUTS2=0.80, NUTS3=0.70).

**Files:**
- Modify: `app/data_loader.py:968-1028`
- Modify: `tests/test_data_loader.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_loader.py`:

```python
def test_tier_3_5_outward_lookup_for_uk(mock_data):
    # mock_data populates UK postcodes; SW1A is in TLI32 majority.
    result = data_loader.lookup("UK", "SW1A")
    assert result is not None
    assert result["nuts3"] == "TLI32"
    assert result["match_type"] == "estimated"
    assert result["nuts1_confidence"] == pytest.approx(0.90)
    assert result["nuts2_confidence"] == pytest.approx(0.80)
    assert result["nuts3_confidence"] == pytest.approx(0.70)


def test_tier_3_5_falls_through_for_unknown_outward(mock_data):
    result = data_loader.lookup("UK", "ZZ99")
    # No outward match; downstream tiers will also miss → None
    assert result is None
```

Add to `tests/test_api.py`:

```python
def test_uk_outward_only_input_returns_estimated(client, mock_data):
    resp = client.get("/lookup", params={"country": "UK", "postal_code": "SW1A"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["match_type"] == "estimated"
    assert body["nuts3"] == "TLI32"
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -k "tier_3_5 or outward_only_input"
```

Expected: failures — lookup returns None or 404 for outward-only input.

- [ ] **Step 3: Insert Tier 3.5 in `lookup()`**

In `app/data_loader.py`, modify `lookup` (the function around line 968). Insert between Tier 3 and Tier 4:

```python
def lookup(country_code: str, postal_code: str) -> dict | None:
    """... (existing docstring + add: 3.5. Outward-code lookup (UK) ...)"""
    from app.postal_patterns import extract_outward, extract_postal_code

    cc = normalize_country(country_code)
    extracted = extract_postal_code(cc, postal_code)
    key = (cc, extracted)

    # Tier 1: Exact TERCET match
    nuts3 = _lookup.get(key)
    if nuts3 is not None:
        return _build_result("exact", nuts3)

    # Tier 2: Pre-computed estimate
    est = _estimates.get(key)
    if est is not None:
        return _build_result(
            "estimated", est["nuts3"],
            nuts1=est["nuts1"], nuts2=est["nuts2"],
            nuts1_confidence=est["nuts1_confidence"],
            nuts2_confidence=est["nuts2_confidence"],
            nuts3_confidence=est["nuts3_confidence"],
        )

    # Tier 3: Runtime prefix-based estimation
    approx = _estimate_by_prefix(cc, extracted)
    if approx is not None:
        return approx

    # Tier 3.5: Outward-code lookup (UK and any other country with outward_only)
    outward = extract_outward(cc, postal_code)
    if outward is not None:
        outward_hit = _outward_lookup.get((cc, outward))
        if outward_hit is not None:
            nuts3, _agreement = outward_hit
            conf = settings.confidence_map["medium"]
            return _build_result(
                "estimated",
                nuts3,
                nuts1_confidence=conf["nuts1"],
                nuts2_confidence=conf["nuts2"],
                nuts3_confidence=conf["nuts3"],
            )

    # Tier 4: Country-level majority vote (existing)
    fallback = _country_fallback.get(cc)
    if fallback is not None:
        return _build_result(
            "approximate", fallback["nuts3"],
            nuts1=fallback["nuts1"], nuts2=fallback["nuts2"],
            nuts1_confidence=fallback["nuts1_confidence"],
            nuts2_confidence=fallback["nuts2_confidence"],
            nuts3_confidence=fallback["nuts3_confidence"],
        )

    # Tier 5: Single-NUTS3 country fallback (existing)
    nuts3 = _single_nuts3.get(cc)
    if nuts3 is not None:
        return _build_result("estimated", nuts3)

    return None
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/test_data_loader.py tests/test_api.py
git commit -m "feat: add Tier 3.5 outward-code lookup to lookup waterfall"
```

---

## Task 12: Tag UK results with `code_system="ITL"`

**Goal:** `_build_result` and `lookup` know which scheme each entry belongs to. Simplest implementation: derive at result-construction time from country code (UK → ITL, everything else → NUTS), since UK is the only ITL source today. Forwarded to the `NUTSResult` model in `app/main.py`.

**Files:**
- Modify: `app/data_loader.py` (extend `_build_result`)
- Modify: `app/main.py:194-207` (forward `code_system`)
- Modify: `tests/test_data_loader.py`
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_data_loader.py`:

```python
def test_lookup_result_includes_code_system_itl_for_uk(mock_data):
    result = data_loader.lookup("UK", "SW1A 2AA")
    assert result["code_system"] == "ITL"


def test_lookup_result_includes_code_system_nuts_for_at(mock_data):
    result = data_loader.lookup("AT", "1010")
    assert result["code_system"] == "NUTS"
```

Add to `tests/test_api.py`:

```python
def test_uk_lookup_response_has_code_system_itl(client, mock_data):
    resp = client.get("/lookup", params={"country": "UK", "postal_code": "SW1A 2AA"})
    assert resp.status_code == 200
    assert resp.json()["code_system"] == "ITL"
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/ -v -k "code_system"
```

Expected: failures (`code_system` absent from result dict; default "NUTS" returned for UK).

- [ ] **Step 3: Plumb `code_system` through**

In `app/data_loader.py`, change `_build_result` to accept a `code_system` (default `"NUTS"`):

```python
def _build_result(
    match_type: str,
    nuts3: str,
    nuts1: str = "",
    nuts2: str = "",
    code_system: str = "NUTS",
    **confidence,
) -> dict:
    n1 = nuts1 or nuts3[:3]
    n2 = nuts2 or nuts3[:4]
    return {
        "code_system": code_system,
        "match_type": match_type,
        "nuts1": n1,
        "nuts1_confidence": confidence.get("nuts1_confidence", 1.0),
        "nuts2": n2,
        "nuts2_confidence": confidence.get("nuts2_confidence", 1.0),
        "nuts3": nuts3,
        "nuts3_confidence": confidence.get("nuts3_confidence", 1.0),
        **_resolve_names(n1, n2, nuts3),
    }
```

In `lookup()`, derive `code_system` once and pass it to every `_build_result` call:

```python
def lookup(country_code: str, postal_code: str) -> dict | None:
    from app.postal_patterns import extract_outward, extract_postal_code

    cc = normalize_country(country_code)
    code_system = "ITL" if cc == "UK" else "NUTS"
    extracted = extract_postal_code(cc, postal_code)
    key = (cc, extracted)

    # ... at every `return _build_result(...)`, add `code_system=code_system,` to the kwargs ...
```

(Touch every tier's return statement: Tier 1, 2, 3 via `_estimate_by_prefix`, 3.5, 4, 5. For Tier 3, pass `code_system` into `_estimate_by_prefix` too — see snippet below.)

```python
def _estimate_by_prefix(country_code: str, postal_code: str, code_system: str = "NUTS") -> dict | None:
    # ... existing body ...
    return _build_result("approximate", nuts3, nuts1=n1, nuts2=n2, code_system=code_system, ...)
```

In `app/main.py`, modify `lookup_postal_code` to forward `code_system` from the result dict:

```python
    return NUTSResult(
        postal_code=postal_code,
        country_code=cc,
        code_system=result.get("code_system", "NUTS"),
        **{k: v for k, v in result.items() if k != "code_system"},
    )
```

(Or simpler: rely on `**result` plus pydantic ignoring extras — but pydantic v2 raises on extras unless `model_config = {"extra": "ignore"}`. Either set that on `NUTSResult` or do the explicit forward as above.)

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py app/main.py tests/test_data_loader.py tests/test_api.py
git commit -m "feat: tag UK lookups with code_system=ITL through the waterfall"
```

---

## Task 13: Implement ITL names CSV loader

**Goal:** Download three ONS Names-and-Codes CSVs (one per ITL level), parse `(code, name)` pairs, insert into `_nuts_names`. The current `_NUTS3_RE` already accepts TL-prefixed codes so name resolution in `_resolve_names` will work without changes.

**Files:**
- Modify: `app/data_loader.py` (new `_load_itl_names`)
- Modify: `tests/test_data_loader.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_data_loader.py`:

```python
def test_load_itl_names_populates_nuts_names(monkeypatch):
    monkeypatch.setattr(data_loader, "_nuts_names", {})

    def handler(request):
        body = (
            "ITL321CD,ITL321NM\n"
            "TLI32,Tower Hamlets\n"
            "TLI31,Hackney and Newham\n"
        )
        return httpx.Response(200, content=body.encode())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    count = data_loader._load_itl_names(client, ["https://example.com/itl3.csv"])
    assert count == 2
    assert data_loader._nuts_names["TLI32"] == "Tower Hamlets"


def test_load_itl_names_empty_url_list_no_op():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert data_loader._load_itl_names(client, []) == 0
```

- [ ] **Step 2: Run tests**

```bash
pytest tests/test_data_loader.py -v -k load_itl_names
```

Expected: AttributeError on `_load_itl_names`.

- [ ] **Step 3: Implement**

In `app/data_loader.py`, near `_download_nuts_names`:

```python
_ITL_CODE_RE = re.compile(r"ITL\d?(?:CD)?$", re.IGNORECASE)
_ITL_NAME_RE = re.compile(r"ITL\d?(?:NM)?$", re.IGNORECASE)


def _load_itl_names(client: httpx.Client, urls: list[str]) -> int:
    """Fetch ONS ITL Names-and-Codes CSVs and merge into _nuts_names.

    Each CSV has paired columns like ITL321CD/ITL321NM (level 3),
    ITL221CD/ITL221NM (level 2), ITL121CD/ITL121NM (level 1). Column
    names vary by release year — match by suffix (CD / NM).
    """
    if not urls:
        return 0
    total = 0
    for url in urls:
        try:
            resp = client.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
            text = resp.text
        except httpx.HTTPError as exc:
            logger.warning("ITL names fetch failed for %s: %s", url, exc)
            continue
        try:
            reader = csv.DictReader(io.StringIO(text))
            fieldnames = [f.strip() for f in (reader.fieldnames or [])]
            code_col = next(
                (f for f in fieldnames if f.upper().endswith("CD") and "ITL" in f.upper()),
                None,
            )
            name_col = next(
                (f for f in fieldnames if f.upper().endswith("NM") and "ITL" in f.upper()),
                None,
            )
            if not code_col or not name_col:
                logger.warning("Could not find ITL CD/NM columns in %s; headers=%s", url, fieldnames)
                continue
            for row in reader:
                code = (row.get(code_col) or "").strip().upper()
                name = (row.get(name_col) or "").strip()
                if code and name:
                    _nuts_names[code] = name
                    total += 1
        except (csv.Error, KeyError) as exc:
            logger.warning("ITL names parse failed for %s: %s", url, exc)
    logger.info("ITL names loaded: %d entries from %d URLs", total, len(urls))
    return total
```

Hook it into `load_data` after `_load_nspl`:

```python
            if not timed_out and settings.itl_names_url_list:
                _load_itl_names(client, settings.itl_names_url_list)
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/ -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/data_loader.py tests/test_data_loader.py
git commit -m "feat: load ITL region names from ONS Names-and-Codes CSVs"
```

---

## Task 14: Verify NSPL failure isolation

**Goal:** End-to-end test: when NSPL is unreachable, TERCET data still loads and the service continues to serve. (Most of this isolation is already implicit in Task 8's design — `_load_nspl` swallows exceptions. This task adds an explicit regression test.)

**Files:**
- Modify: `tests/test_data_loader.py`

- [ ] **Step 1: Write the test**

Add to `tests/test_data_loader.py`:

```python
def test_nspl_failure_does_not_block_tercet(tmp_path, monkeypatch):
    """If NSPL fails, TERCET data must still be served."""
    monkeypatch.setattr(data_loader, "_lookup", {("AT", "1010"): "AT130"})

    # Simulate NSPL endpoint that always errors
    def handler(request):
        raise httpx.ConnectError("ons unavailable")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    nspl_count = data_loader._load_nspl(client, "https://ons.invalid/nspl.zip", tmp_path)
    assert nspl_count == 0

    # AT lookup must still work
    result = data_loader.lookup("AT", "1010")
    assert result is not None
    assert result["nuts3"] == "AT130"
```

- [ ] **Step 2: Run test**

```bash
pytest tests/test_data_loader.py::test_nspl_failure_does_not_block_tercet -v
```

Expected: PASS already (Task 8's implementation swallows the error). If it fails, the implementation needs the broader exception clause shown in Task 8.

- [ ] **Step 3: Commit (test-only)**

```bash
git add tests/test_data_loader.py
git commit -m "test: confirm NSPL failure does not block TERCET serving"
```

---

## Task 15: README and documentation updates

**Goal:** Surface the new feature, the divergence note, the new tier, the new config, and the OGL v3.0 attribution.

**Files:**
- Modify: `README.md` (multiple sections)

- [ ] **Step 1: Update the Coverage section**

Insert after the "EU candidate countries" subsection (around `README.md:18`):

```markdown
**United Kingdom** (via the ONS NSPL dataset, mapped to ITL — International Territorial Level):
United Kingdom (UK; ISO `GB` accepted as alias).

ITL is the UK's territorial classification published by ONS, succeeding NUTS for UK statistical geography after Brexit. ITL diverges from NUTS 2016 UK at L2 and L3 (41 vs 40 ITL2 regions; 179 vs 174 ITL3 regions). The bidirectional NUTS↔ITL lookups previously published by ONS were discontinued in 2023. Responses for UK lookups carry `code_system: "ITL"` so consumers can branch correctly when comparing against historical NUTS-UK data.
```

- [ ] **Step 2: Update the response example**

Modify the existing AT example response (around `README.md:69-84`) to include the new field:

```json
{
  "postal_code": "A-1010",
  "country_code": "AT",
  "code_system": "NUTS",
  "match_type": "exact",
  ...
}
```

Add a UK example below the existing examples:

```markdown
**Example — UK postcode (ITL):**

`GET /lookup?country=UK&postal_code=SW1A%202AA`

```json
{
  "postal_code": "SW1A 2AA",
  "country_code": "UK",
  "code_system": "ITL",
  "match_type": "exact",
  "nuts1": "TLI",
  "nuts1_name": "London",
  "nuts1_confidence": 1.0,
  "nuts2": "TLI3",
  "nuts2_name": "Inner London - East",
  "nuts2_confidence": 1.0,
  "nuts3": "TLI32",
  "nuts3_name": "Tower Hamlets",
  "nuts3_confidence": 1.0
}
```
```

- [ ] **Step 3: Rename "Five-tier lookup" → "Six-tier lookup" and add Tier 3.5**

Insert between Tier 3 and Tier 4 sections:

```markdown
### Tier 3.5: Outward-code lookup (`match_type: "estimated"`) — UK only

Triggered when:
- The input postcode is shorter than a full UK postcode (no inward portion), OR
- Tier 3 prefix approximation finds no match.

Looks up `(country, outward_code)` in a precomputed majority-vote index built at NSPL load time. The outward code for UK is everything before the last 3 characters of the normalised postcode (e.g. `SW1A` for `SW1A 2AA`). Confidence uses the medium tier (NUTS1 0.90 / NUTS2 0.80 / NUTS3 0.70) because one outward code can span two adjacent ITL3 regions in dense urban areas.
```

- [ ] **Step 4: Add the supported-patterns row for UK**

Add to the "Supported patterns" table (around `README.md:266-301`), in alphabetical position after `TR`:

```markdown
| UK | 1-2 letters + digit + optional letter/digit + optional space + digit + 2 letters | — | `SW1A 2AA`, `EC1A 1BB`, `M1 1AA`, `B33 8TH`, `SW1A` (outward only) |
```

- [ ] **Step 5: Add new env vars to the Configuration table**

Add to the configuration table:

```markdown
| `PC2NUTS_NSPL_URL` | *(empty)* | URL to the latest NSPL ZIP from the ONS Open Geography Portal. When unset, UK is unsupported. |
| `PC2NUTS_ITL_NAMES_URLS` | *(empty)* | Comma-separated list of ONS "Names and Codes" CSV URLs (one per ITL level). Loaded after NSPL. |
```

- [ ] **Step 6: Add OGL v3.0 attribution to Data source**

Replace the existing single-source line at the end of the Data source section with:

```markdown
[GISCO TERCET flat files](https://ec.europa.eu/eurostat/web/gisco/geodata/administrative-units/postal-codes) ([download](https://gisco-services.ec.europa.eu/tercet/flat-files)), © European Union – GISCO, licensed [CC-BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/).

UK postcode data: [ONS National Statistics Postcode Lookup (NSPL)](https://geoportal.statistics.gov.uk/), © Crown copyright, licensed [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). Contains public sector information licensed under the OGL v3.0.
```

- [ ] **Step 7: Document Crown Dependencies / Gibraltar exclusion**

Add a one-paragraph note near the UK coverage section:

```markdown
**Out of scope:** Crown Dependencies (Jersey JE, Guernsey GG, Isle of Man IM) and Gibraltar (GI) use UK-style postcode formats but are not in ITL geography or NSPL, and are not currently supported. Lookups for these country codes return a 400 (unsupported country).
```

- [ ] **Step 8: Update "Adding a new country"**

Add a note at the end of that section:

```markdown
> Countries served via a non-GISCO source (currently only UK via NSPL) require a separate loader path and additional configuration (URLs for the source ZIP and any names files). See `_load_nspl` and `_load_itl_names` in `app/data_loader.py` for the NSPL precedent.
```

- [ ] **Step 9: Commit**

```bash
git add README.md
git commit -m "docs: document UK/ITL support, six-tier waterfall, OGL attribution"
```

---

## Self-review checklist (post-plan)

| Spec section | Implemented by | Notes |
|--------------|----------------|-------|
| §3 Architecture (parallel data channel) | Task 8, Task 14 | NSPL loader called from `load_data`; failure isolated. |
| §4 NSPL URL / config | Task 6 | `nspl_url`, `itl_names_urls` (string field) + `itl_names_url_list` (parsing property). Mirrors `extra_sources` precedent — no `Field(alias=...)`. |
| §4 Conditional GET | Task 7 | Wrapper added; integration into TERCET cache flow can be a follow-up if needed (the wrapper is in place). |
| §4 doterm filter | Task 5 | Flag in `_parse_csv_content`. |
| §4 NSPL column aliases | Task 4 | `PCDS`, `ITL`, `ITL3`, `ITL3CD`. |
| §4 ITL names | Task 13 | `_load_itl_names` with paired CD/NM column matching. |
| §4 Shared TTL | (no code change) | Inherits existing `db_cache_ttl_days`. |
| §5 Tier 3.5 | Task 3, Task 9, Task 11 | `extract_outward`, `_outward_lookup`, lookup waterfall insert. |
| §6 `code_system` field | Task 1, Task 12 | Model + plumbing. |
| §7 Configuration changes | Tasks 1, 2, 6, 12 | All listed files touched. |
| §7 `patterns_version` bump | Task 2 | `1.0 → 1.1`. |
| §7 GB→UK alias | Task 10 | Implemented in `normalize_country`. |
| §8 Documentation | Task 15 | README sections updated. |
| §9 Operational impact | (verify in deployment) | Plan does not include an explicit benchmark task; if needed, add measurement to Task 8/9 PR description. |
| §10 Out of scope | Task 15 (documented) | Code-level rejection for JE/GG/IM/GI is the existing default behaviour. |

**No placeholders detected.** All steps include concrete code, exact commands, and explicit expected output.

**Type/name consistency:**
- `extract_outward` (not `extract_outward_code`) used consistently in Tasks 3, 11.
- `_outward_lookup` (not `_outward_index`) used consistently in Tasks 9, 11.
- `_load_nspl` and `_load_itl_names` consistent in Tasks 8, 13.
- `code_system` literal values exactly `"NUTS"` and `"ITL"` everywhere.

**Scope:** focused single feature; ~15 tasks; bounded by the spec. No decomposition needed.
