"""Download and parse TERCET flat files into an in-memory lookup table."""

import csv
import hashlib
import io
import logging
import re
import sqlite3
import threading
import time
import zipfile
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import httpx2 as httpx

from app.albania_blocks import SUPPORTED as AL_SUPPORTED
from app.albania_blocks import resolve_al_block
from app.config import settings
from app.territories import classify as classify_territory
from app.territories import load_territories, territory_iso_codes

_NUTS3_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{1,3}$")

_MAX_UNCOMPRESSED_SIZE = 100 * 1024 * 1024  # 100 MB

# The NSPL postcode CSV (~1.79M live rows) is far larger than a TERCET file; it
# needs its own, higher extraction cap. Source is operator-configured (trusted).
_MAX_NSPL_UNCOMPRESSED_SIZE = 1024 * 1024 * 1024  # 1 GB

logger = logging.getLogger(__name__)

# postal_code -> NUTS3 code, keyed by (country_code, normalized_postal_code)
_lookup: dict[tuple[str, str], str] = {}

# Pre-computed estimates keyed by (country_code, postal_code)
_estimates: dict[tuple[str, str], dict] = {}

# Prefix index: country_code -> prefix -> list of nuts3 codes
_prefix_index: dict[str, dict[str, list[str]]] = {}

# Countries with a single NUTS3 region: country_code -> nuts3 code
_single_nuts3: dict[str, str] = {}

# Country-level majority-vote fallback for countries where NUTS1/NUTS2
# are unanimous but NUTS3 has a dominant winner (e.g. MT → MT0/MT00/MT001)
_country_fallback: dict[str, dict] = {}

# NUTS region names: nuts_id -> name_latn
_nuts_names: dict[str, str] = {}

# Outward-code index for lookup Tier 3.5 (UK): (country_code, outward) ->
# (nuts3, agreement_ratio). Built from _lookup by majority vote at load time.
_outward_lookup: dict[tuple[str, str], tuple[str, float]] = {}

# Staleness tracking
_data_stale: bool = False
_data_loaded_at: str = ""

# Extra source tracking
_extra_source_count: int = 0

# Protects against concurrent reload
_data_lock = threading.Lock()

load_territories()


def normalize_postal_code(code: str) -> str:
    """Normalize a postal code by removing spaces, dashes, and uppercasing.

    European postal codes use varied formats (PL: 00-950, SE: 111 22, UK: SW1A 1AA).
    Stripping all non-alphanumeric characters ensures consistent matching.
    """
    return re.sub(r"[^A-Za-z0-9]", "", code.strip()).upper()


def normalize_country(country_code: str) -> str:
    """Normalize a country code: uppercase + map non-canonical aliases.

    GR → EL  (ISO vs GISCO convention)
    GB → UK  (ISO vs NSPL/internal convention)
    """
    cc = country_code.strip().upper()
    if cc == "GR":
        return "EL"
    if cc == "GB":
        return "UK"
    return cc


def get_lookup_table() -> dict[tuple[str, str], str]:
    return _lookup


def get_estimates_table() -> dict[tuple[str, str], dict]:
    return _estimates


def get_loaded_countries() -> set[str]:
    """Return the set of country codes that have data loaded.

    Includes estimate-only countries (e.g. AL): countries present solely in
    the estimates table with no TERCET file and no fallback entry.
    """
    return (
        {cc for cc, _ in _lookup}
        | {cc for cc, _ in _estimates}
        | set(_single_nuts3.keys())
        | territory_iso_codes()
        | set(AL_SUPPORTED)
    )


def get_data_stale() -> bool:
    return _data_stale


def get_data_loaded_at() -> str:
    return _data_loaded_at


def get_extra_source_count() -> int:
    return _extra_source_count


def get_nuts_names() -> dict[str, str]:
    return _nuts_names


def _infer_country_from_url(url: str) -> str:
    """Extract country code from a TERCET-style filename (e.g. pc2025_AT_...).

    Returns uppercase 2-letter code, or empty string if not found.
    """
    m = re.search(r"pc\d{4}_([A-Z]{2})_", url)
    return m.group(1) if m else ""


def _extra_sources_hash() -> str:
    """SHA-256 hash of joined extra source URLs (truncated to 16 hex chars).

    Returns empty string when no extra sources are configured.
    """
    urls = settings.extra_source_urls
    if not urls:
        return ""
    joined = ",".join(urls)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _nspl_config_hash() -> str:
    """SHA-256 hash (16 hex chars) of the UK/NSPL configuration.

    Returns empty string when NSPL is unconfigured, so a TERCET-only deployment's
    cache stays valid. Enabling, disabling, or changing PC2NUTS_NSPL_URL /
    PC2NUTS_UK_ITL_LOOKUP_URL changes the hash, busting the fast-path cache so UK
    rows are added (or dropped) on the next load instead of after TTL expiry.
    """
    if not settings.nspl_url and not settings.uk_itl_lookup_url:
        return ""
    joined = settings.nspl_url + "|" + settings.uk_itl_lookup_url
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


def _load_extra_sources(client: httpx.Client, cache_dir: Path, *, deadline: float = 0) -> int:
    """Download and parse extra data source ZIPs. Returns total entries written."""
    global _extra_source_count

    urls = settings.extra_source_urls
    if not urls:
        _extra_source_count = 0
        return 0

    _extra_source_count = len(urls)
    total = 0

    for url in urls:
        # Validate URL scheme and extension
        if not url.lower().startswith(("http://", "https://")):
            logger.warning("Skipping extra source with invalid scheme: %s", url)
            continue
        if not url.lower().endswith(".zip"):
            logger.warning("Skipping extra source (not a .zip URL): %s", url)
            continue

        cc = _infer_country_from_url(url)
        if not cc:
            logger.info("No country code in URL filename %s, will rely on CSV COUNTRY_CODE column", url)

        count = _download_and_parse_zip(client, url, cc, cache_dir, overwrite=True, deadline=deadline)
        if count > 0:
            logger.info("Extra source %s: loaded %d entries (overwrite mode)", url, count)
        else:
            logger.warning("Extra source %s: no entries loaded", url)
        total += count

    return total


@contextmanager
def _db_connection(path: Path, *, readonly: bool = True):
    """Open a SQLite connection and ensure it is closed on exit."""
    if readonly:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    else:
        con = sqlite3.connect(str(path))
    try:
        yield con
    finally:
        con.close()


def _read_db_created_at(db: Path) -> str:
    """Read the created_at timestamp from the DB metadata table."""
    try:
        with _db_connection(db) as con:
            row = con.execute("SELECT value FROM metadata WHERE key = 'created_at'").fetchone()
        return row[0] if row else ""
    except sqlite3.Error:
        return ""


