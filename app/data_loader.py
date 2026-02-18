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
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.config import settings

_NUTS3_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{1,3}$")

_MAX_UNCOMPRESSED_SIZE = 100 * 1024 * 1024  # 100 MB

logger = logging.getLogger(__name__)

# postal_code -> NUTS3 code, keyed by (country_code, normalized_postal_code)
_lookup: dict[tuple[str, str], str] = {}

# Pre-computed estimates keyed by (country_code, postal_code)
_estimates: dict[tuple[str, str], dict] = {}

# Prefix index: country_code -> prefix -> list of nuts3 codes
_prefix_index: dict[str, dict[str, list[str]]] = {}

# Staleness tracking
_data_stale: bool = False
_data_loaded_at: str = ""

# Extra source tracking
_extra_source_count: int = 0

# Protects against concurrent reload
_data_lock = threading.Lock()

def normalize_postal_code(code: str) -> str:
    """Normalize a postal code by removing spaces, dashes, and uppercasing.

    European postal codes use varied formats (PL: 00-950, SE: 111 22, UK: SW1A 1AA).
    Stripping all non-alphanumeric characters ensures consistent matching.
    """
    return re.sub(r"[^A-Za-z0-9]", "", code.strip()).upper()


def get_lookup_table() -> dict[tuple[str, str], str]:
    return _lookup


def get_estimates_table() -> dict[tuple[str, str], dict]:
    return _estimates


def get_loaded_countries() -> set[str]:
    """Return the set of country codes that have data loaded."""
    return {cc for cc, _ in _lookup}


def get_data_stale() -> bool:
    return _data_stale


def get_data_loaded_at() -> str:
    return _data_loaded_at


def get_extra_source_count() -> int:
    return _extra_source_count


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
            logger.info(
                "No country code in URL filename %s, will rely on CSV COUNTRY_CODE column", url
            )

        count = _download_and_parse_zip(client, url, cc, cache_dir, overwrite=True, deadline=deadline)
        if count > 0:
            logger.info("Extra source %s: loaded %d entries (overwrite mode)", url, count)
        else:
            logger.warning("Extra source %s: no entries loaded", url)
        total += count

    return total


def _read_db_created_at(db: Path) -> str:
    """Read the created_at timestamp from the DB metadata table."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            row = con.execute(
                "SELECT value FROM metadata WHERE key = 'created_at'"
            ).fetchone()
        finally:
            con.close()
        return row[0] if row else ""
    except Exception:
        return ""


def _discover_zip_urls(client: httpx.Client, base_url: str) -> list[str]:
    """Try to discover ZIP file URLs from the TERCET directory listing."""
    urls: list[str] = []
    try:
        resp = client.get(base_url, timeout=30)
        resp.raise_for_status()
        # Parse href attributes pointing to .zip files
        for match in re.finditer(r'href="([^"]*\.zip)"', resp.text):
            href = match.group(1)
            if href.startswith("http"):
                urls.append(href)
            else:
                urls.append(base_url.rstrip("/") + "/" + href.lstrip("/"))
    except Exception:
        logger.debug("Could not fetch directory listing from %s", base_url)
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


def _parse_csv_content(
    text: str, country_code: str, *, overwrite: bool = False
) -> int:
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
            "Could not identify columns in file for %s. "
            "Headers found: %s (need postal code + NUTS3 column)",
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
        logger.warning(
            "Skipped %d rows with invalid NUTS3 codes for %s", skipped, country_code
        )
    return count


def _download_zip(client: httpx.Client, url: str) -> bytes | None:
    """Download a ZIP with one retry on transient network errors.

    Returns raw bytes on success, None on failure or 404.
    """
    for attempt in range(2):
        try:
            resp = client.get(url, timeout=60, follow_redirects=True)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.content
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
    client: httpx.Client, url: str, country_code: str, cache_dir: Path,
    *, overwrite: bool = False, deadline: float = 0,
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
        cached.write_bytes(content)

    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".csv", ".tsv", ".txt")):
                    file_size = zf.getinfo(name).file_size
                    if file_size > _MAX_UNCOMPRESSED_SIZE:
                        logger.warning(
                            "Skipping %s in %s: uncompressed size %d bytes exceeds limit",
                            name, url, file_size,
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


def _db_path() -> Path:
    """Return the path for the SQLite cache DB, scoped by NUTS version."""
    return Path(settings.data_dir) / f"postalcode2nuts_NUTS-{settings.nuts_version}.db"


def _db_is_valid(db: Path) -> bool:
    """Check if the SQLite cache DB exists, matches current version, and is fresh."""
    if not db.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            cur = con.execute("SELECT key, value FROM metadata")
            meta = dict(cur.fetchall())
        finally:
            con.close()
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
        return True
    except Exception as exc:
        logger.info("DB cache unusable (%s), will rebuild", exc)
        return False


def _load_estimates_from_db(db: Path) -> bool:
    """Load pre-computed estimates from the DB. Graceful if table is missing."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            # Check if estimates table exists
            cur = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='estimates'"
            )
            if cur.fetchone() is None:
                return False
            rows = con.execute(
                "SELECT country_code, postal_code, nuts3, nuts2, nuts1, "
                "nuts3_confidence, nuts2_confidence, nuts1_confidence FROM estimates"
            ).fetchall()
        finally:
            con.close()
        if not rows:
            return False
        for cc, pc, n3, n2, n1, c3, c2, c1 in rows:
            _estimates[(cc, pc)] = {
                "nuts3": n3, "nuts2": n2, "nuts1": n1,
                "nuts3_confidence": c3, "nuts2_confidence": c2, "nuts1_confidence": c1,
            }
        logger.info("Loaded %d estimates from SQLite cache %s", len(rows), db.name)
        return True
    except Exception as exc:
        logger.warning("Failed to load estimates from DB: %s", exc)
        return False