def _discover_zip_urls(client: httpx.Client, base_url: str) -> list[str]:
    """Try to discover ZIP file URLs from the TERCET directory listing."""
    urls: list[str] = []
    try:
        # Capped like every other fetch: this one runs first on a cold cache, so
        # an unbounded listing here would exhaust the worker before any of the
        # capped ZIP downloads got a chance to.
        resp = _get_capped(client, base_url, limit_bytes=_max_download_bytes(), timeout=30)
        resp.raise_for_status()
        # Parse href attributes pointing to .zip files
        for match in re.finditer(r'href="([^"]*\.zip)"', resp.text):
            href = match.group(1)
            if href.startswith("http"):
                urls.append(href)
            else:
                urls.append(base_url.rstrip("/") + "/" + href.lstrip("/"))
    except (httpx.RequestError, httpx.HTTPStatusError, DownloadTooLarge) as exc:
        logger.debug("Could not fetch directory listing from %s: %s", base_url, exc)
    return urls


def _guess_zip_urls_for_country(base_url: str, country_code: str):
    """Yield candidate ZIP URLs for a single country, most likely first."""
    base = base_url.rstrip("/")
    for pc_year in ("2025", "2024", "2023", "2020"):
        for version in ("v1.0", "v2.0", "v3.0", "v4.0"):
            yield f"{base}/pc{pc_year}_{country_code}_NUTS-{settings.nuts_version}_{version}.zip"


def _sniff_dialect(text: str) -> csv.Dialect | None:
    """Detect CSV dialect (delimiter + quotechar) using csv.Sniffer."""
    sample = "\n".join(text.split("\n", 10)[:10])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return None


def _parse_csv_content(text: str, country_code: str, *, overwrite: bool = False) -> int:
    """Parse CSV/TSV content and populate the lookup table. Returns row count."""
    count = 0
    skipped = 0

    dialect = _sniff_dialect(text)
    if dialect is not None:
        reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    else:
        # Fallback heuristic for delimiter only
        first_line = text.split("\n", 1)[0]
        delimiter = "\t" if "\t" in first_line else ";" if ";" in first_line else ","
        reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    fieldnames = [f.strip().upper() for f in (reader.fieldnames or [])]

    # Find the postal code column
    pc_col = None
    for candidate in ("CODE", "PC", "POSTAL_CODE", "POSTCODE", "PC_FMT"):
        if candidate in fieldnames:
            pc_col = candidate
            break

    # Find the NUTS3 column — prefer current version, never fall back to old versions
    nuts3_col = None
    for candidate in (f"NUTS3_{settings.nuts_version}", "NUTS3", "NUTS_ID", "NUTS"):
        if candidate in fieldnames:
            nuts3_col = candidate
            break

    if pc_col is None or nuts3_col is None:
        logger.warning(
            "Could not identify columns in file for %s. Headers found: %s (need postal code + NUTS3 column)",
            country_code,
            fieldnames,
        )
        return 0

    # Detect optional COUNTRY_CODE column
    cc_col = None
    for candidate in ("COUNTRY_CODE", "CC", "CNTR_CODE"):
        if candidate in fieldnames:
            cc_col = candidate
            break

    # Map back to original-case field names from DictReader
    orig_fields = list(reader.fieldnames or [])
    pc_orig = orig_fields[fieldnames.index(pc_col)]
    nuts3_orig = orig_fields[fieldnames.index(nuts3_col)]
    cc_orig = orig_fields[fieldnames.index(cc_col)] if cc_col else None

    if not country_code and cc_col is None:
        logger.warning("No country code available (not in URL or CSV columns), skipping file")
        return 0

    for row in reader:
        pc = row.get(pc_orig, "")
        nuts3 = row.get(nuts3_orig, "").strip()
        if not pc or not nuts3:
            continue
        # Validate NUTS3 code format
        if not _NUTS3_RE.match(nuts3):
            skipped += 1
            continue
        # Resolve country code: per-row CSV column takes priority, then function param
        row_cc = row.get(cc_orig, "").strip().upper() if cc_orig else ""
        cc = row_cc if row_cc else country_code.upper()
        key = (cc, normalize_postal_code(pc))
        if overwrite:
            # Last-write-wins: extra sources overwrite TERCET data
            is_new = key not in _lookup
            _lookup[key] = nuts3
            if is_new:
                count += 1
        else:
            # First-write-wins: discovery-phase data takes priority
            if key not in _lookup:
                _lookup[key] = nuts3
                count += 1

    if skipped:
        logger.warning("Skipped %d rows with invalid NUTS3 codes for %s", skipped, country_code)
    return count


# Describe the wire body, not the buffered copy we hand back.
_WIRE_HEADERS = frozenset({"content-length", "content-encoding", "transfer-encoding"})


class DownloadTooLarge(Exception):
    """A remote body exceeded the configured size ceiling and was abandoned."""


def _max_download_bytes() -> int:
    return settings.max_download_mb * 1024 * 1024


def _get_capped(
    client: httpx.Client,
    url: str,
    *,
    limit_bytes: int,
    headers: dict[str, str] | None = None,
    timeout: float = 60,
) -> httpx.Response:
    """GET `url`, buffering at most `limit_bytes` of body.

    Every remote body we fetch (TERCET zips, NSPL, the names CSV, the ITL
    override) is read into memory whole, so an upstream that turns hostile or
    simply misbehaves could otherwise exhaust the worker. Streaming lets us stop
    at the ceiling instead of after the damage.

    Returns a Response carrying the buffered bytes, so callers keep reading
    .status_code / .headers / .content unchanged. Raises DownloadTooLarge as
    soon as the declared or actual length passes the limit.
    """
    with client.stream("GET", url, headers=headers or {}, timeout=timeout, follow_redirects=True) as resp:
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > limit_bytes:
            raise DownloadTooLarge(f"{url}: Content-Length {declared} exceeds {limit_bytes} bytes")
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > limit_bytes:
                raise DownloadTooLarge(f"{url}: body exceeds {limit_bytes} bytes")
            chunks.append(chunk)
        # content-length/-encoding describe the wire body, which iter_bytes has
        # already decoded; carrying them onto the buffered copy would misdescribe it.
        kept = [(k, v) for k, v in resp.headers.multi_items() if k.lower() not in _WIRE_HEADERS]
        status = resp.status_code
        # Carry the request across so callers can still use raise_for_status().
        request = resp.request
    return httpx.Response(status, headers=kept, content=b"".join(chunks), request=request)


def _download_zip_conditional(client: httpx.Client, url: str, cached_meta: dict) -> httpx.Response:
    """Download with conditional-GET headers; returns a buffered httpx.Response.

    cached_meta keys: 'etag' and 'last_modified' (either may be absent). The
    caller handles 200 (re-parse), 304 (keep cache), and error statuses. Applies
    to both TERCET and NSPL so an unchanged upstream ZIP is not re-fetched.

    Raises DownloadTooLarge when the body passes PC2NUTS_MAX_DOWNLOAD_MB.
    """
    headers = {}
    if cached_meta.get("etag"):
        headers["If-None-Match"] = cached_meta["etag"]
    if cached_meta.get("last_modified"):
        headers["If-Modified-Since"] = cached_meta["last_modified"]
    return _get_capped(client, url, limit_bytes=_max_download_bytes(), headers=headers, timeout=60)