def _load_estimates_from_csv(csv_path: Path) -> bool:
    """Load pre-computed estimates directly from CSV into memory."""
    if not csv_path.is_file():
        return False
    try:
        count = 0
        skipped = 0
        with open(csv_path, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
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

                _estimates[(cc, pc)] = {
                    "nuts3": n3, "nuts2": n2, "nuts1": n1,
                    "nuts3_confidence": conf["nuts3"],
                    "nuts2_confidence": conf["nuts2"],
                    "nuts1_confidence": conf["nuts1"],
                }
                count += 1
        if skipped:
            logger.warning("Skipped %d estimate rows with unknown confidence labels", skipped)
        if count:
            logger.info("Loaded %d estimates from %s", count, csv_path)
        return count > 0
    except Exception as exc:
        logger.warning("Failed to load estimates from CSV: %s", exc)
        return False


def _revalidate_estimates() -> int:
    """Remove estimates that now have exact matches. Returns count removed."""
    to_remove = [key for key in _estimates if key in _lookup]
    for key in to_remove:
        del _estimates[key]
    if to_remove:
        logger.info("Removed %d estimates that now have exact TERCET matches", len(to_remove))
    return len(to_remove)


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

    return {
        "match_type": "approximate",
        "nuts1": nuts1_winner,
        "nuts1_confidence": c1,
        "nuts2": nuts2_winner,
        "nuts2_confidence": c2,
        "nuts3": nuts3_winner,
        "nuts3_confidence": c3,
    }


def _load_from_db(db: Path) -> bool:
    """Load the lookup table from SQLite cache. Returns True on success."""
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(
                "SELECT country_code, postal_code, nuts3 FROM lookup"
            ).fetchall()
        finally:
            con.close()
        if not rows:
            return False
        for cc, pc, nuts3 in rows:
            _lookup[(cc, pc)] = nuts3
        logger.info("Loaded %d entries from SQLite cache %s", len(rows), db.name)
        return True
    except Exception as exc:
        logger.warning("Failed to load from DB cache: %s", exc)
        _lookup.clear()
        return False


def _save_to_db(db: Path) -> None:
    """Persist the lookup table and estimates to SQLite cache with atomic rename."""
    tmp = db.with_suffix(".db.tmp")
    try:
        tmp.unlink(missing_ok=True)
        con = sqlite3.connect(str(tmp))
        try:
            con.execute(
                "CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
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
                    (cc, pc, est["nuts3"], est["nuts2"], est["nuts1"],
                     est["nuts3_confidence"], est["nuts2_confidence"], est["nuts1_confidence"])
                    for (cc, pc), est in _estimates.items()
                ],
            )
            con.executemany(
                "INSERT INTO metadata (key, value) VALUES (?, ?)",
                [
                    ("nuts_version", settings.nuts_version),
                    ("created_at", datetime.now(timezone.utc).isoformat()),
                    ("entry_count", str(len(_lookup))),
                    ("estimate_count", str(len(_estimates))),
                    ("extra_sources_hash", _extra_sources_hash()),
                ],
            )
            con.commit()
        finally:
            con.close()
        tmp.replace(db)
        logger.info(
            "Saved %d entries + %d estimates to SQLite cache %s",
            len(_lookup), len(_estimates), db.name,
        )
    except Exception as exc:
        logger.warning("Failed to save DB cache: %s", exc)
        tmp.unlink(missing_ok=True)


def load_data() -> None:
    """Download all TERCET flat files and build the in-memory lookup table."""
    global _data_stale, _data_loaded_at, _extra_source_count

    with _data_lock:
        _lookup.clear()
        _estimates.clear()
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
            _build_prefix_index()
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
                logger.info(
                    "Discovered %d ZIP files from directory listing", len(discovered)
                )
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
                logger.info(
                    "Trying guessed URLs for %d remaining countries", len(remaining)
                )
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
            _data_stale = True
            logger.warning("TERCET refresh failed — serving stale cache")

        _build_prefix_index()


def lookup(country_code: str, postal_code: str) -> dict | None:
    """Look up NUTS codes for a given country + postal code.

    Three-tier fall-through:
    1. Exact TERCET match → confidence 1.0
    2. Pre-computed estimate → stored confidence per level
    3. Runtime prefix-based estimation → calculated confidence

    Returns a dict with nuts1/2/3, match_type, and per-level confidence, or None.
    """
    from app.postal_patterns import extract_postal_code

    # Handle Greece: ISO is GR but GISCO uses EL
    cc = country_code.upper()
    if cc == "GR":
        cc = "EL"

    extracted = extract_postal_code(cc, postal_code)
    key = (cc, extracted)

    # Tier 1: Exact TERCET match
    nuts3 = _lookup.get(key)
    if nuts3 is not None:
        return {
            "match_type": "exact",
            "nuts1": nuts3[:3],
            "nuts1_confidence": 1.0,
            "nuts2": nuts3[:4],
            "nuts2_confidence": 1.0,
            "nuts3": nuts3,
            "nuts3_confidence": 1.0,
        }

    # Tier 2: Pre-computed estimate
    est = _estimates.get(key)
    if est is not None:
        return {
            "match_type": "estimated",
            "nuts1": est["nuts1"],
            "nuts1_confidence": est["nuts1_confidence"],
            "nuts2": est["nuts2"],
            "nuts2_confidence": est["nuts2_confidence"],
            "nuts3": est["nuts3"],
            "nuts3_confidence": est["nuts3_confidence"],
        }

    # Tier 3: Runtime prefix-based estimation
    return _estimate_by_prefix(cc, extracted)