def _download_zip(client: httpx.Client, url: str) -> bytes | None:
    """Download a ZIP with one retry on transient network errors.

    Returns raw bytes on success, None on failure or 404.
    """
    for attempt in range(2):
        try:
            resp = _get_capped(client, url, limit_bytes=_max_download_bytes(), timeout=60)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content
        except DownloadTooLarge as exc:
            logger.warning("Refusing oversized download: %s", exc)
            return None
        except httpx.HTTPStatusError:
            return None
        except httpx.RequestError as exc:
            if attempt == 0:
                logger.debug("Transient error downloading %s, retrying: %s", url, exc)
                time.sleep(2)
            else:
                logger.warning("Failed to download %s after 2 attempts: %s", url, exc)
    return None


def _download_and_parse_zip(
    client: httpx.Client,
    url: str,
    country_code: str,
    cache_dir: Path,
    *,
    overwrite: bool = False,
    deadline: float = 0,
) -> int:
    """Download a single ZIP, extract CSVs, parse them. Returns row count."""
    if deadline and time.monotonic() > deadline:
        logger.warning("Startup timeout reached, skipping download of %s", url)
        return 0
    filename = url.rsplit("/", 1)[-1]
    cached = cache_dir / filename

    content: bytes | None = None

    if cached.exists():
        # Check cache TTL — re-download if older than 30 days
        age = time.time() - cached.stat().st_mtime
        if age > settings.db_cache_ttl_days * 86400:
            logger.info("Cache expired for %s (%.0f days old), re-downloading", cached.name, age / 86400)
            cached.unlink()
        else:
            content = cached.read_bytes()
            # Validate cached file is a real ZIP
            if not zipfile.is_zipfile(io.BytesIO(content)):
                logger.warning("Corrupt cached file %s, deleting and re-downloading", cached.name)
                cached.unlink()
                content = None
            else:
                logger.info("Using cached file %s", cached)

    if content is None:
        logger.info("Downloading %s", url)
        content = _download_zip(client, url)
        if content is None:
            return 0
        # Validate before caching
        if not zipfile.is_zipfile(io.BytesIO(content)):
            logger.warning("Downloaded file from %s is not a valid ZIP, skipping", url)
            return 0
        try:
            cached.write_bytes(content)
        except OSError as exc:
            logger.error("Failed to cache %s: %s", cached, exc)

    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".csv", ".tsv", ".txt")):
                    file_size = zf.getinfo(name).file_size
                    if file_size > _MAX_UNCOMPRESSED_SIZE:
                        logger.warning(
                            "Skipping %s in %s: uncompressed size %d bytes exceeds limit",
                            name,
                            url,
                            file_size,
                        )
                        continue
                    raw = zf.read(name)
                    # Try common encodings; latin-1 always succeeds so no else needed
                    for enc in ("utf-8-sig", "utf-8", "latin-1"):
                        try:
                            text = raw.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    total += _parse_csv_content(text, country_code, overwrite=overwrite)
    except zipfile.BadZipFile:
        logger.warning("Bad ZIP file from %s", url)
    return total


def _parse_nspl_csv(text: str, lad_to_itl3: dict[str, str]) -> int:
    """Parse one NSPL CSV: postcode (pcds) + LAD (lad25cd) → ITL3 TL code.

    NSPL's own ``itl`` column holds ONS GSS entity codes, not the Eurostat ``TL``
    codes we emit — so we resolve via the LAD instead: each live postcode's LAD is
    translated to its ITL3 code through ``lad_to_itl3`` (see app.uk_itl). Only live
    postcodes (blank DOTERM) are kept; rows whose LAD is unmapped are skipped.
    Returns the number of rows added to _lookup.
    """
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [f.strip().upper() for f in (reader.fieldnames or [])]
    orig = list(reader.fieldnames or [])

    def find(candidates: tuple[str, ...]) -> str | None:
        for c in candidates:
            if c in fieldnames:
                return orig[fieldnames.index(c)]
        return None

    pc_col = find(("PCDS", "PCD8", "PCD7"))
    lad_col = find(("LAD25CD", "LAD24CD", "LAD23CD", "LAD21CD", "OSLAUA"))
    doterm_col = find(("DOTERM",))
    if not pc_col or not lad_col:
        return 0

    count = 0
    for row in reader:
        if doterm_col and (row.get(doterm_col) or "").strip():
            continue
        pc = row.get(pc_col, "")
        lad = (row.get(lad_col) or "").strip().upper()
        itl3 = lad_to_itl3.get(lad)
        if not pc or not itl3:
            continue
        key = ("UK", normalize_postal_code(pc))
        if key not in _lookup:
            _lookup[key] = itl3
            count += 1
    return count


def _parse_nspl_zip(content: bytes, lad_to_itl3: dict[str, str]) -> int:
    """Parse NSPL ZIP bytes and load live UK postcode → ITL3 rows into _lookup.

    Returns the number of rows added. Raises zipfile.BadZipFile for invalid input.
    """
    total = 0
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        for name in zf.namelist():
            # NSPL ships the postcode data as per-area "NSPL*.csv" files under
            # Data/multi_csv/; other bundled CSVs (user guide, code lookups) lack
            # the pcds/lad columns and _parse_nspl_csv returns 0 for them.
            if not name.lower().endswith(".csv") or "nspl" not in name.lower():
                continue
            file_size = zf.getinfo(name).file_size
            if file_size > _MAX_NSPL_UNCOMPRESSED_SIZE:
                logger.warning(
                    "Skipping %s: uncompressed size %d exceeds NSPL limit %d",
                    name,
                    file_size,
                    _MAX_NSPL_UNCOMPRESSED_SIZE,
                )
                continue
            raw = zf.read(name)
            for enc in ("utf-8-sig", "utf-8", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            total += _parse_nspl_csv(text, lad_to_itl3)
    return total


def _load_nspl(client: httpx.Client, url: str, cache_dir: Path, lad_to_itl3: dict[str, str]) -> int:
    """Fetch the NSPL ZIP and load UK postcode → ITL3 entries into _lookup.

    Returns the number of rows added. Returns 0 when url is empty. An NSPL
    failure must never block TERCET-only operation, so on a fetch/parse failure
    the previously-cached nspl.zip is reused when present (a transient ONS outage
    must not silently drop UK support for a configured deployment). Terminated
    postcodes (non-blank DOTERM) are filtered out. UK registers in the loaded
    country set automatically because its rows land in _lookup.
    """
    if not url:
        return 0
    cache_path = cache_dir / "nspl.zip"
    try:
        resp = _download_zip_conditional(client, url, {})
        if resp.status_code == 304:
            # Unchanged upstream — reload from the cached copy if we have one.
            return _load_nspl_from_cache(cache_path, lad_to_itl3)
        resp.raise_for_status()
        content = resp.content
        if not zipfile.is_zipfile(io.BytesIO(content)):
            logger.warning("NSPL response from %s is not a valid ZIP", url)
            return _load_nspl_from_cache(cache_path, lad_to_itl3)
        try:
            cache_path.write_bytes(content)
        except OSError as exc:
            logger.warning("Failed to cache NSPL ZIP: %s", exc)
        total = _parse_nspl_zip(content, lad_to_itl3)
        logger.info("NSPL loaded: %d live UK postcodes", total)
        return total
    except (httpx.HTTPError, DownloadTooLarge, zipfile.BadZipFile, OSError) as exc:
        logger.warning("NSPL fetch failed (%s); trying cached copy", exc)
        return _load_nspl_from_cache(cache_path, lad_to_itl3)


def _load_nspl_from_cache(cache_path: Path, lad_to_itl3: dict[str, str]) -> int:
    """Load UK rows from a previously-cached nspl.zip. Returns 0 if unavailable."""
    if not cache_path.is_file():
        return 0
    try:
        content = cache_path.read_bytes()
        if not zipfile.is_zipfile(io.BytesIO(content)):
            return 0
        total = _parse_nspl_zip(content, lad_to_itl3)
        logger.info("NSPL loaded from cache: %d live UK postcodes", total)
        return total
    except (zipfile.BadZipFile, OSError) as exc:
        logger.warning("Cached NSPL ZIP unusable: %s", exc)
        return 0


def _db_path() -> Path:
    """Return the path for the SQLite cache DB, scoped by NUTS version."""
    return Path(settings.data_dir) / f"postalcode2nuts_NUTS-{settings.nuts_version}.db"


def _db_is_valid(db: Path) -> bool:
    """Check if the SQLite cache DB exists, matches current version, and is fresh."""
    if not db.is_file():
        return False
    try:
        with _db_connection(db) as con:
            cur = con.execute("SELECT key, value FROM metadata")
            meta = dict(cur.fetchall())
        if meta.get("nuts_version") != settings.nuts_version:
            logger.info("DB cache version mismatch, will rebuild")
            return False
        if int(meta.get("entry_count", "0")) == 0:
            logger.info("DB cache is empty, will rebuild")
            return False
        created = datetime.fromisoformat(meta["created_at"])
        age_days = (datetime.now(timezone.utc) - created).total_seconds() / 86400
        if age_days > settings.db_cache_ttl_days:
            logger.info("DB cache expired (%.0f days old), will rebuild", age_days)
            return False
        # Check if extra sources configuration changed
        stored_hash = meta.get("extra_sources_hash", "")
        if stored_hash != _extra_sources_hash():
            logger.info("Extra sources configuration changed, will rebuild")
            return False
        # Check if NSPL / ITL-names configuration changed (enable/disable/URL swap)
        if meta.get("nspl_config_hash", "") != _nspl_config_hash():
            logger.info("NSPL configuration changed, will rebuild")
            return False
        return True
    except (sqlite3.Error, KeyError, ValueError) as exc:
        logger.info("DB cache unusable (%s), will rebuild", exc)
        return False


def _load_estimates_from_db(db: Path) -> bool:
    """Load pre-computed estimates from the DB. Graceful if table is missing."""
    try:
        with _db_connection(db) as con:
            # Check if estimates table exists
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='estimates'")
            if cur.fetchone() is None:
                return False
            rows = con.execute(
                "SELECT country_code, postal_code, nuts3, nuts2, nuts1, "
                "nuts3_confidence, nuts2_confidence, nuts1_confidence FROM estimates"
            ).fetchall()
        if not rows:
            return False
        for cc, pc, n3, n2, n1, c3, c2, c1 in rows:
            _estimates[(cc, pc)] = {
                "nuts3": n3,
                "nuts2": n2,
                "nuts1": n1,
                "nuts3_confidence": c3,
                "nuts2_confidence": c2,
                "nuts1_confidence": c1,
            }
        logger.info("Loaded %d estimates from SQLite cache %s", len(rows), db.name)
        return True
    except sqlite3.Error as exc:
        logger.warning("Failed to load estimates from DB: %s", exc)
        return False


def parse_estimates_from_text(text: str) -> tuple[dict[tuple[str, str], dict], int]:
    """Parse an estimates CSV from a string into a fresh dict.

    Returns (parsed_dict, skipped_count). Rows with unknown confidence labels
    are counted in skipped_count and not included in the dict. Used both by
    _load_estimates_from_csv (file path) and app.estimates_refresh (HTTP body).
    """
    out: dict[tuple[str, str], dict] = {}
    skipped = 0
    reader = csv.DictReader(io.StringIO(text.removeprefix("﻿")))
    for row in reader:
        cc = row["COUNTRY_CODE"].strip().upper()
        pc = normalize_postal_code(row["POSTAL_CODE"])
        n3 = row["ESTIMATED_NUTS3"].strip()
        n2 = row["ESTIMATED_NUTS2"].strip()
        n1 = row["ESTIMATED_NUTS1"].strip()
        label = row["CONFIDENCE"].strip().lower()
        conf = settings.confidence_map.get(label)
        if conf is None:
            skipped += 1
            continue
        out[(cc, pc)] = {
            "nuts3": n3,
            "nuts2": n2,
            "nuts1": n1,
            "nuts3_confidence": conf["nuts3"],
            "nuts2_confidence": conf["nuts2"],
            "nuts1_confidence": conf["nuts1"],
        }
    return out, skipped


def _load_estimates_from_csv(csv_path: Path) -> bool:
    """Load pre-computed estimates from a file into the live in-memory dict."""
    if not csv_path.is_file():
        return False
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
        parsed, skipped = parse_estimates_from_text(text)
    except (OSError, KeyError, ValueError, csv.Error) as exc:
        logger.warning("Failed to load estimates from CSV: %s", exc)
        return False
    _estimates.update(parsed)
    if skipped:
        logger.warning("Skipped %d estimate rows with unknown confidence labels", skipped)
    if parsed:
        logger.info("Loaded %d estimates from %s", len(parsed), csv_path)
    return len(parsed) > 0


def _revalidate_estimates() -> int:
    """Remove estimates that now have exact matches and warn about inconsistencies.

    Returns count removed.
    """
    to_remove = []
    inconsistent = 0
    for key, est in _estimates.items():
        exact = _lookup.get(key)
        if exact is not None:
            to_remove.append(key)
            # Warn if the estimate pointed to a different NUTS3 than the exact match
            if est["nuts3"] != exact:
                inconsistent += 1
    for key in to_remove:
        del _estimates[key]
    if to_remove:
        logger.info("Removed %d estimates that now have exact TERCET matches", len(to_remove))
    if inconsistent:
        logger.warning(
            "%d removed estimates had NUTS3 codes inconsistent with current exact data "
            "(estimates CSV may need updating)",
            inconsistent,
        )
    return len(to_remove)


def _download_nuts_names(client: httpx.Client) -> int:
    """Download NUTS region names CSV from GISCO and populate _nuts_names.

    Returns the number of names loaded, or 0 on failure.
    """
    url = f"https://gisco-services.ec.europa.eu/distribution/v2/nuts/csv/NUTS_AT_{settings.nuts_version}.csv"
    try:
        resp = _get_capped(client, url, limit_bytes=_max_download_bytes(), timeout=30)
        resp.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError, DownloadTooLarge) as exc:
        logger.warning("Failed to download NUTS names from %s: %s", url, exc)
        return 0

    text = resp.text
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = [f.strip().upper() for f in (reader.fieldnames or [])]
    orig_fields = list(reader.fieldnames or [])

    # Find NUTS_ID and NAME_LATN columns
    nuts_id_col = None
    name_col = None
    for candidate in ("NUTS_ID", "NUTS_CODE", "CODE"):
        if candidate in fieldnames:
            nuts_id_col = orig_fields[fieldnames.index(candidate)]
            break
    for candidate in ("NAME_LATN", "NUTS_NAME", "NAME"):
        if candidate in fieldnames:
            name_col = orig_fields[fieldnames.index(candidate)]
            break

    if nuts_id_col is None or name_col is None:
        logger.warning("NUTS names CSV missing expected columns. Headers: %s", fieldnames)
        return 0

    count = 0
    for row in reader:
        nuts_id = row.get(nuts_id_col, "").strip()
        name = row.get(name_col, "").strip()
        if nuts_id and name:
            _nuts_names[nuts_id] = name
            count += 1

    logger.info("Loaded %d NUTS region names from %s", count, url)
    return count


def _load_uk_itl_bridge(client: httpx.Client) -> dict[str, str]:
    """Load the LAD→ITL3 bridge and merge ITL region names into _nuts_names.

    Uses the bundled ONS map (app/uk_lad_itl.csv) by default; if
    PC2NUTS_UK_ITL_LOOKUP_URL is set, fetches a refreshed export in the same
    shape and falls back to the bundle on any error. Returns the LAD→ITL3 map
    (empty only if both bundle and override are unusable).
    """
    from app import uk_itl

    lad_to_itl3: dict[str, str] = {}
    itl_names: dict[str, str] = {}
    url = settings.uk_itl_lookup_url
    if url:
        try:
            resp = _get_capped(client, url, limit_bytes=_max_download_bytes(), timeout=30)
            resp.raise_for_status()
            lad_to_itl3, itl_names = uk_itl.parse_lad_itl(resp.text)
        except (httpx.HTTPError, DownloadTooLarge, csv.Error) as exc:
            logger.warning("UK ITL lookup override failed (%s); using bundled map", exc)
    if not lad_to_itl3:
        lad_to_itl3, itl_names = uk_itl.load_bundled()
    # ITL region names join into the shared NUTS names table (TL codes resolve
    # via the same _resolve_names path; truncation gives ITL2/ITL1).
    _nuts_names.update(itl_names)
    logger.info("UK ITL bridge: %d LADs, %d ITL names", len(lad_to_itl3), len(itl_names))
    return lad_to_itl3


def _load_nuts_names_from_db(db: Path) -> bool:
    """Load NUTS region names from SQLite cache. Graceful if table is missing."""
    try:
        with _db_connection(db) as con:
            cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='nuts_names'")
            if cur.fetchone() is None:
                return False
            rows = con.execute("SELECT nuts_id, name_latn FROM nuts_names").fetchall()
        if not rows:
            return False
        for nuts_id, name in rows:
            _nuts_names[nuts_id] = name
        logger.info("Loaded %d NUTS region names from SQLite cache %s", len(rows), db.name)
        return True
    except sqlite3.Error as exc:
        logger.warning("Failed to load NUTS names from DB: %s", exc)
        return False


def _resolve_names(nuts1: str, nuts2: str, nuts3: str) -> dict:
    """Return a dict with nuts1_name, nuts2_name, nuts3_name from _nuts_names."""
    return {
        "nuts1_name": _nuts_names.get(nuts1),
        "nuts2_name": _nuts_names.get(nuts2),
        "nuts3_name": _nuts_names.get(nuts3),
    }


def _build_prefix_index() -> None:
    """Build a prefix index over all TERCET codes for runtime estimation."""
    _prefix_index.clear()
    for (cc, pc), nuts3 in _lookup.items():
        if cc not in _prefix_index:
            _prefix_index[cc] = {}
        idx = _prefix_index[cc]
        # Index all prefixes from length 1 to len(pc)-1
        for length in range(1, len(pc)):
            prefix = pc[:length]
            if prefix not in idx:
                idx[prefix] = []
            idx[prefix].append(nuts3)
    total_prefixes = sum(len(v) for v in _prefix_index.values())
    logger.info("Built prefix index: %d prefixes across %d countries", total_prefixes, len(_prefix_index))

    # Detect countries with a single NUTS3 region (e.g. LI → LI000)
    _single_nuts3.clear()
    country_nuts3: dict[str, set[str]] = {}
    for (cc, _pc), nuts3 in _lookup.items():
        country_nuts3.setdefault(cc, set()).add(nuts3)
    for cc, nuts3_set in country_nuts3.items():
        if len(nuts3_set) == 1:
            _single_nuts3[cc] = next(iter(nuts3_set))
    # Merge in countries Eurostat treats as a single nationwide unit but for which
    # no TERCET file is published (e.g. ME → ME000).
    for cc, nuts3 in settings.single_nuts3_fallback.items():
        _single_nuts3.setdefault(cc, nuts3)
    if _single_nuts3:
        logger.info("Single-NUTS3 countries: %s", ", ".join(sorted(_single_nuts3)))

    # Country-level majority-vote fallback for countries NOT in _single_nuts3
    # where NUTS1 and NUTS2 are unanimous but NUTS3 has a dominant winner
    _country_fallback.clear()
    caps = settings.approximate_confidence_caps
    for cc, nuts3_set in country_nuts3.items():
        if cc in _single_nuts3:
            continue
        nuts1_set = {n[:3] for n in nuts3_set}
        nuts2_set = {n[:4] for n in nuts3_set}
        if len(nuts1_set) != 1 or len(nuts2_set) != 1:
            continue
        # Count postal codes per NUTS3 to find dominant region
        nuts3_counts: Counter[str] = Counter()
        for (c, _), n3 in _lookup.items():
            if c == cc:
                nuts3_counts[n3] += 1
        total = sum(nuts3_counts.values())
        if total == 0:
            continue
        winner, winner_count = nuts3_counts.most_common(1)[0]
        ratio = winner_count / total
        _country_fallback[cc] = {
            "nuts1": next(iter(nuts1_set)),
            "nuts1_confidence": 1.0,
            "nuts2": next(iter(nuts2_set)),
            "nuts2_confidence": 1.0,
            "nuts3": winner,
            "nuts3_confidence": round(min(ratio, caps["nuts3"]), 2),
        }
    if _country_fallback:
        logger.info(
            "Country-level fallback: %s",
            ", ".join(f"{cc}→{v['nuts3']}" for cc, v in sorted(_country_fallback.items())),
        )


def _build_outward_index(country_code: str) -> None:
    """Populate _outward_lookup for one country by majority vote per outward code.

    Outward = the full normalised postcode minus its last three characters (UK
    convention). Codes shorter than four characters are skipped (no meaningful
    split). Used by lookup Tier 3.5 for outward-only or otherwise-unmatched input.
    """
    groups: dict[str, list[str]] = {}
    for (cc, code), nuts3 in _lookup.items():
        if cc != country_code or len(code) < 4:
            continue
        outward = code[:-3]
        groups.setdefault(outward, []).append(nuts3)

    for outward, nuts3_list in groups.items():
        counts = Counter(nuts3_list)
        winner, count = counts.most_common(1)[0]
        agreement = count / len(nuts3_list)
        _outward_lookup[(country_code, outward)] = (winner, agreement)
    if groups:
        logger.info("Built outward index for %s: %d outward codes", country_code, len(groups))


def _estimate_by_prefix(cc: str, postal_code: str) -> dict | None:
    """Runtime estimation via longest prefix match + majority vote.

    Returns a result dict with match_type='approximate' or None.
    """
    idx = _prefix_index.get(cc)
    if not idx:
        return None

    # Find the longest matching prefix
    best_prefix = None
    for length in range(len(postal_code), 0, -1):
        prefix = postal_code[:length]
        if prefix in idx:
            best_prefix = prefix
            break

    if best_prefix is None:
        return None

    neighbors = idx[best_prefix]
    prefix_ratio = len(best_prefix) / len(postal_code)

    # Majority vote at each NUTS level
    nuts3_counts = Counter(neighbors)
    nuts2_counts = Counter(n[:4] for n in neighbors)
    nuts1_counts = Counter(n[:3] for n in neighbors)

    total = len(neighbors)

    nuts3_winner, nuts3_count = nuts3_counts.most_common(1)[0]
    nuts2_winner, nuts2_count = nuts2_counts.most_common(1)[0]
    nuts1_winner, nuts1_count = nuts1_counts.most_common(1)[0]

    # Confidence = agreement_ratio * prefix_ratio, capped per level
    caps = settings.approximate_confidence_caps
    c3 = round(min((nuts3_count / total) * prefix_ratio, caps["nuts3"]), 2)
    c2 = round(min((nuts2_count / total) * prefix_ratio, caps["nuts2"]), 2)
    c1 = round(min((nuts1_count / total) * prefix_ratio, caps["nuts1"]), 2)

    # Skip if NUTS1 confidence is too low to be useful
    if c1 < settings.approximate_min_confidence:
        return None

    return _build_result(
        "approximate",
        nuts3_winner,
        nuts1=nuts1_winner,
        nuts2=nuts2_winner,
        nuts1_confidence=c1,
        nuts2_confidence=c2,
        nuts3_confidence=c3,
    )


def _load_from_db(db: Path) -> bool:
    """Load the lookup table from SQLite cache. Returns True on success."""
    try:
        with _db_connection(db) as con:
            rows = con.execute("SELECT country_code, postal_code, nuts3 FROM lookup").fetchall()
        if not rows:
            return False
        for cc, pc, nuts3 in rows:
            _lookup[(cc, pc)] = nuts3
        logger.info("Loaded %d entries from SQLite cache %s", len(rows), db.name)
        return True
    except sqlite3.Error as exc:
        logger.warning("Failed to load from DB cache: %s", exc)
        _lookup.clear()
        return False


def _save_to_db(db: Path) -> None:
    """Persist the lookup table and estimates to SQLite cache with atomic rename."""
    tmp = db.with_suffix(".db.tmp")
    try:
        tmp.unlink(missing_ok=True)
        with _db_connection(tmp, readonly=False) as con:
            con.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            con.execute(
                "CREATE TABLE lookup ("
                "country_code TEXT NOT NULL, "
                "postal_code TEXT NOT NULL, "
                "nuts3 TEXT NOT NULL, "
                "PRIMARY KEY (country_code, postal_code))"
            )
            con.execute(
                "CREATE TABLE estimates ("
                "country_code TEXT NOT NULL, "
                "postal_code TEXT NOT NULL, "
                "nuts3 TEXT NOT NULL, "
                "nuts2 TEXT NOT NULL, "
                "nuts1 TEXT NOT NULL, "
                "nuts3_confidence REAL NOT NULL, "
                "nuts2_confidence REAL NOT NULL, "
                "nuts1_confidence REAL NOT NULL, "
                "PRIMARY KEY (country_code, postal_code))"
            )
            con.executemany(
                "INSERT INTO lookup (country_code, postal_code, nuts3) VALUES (?, ?, ?)",
                [(cc, pc, nuts3) for (cc, pc), nuts3 in _lookup.items()],
            )
            con.executemany(
                "INSERT INTO estimates "
                "(country_code, postal_code, nuts3, nuts2, nuts1, "
                "nuts3_confidence, nuts2_confidence, nuts1_confidence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        cc,
                        pc,
                        est["nuts3"],
                        est["nuts2"],
                        est["nuts1"],
                        est["nuts3_confidence"],
                        est["nuts2_confidence"],
                        est["nuts1_confidence"],
                    )
                    for (cc, pc), est in _estimates.items()
                ],
            )
            con.execute("CREATE TABLE nuts_names (nuts_id TEXT PRIMARY KEY, name_latn TEXT NOT NULL)")
            con.executemany(
                "INSERT INTO nuts_names (nuts_id, name_latn) VALUES (?, ?)",
                list(_nuts_names.items()),
            )
            con.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                [
                    ("nuts_version", settings.nuts_version),
                    ("created_at", datetime.now(timezone.utc).isoformat()),
                    ("entry_count", str(len(_lookup))),
                    ("estimate_count", str(len(_estimates))),
                    ("nuts_names_count", str(len(_nuts_names))),
                    ("extra_sources_hash", _extra_sources_hash()),
                    ("nspl_config_hash", _nspl_config_hash()),
                ],
            )
            con.commit()
        tmp.replace(db)
        logger.info(
            "Saved %d entries + %d estimates + %d names to SQLite cache %s",
            len(_lookup),
            len(_estimates),
            len(_nuts_names),
            db.name,
        )
    except (sqlite3.Error, OSError) as exc:
        logger.error("Failed to save DB cache: %s", exc)
        tmp.unlink(missing_ok=True)


def load_data() -> None:
    """Download all TERCET flat files and build the in-memory lookup table."""
    global _data_stale, _data_loaded_at, _extra_source_count

    with _data_lock:
        if settings.nuts_version == "unknown":
            logger.warning(
                "Could not derive NUTS version from base URL '%s'. "
                "URL guessing and DB caching may not work correctly.",
                settings.tercet_base_url,
            )
        if settings.db_cache_ttl_days < 1:
            logger.warning(
                "PC2NUTS_DB_CACHE_TTL_DAYS=%d is less than 1, cache will always be considered expired.",
                settings.db_cache_ttl_days,
            )

        _lookup.clear()
        _estimates.clear()
        _nuts_names.clear()
        _outward_lookup.clear()
        _data_stale = False
        _extra_source_count = len(settings.extra_source_urls)

        start_time = time.monotonic()
        deadline = start_time + settings.startup_timeout

        # Ensure data directory exists
        data_dir = Path(settings.data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)

        estimates_csv = Path(settings.estimates_csv)

        # Fast path: load from SQLite cache if valid
        db = _db_path()
        if _db_is_valid(db) and _load_from_db(db):
            _data_loaded_at = _read_db_created_at(db)
            if not _load_estimates_from_csv(estimates_csv):
                _load_estimates_from_db(db)
            _revalidate_estimates()
            _load_nuts_names_from_db(db)
            _build_prefix_index()
            _build_outward_index("UK")
            return

        _lookup.clear()
        cache_dir = data_dir / f"NUTS-{settings.nuts_version}"
        cache_dir.mkdir(parents=True, exist_ok=True)

        base_url = settings.tercet_base_url
        countries = settings.countries
        timed_out = False

        with httpx.Client() as client:
            # Strategy 1: discover files from directory listing
            discovered = _discover_zip_urls(client, base_url)
            loaded_countries: set[str] = set()

            if discovered:
                logger.info("Discovered %d ZIP files from directory listing", len(discovered))
                for url in discovered:
                    if time.monotonic() > deadline:
                        logger.warning("Startup timeout reached during discovery downloads")
                        timed_out = True
                        break
                    cc = _infer_country_from_url(url)
                    if not cc:
                        continue
                    count = _download_and_parse_zip(client, url, cc, cache_dir, deadline=deadline)
                    if count > 0:
                        loaded_countries.add(cc)
                        logger.info("Loaded %d entries for %s", count, cc)

            # Strategy 2: for countries not yet loaded, try guessed URLs per-country
            remaining = [c for c in countries if c not in loaded_countries]
            if remaining and not timed_out:
                logger.info("Trying guessed URLs for %d remaining countries", len(remaining))
                for cc in remaining:
                    if time.monotonic() > deadline:
                        logger.warning("Startup timeout reached during country downloads")
                        timed_out = True
                        break
                    for url in _guess_zip_urls_for_country(base_url, cc):
                        count = _download_and_parse_zip(client, url, cc, cache_dir, deadline=deadline)
                        if count > 0:
                            loaded_countries.add(cc)
                            logger.info("Loaded %d entries for %s", count, cc)
                            break

            # Extra data sources (overwrite TERCET entries)
            if not timed_out:
                extra_count = _load_extra_sources(client, cache_dir, deadline=deadline)
                if extra_count:
                    logger.info("Extra sources added %d entries (overwrite mode)", extra_count)

            # NSPL (UK postcodes → ITL via the LAD bridge) — no-op when nspl_url unset
            if not timed_out and settings.nspl_url:
                lad_to_itl3 = _load_uk_itl_bridge(client)
                nspl_count = _load_nspl(client, settings.nspl_url, cache_dir, lad_to_itl3)
                if nspl_count > 0:
                    logger.info("Loaded %d entries for UK from NSPL", nspl_count)

            # NUTS region names
            if not timed_out:
                _download_nuts_names(client)

        elapsed = time.monotonic() - start_time
        logger.info(
            "Data loading complete: %d postal codes across %d countries (%.1fs)",
            len(_lookup),
            len(loaded_countries),
            elapsed,
        )

        if _lookup:
            # Fresh download succeeded (possibly partial on timeout)
            _data_loaded_at = datetime.now(timezone.utc).isoformat()
            if not _load_estimates_from_csv(estimates_csv):
                _load_estimates_from_db(db)
            _revalidate_estimates()
            _save_to_db(db)
            if timed_out:
                _data_stale = True
                logger.warning("Startup timed out — partial data loaded")
        elif db.is_file():
            # Download failed but stale DB exists — fallback
            _load_from_db(db)
            _data_loaded_at = _read_db_created_at(db)
            if not _load_estimates_from_csv(estimates_csv):
                _load_estimates_from_db(db)
            _revalidate_estimates()
            _load_nuts_names_from_db(db)
            _data_stale = True
            logger.warning("TERCET refresh failed — serving stale cache")

        _build_prefix_index()
        _build_outward_index("UK")


def _build_result(match_type: str, nuts3: str, nuts1: str = "", nuts2: str = "", **confidence) -> dict:
    """Construct a lookup result dict with names resolved.

    If nuts1/nuts2 are not provided, they are derived from nuts3.
    Confidence keys: nuts1_confidence, nuts2_confidence, nuts3_confidence.

    code_system is derived from the code itself: ITL codes are the UK's
    NUTS successor and uniquely carry the "TL" prefix (no NUTS country code is
    "TL"), so every "TL…" result is tagged "ITL" and all others "NUTS".
    """
    n1 = nuts1 or nuts3[:3]
    n2 = nuts2 or nuts3[:4]
    code_system = "ITL" if nuts3[:2] == "TL" else "NUTS"
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


def _matches_pattern(cc: str, raw: str) -> bool:
    """True if raw input matches the country's compiled postal pattern.

    Used as a format guard for territory input. Imported locally to avoid the
    postal_patterns ↔ data_loader circular import.
    """
    from app.postal_patterns import _COMPILED, _preprocess, POSTAL_PATTERNS

    pat = _COMPILED.get(cc)
    if pat is None:
        return False
    cleaned = _preprocess(raw.strip(), POSTAL_PATTERNS.get(cc))
    return pat.match(cleaned.upper()) is not None


def _lookup_cascade(country_code: str, postal_code: str) -> dict | None:
    """Look up NUTS codes for a given country + postal code.

    Tiered fall-through:
    1. Exact TERCET match → confidence 1.0
    2. Pre-computed estimate → stored confidence per level
    2b. Albania block map → district-block NUTS3, match_type='estimated' (#118)
    3.5. Outward-code lookup (UK) → majority-vote ITL3 for the outward code,
       match_type='estimated', medium confidence (before generic prefix)
    3. Runtime prefix-based estimation → calculated confidence
    4. Country-level majority vote → unanimous NUTS1/2, dominant NUTS3 (e.g. MT)
    5. Single-NUTS3 country fallback → confidence 1.0 (e.g. LI, CY, LU)

    Returns a dict with nuts1/2/3, match_type, and per-level confidence, or None.
    """
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
            "estimated",
            est["nuts3"],
            nuts1=est["nuts1"],
            nuts2=est["nuts2"],
            nuts1_confidence=est["nuts1_confidence"],
            nuts2_confidence=est["nuts2_confidence"],
            nuts3_confidence=est["nuts3_confidence"],
        )

    # Tier 2b: Albania authoritative block map (#118). AL has no TERCET and no
    # estimate rows; the official postal-district block scheme resolves any
    # well-formed 4-digit code to its NUTS3.
    if cc in AL_SUPPORTED:
        al_nuts3 = resolve_al_block(extracted)
        if al_nuts3 is not None:
            conf = settings.confidence_map["high"]
            return _build_result(
                "estimated",
                al_nuts3,
                nuts1_confidence=conf["nuts1"],
                nuts2_confidence=conf["nuts2"],
                nuts3_confidence=conf["nuts3"],
            )

    # Tier 3.5: Outward-code lookup (UK and any country flagged outward_only).
    # Placed before generic prefix estimation because the outward code is the
    # meaningful UK boundary: a curated majority vote over the whole outward
    # beats an arbitrary prefix match, and it yields match_type='estimated' with
    # medium confidence. extract_outward returns None for non-outward countries,
    # so this tier is inert for everything except UK.
    outward = extract_outward(cc, postal_code)
    if outward is not None:
        outward_hit = _outward_lookup.get((cc, outward))
        if outward_hit is not None:
            o_nuts3, _agreement = outward_hit
            conf = settings.confidence_map["medium"]
            return _build_result(
                "estimated",
                o_nuts3,
                nuts1_confidence=conf["nuts1"],
                nuts2_confidence=conf["nuts2"],
                nuts3_confidence=conf["nuts3"],
            )
        # Outward is the authoritative boundary for outward_only countries. A
        # miss means the code isn't in NSPL — stop here rather than fall through
        # to generic prefix estimation, which would answer from an arbitrary 1–2
        # character prefix (e.g. "SW" for an unknown SW99, mixing distinct ITL3s).
        return None

    # Tier 3: Runtime prefix-based estimation
    approx = _estimate_by_prefix(cc, extracted)
    if approx is not None:
        return approx

    # Tier 4: Country-level majority vote (unanimous NUTS1/2, dominant NUTS3)
    fallback = _country_fallback.get(cc)
    if fallback is not None:
        return _build_result(
            "approximate",
            fallback["nuts3"],
            nuts1=fallback["nuts1"],
            nuts2=fallback["nuts2"],
            nuts1_confidence=fallback["nuts1_confidence"],
            nuts2_confidence=fallback["nuts2_confidence"],
            nuts3_confidence=fallback["nuts3_confidence"],
        )

    # Tier 5: Single-NUTS3 country fallback (e.g. LI → LI000)
    nuts3 = _single_nuts3.get(cc)
    if nuts3 is not None:
        return _build_result("estimated", nuts3)

    return None


def _territory_payload(t, nuts_coverage: str) -> dict:
    return {
        "id": t.id,
        "iso": t.iso,
        "name": t.name,
        "status": t.status,
        "administering_country": t.administering_country,
        "legal_basis": t.legal_basis,
        "note": t.note,
        "nuts_coverage": nuts_coverage,
    }


def _territory_only_result(t) -> dict:
    """A 200-shaped result stating the territory and the absence of a NUTS code."""
    return {
        "code_system": "NUTS",
        "match_type": None,
        "nuts1": None,
        "nuts1_name": None,
        "nuts1_confidence": None,
        "nuts2": None,
        "nuts2_name": None,
        "nuts2_confidence": None,
        "nuts3": None,
        "nuts3_name": None,
        "nuts3_confidence": None,
        "territory": _territory_payload(t, "none"),
    }


def lookup(country_code: str, postal_code: str) -> dict | None:
    """Look up NUTS codes for a given country + postal code.

    Territory gate first (see app/territories.py), then the tier cascade:

    - Not a territory → the cascade runs unchanged.
    - Territory Eurostat classifies (``in_nuts``) → the full cascade runs against
      the administering country, and the result is labelled ``full``.
    - Territory outside NUTS → tier 1 (exact TERCET) only. A hit is labelled
      ``tercet_entry_only``; a miss returns a territory-only result. No
      approximation, prefix chain or fabricated code can be reached.

    Returns None when the input is unusable — an unknown code, or a postal code
    submitted under a territory ISO code it does not belong to.
    """
    from app.postal_patterns import extract_postal_code

    cc = normalize_country(country_code)
    probe_cc = cc
    t = None
    cls = classify_territory(cc, postal_code, extract_postal_code)
    if cls is not None:
        t = cls.territory
        probe_cc = t.validate_as or t.administering_country
        # An ISO-route code outside the territory's own ranges must not be
        # answered from the administering country's data: GL/2100 is a
        # well-formed Danish code, but it is not Greenlandic.
        if t.has_postal_system and not cls.postal_in_territory:
            return None

    if t is None:
        return _lookup_cascade(cc, postal_code)

    # Extracted once, reused by the pattern guard and the tier-1 probe below —
    # validating the guard against the raw string let wide-prefix territories
    # (e.g. ES-CN, PT-20) reject input (spacing, punctuation) that the cascade
    # itself would have accepted after extraction.
    extracted = extract_postal_code(probe_cc, postal_code)
    if t.has_postal_system and not _matches_pattern(probe_cc, extracted):
        return None

    if t.in_nuts:
        result = _lookup_cascade(probe_cc, postal_code)
        if result is None:
            return None
        result["territory"] = _territory_payload(t, "full")
        return result

    # Outside NUTS: tier 1 only. A territory with no postal system
    # (has_postal_system is False, e.g. the Dutch OCTs) cannot have a
    # postal-keyed Eurostat row by construction, so the probe never runs for
    # it — probe_cc would otherwise fall back to the administering country and
    # the raw code could key straight into that country's real data (e.g.
    # AW/1012 must never resolve as NL's Amsterdam row). whole_country
    # territories likewise have no Eurostat rows by construction — an
    # attributed code would be listed as exact or prefix.
    if t.has_postal_system and not t.whole_country:
        nuts3 = _lookup.get((probe_cc, extracted))
        if nuts3 is not None:
            result = _build_result("exact", nuts3)
            result["territory"] = _territory_payload(t, "tercet_entry_only")
            return result
    return _territory_only_result(t)
