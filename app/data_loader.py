"""Download and parse TERCET flat files into an in-memory lookup table."""

import csv
import io
import logging
import os
import re
import zipfile
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# postal_code -> NUTS3 code, keyed by (country_code, normalized_postal_code)
_lookup: dict[tuple[str, str], str] = {}


def normalize_postal_code(code: str) -> str:
    """Normalize a postal code by removing spaces, dashes, and uppercasing.

    European postal codes use varied formats (PL: 00-950, SE: 111 22, UK: SW1A 1AA).
    Stripping all non-alphanumeric characters ensures consistent matching.
    """
    return re.sub(r"[^A-Za-z0-9]", "", code.strip()).upper()


def get_lookup_table() -> dict[tuple[str, str], str]:
    return _lookup


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


def _guess_zip_urls(base_url: str, countries: list[str]) -> list[str]:
    """Generate candidate ZIP URLs using known naming patterns."""
    base = base_url.rstrip("/")
    urls: list[str] = []
    # Try multiple postal-code-year / version combinations
    for pc_year in ("2025", "2024", "2023", "2020"):
        for version in ("v1.0", "v2.0", "v3.0", "v4.0"):
            for cc in countries:
                urls.append(
                    f"{base}/pc{pc_year}_{cc}_NUTS-{settings.nuts_version}_{version}.zip"
                )
    return urls


def _parse_csv_content(text: str, country_code: str) -> int:
    """Parse CSV/TSV content and populate the lookup table. Returns row count."""
    count = 0
    # Auto-detect delimiter
    first_line = text.split("\n", 1)[0]
    delimiter = "\t" if "\t" in first_line else ","
    if ";" in first_line and delimiter == ",":
        # Some EU files use semicolons
        delimiter = ";"

    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    fieldnames = [f.strip().upper() for f in (reader.fieldnames or [])]

    # Find the postal code column
    pc_col = None
    for candidate in ("CODE", "PC", "POSTAL_CODE", "POSTCODE", "PC_FMT"):
        if candidate in fieldnames:
            pc_col = candidate
            break

    # Find the NUTS3 column
    nuts3_col = None
    for candidate in ("NUTS3", "NUTS_ID", "NUTS3_2024", "NUTS3_2021", "NUTS"):
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

    # Map back to original-case field names from DictReader
    orig_fields = list(reader.fieldnames or [])
    pc_orig = orig_fields[fieldnames.index(pc_col)]
    nuts3_orig = orig_fields[fieldnames.index(nuts3_col)]

    for row in reader:
        pc = row.get(pc_orig, "")
        nuts3 = row.get(nuts3_orig, "")
        if pc and nuts3:
            key = (country_code.upper(), normalize_postal_code(pc))
            _lookup[key] = nuts3.strip()
            count += 1

    return count


def _download_and_parse_zip(
    client: httpx.Client, url: str, country_code: str, cache_dir: Path
) -> int:
    """Download a single ZIP, extract CSVs, parse them. Returns row count."""
    filename = url.rsplit("/", 1)[-1]
    cached = cache_dir / filename

    content: bytes
    if cached.exists():
        logger.info("Using cached file %s", cached)
        content = cached.read_bytes()
    else:
        logger.info("Downloading %s", url)
        try:
            resp = client.get(url, timeout=60, follow_redirects=True)
            if resp.status_code == 404:
                return 0
            resp.raise_for_status()
            content = resp.content
            cached.write_bytes(content)
        except httpx.HTTPStatusError:
            return 0
        except httpx.RequestError as exc:
            logger.warning("Failed to download %s: %s", url, exc)
            return 0

    total = 0
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for name in zf.namelist():
                if name.lower().endswith((".csv", ".tsv", ".txt")):
                    raw = zf.read(name)
                    # Try common encodings
                    for enc in ("utf-8-sig", "utf-8", "latin-1"):
                        try:
                            text = raw.decode(enc)
                            break
                        except UnicodeDecodeError:
                            continue
                    else:
                        text = raw.decode("latin-1")
                    total += _parse_csv_content(text, country_code)
    except zipfile.BadZipFile:
        logger.warning("Bad ZIP file from %s", url)
    return total


def load_data() -> None:
    """Download all TERCET flat files and build the in-memory lookup table."""
    _lookup.clear()
    cache_dir = Path(settings.data_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    base_url = settings.tercet_base_url
    countries = settings.countries

    with httpx.Client() as client:
        # Strategy 1: discover files from directory listing
        discovered = _discover_zip_urls(client, base_url)
        loaded_countries: set[str] = set()

        if discovered:
            logger.info(
                "Discovered %d ZIP files from directory listing", len(discovered)
            )
            for url in discovered:
                # Extract country code from filename like pc2025_AT_NUTS-2024_v1.0.zip
                m = re.search(r"pc\d{4}_([A-Z]{2})_", url)
                if not m:
                    continue
                cc = m.group(1)
                count = _download_and_parse_zip(client, url, cc, cache_dir)
                if count > 0:
                    loaded_countries.add(cc)
                    logger.info("Loaded %d entries for %s", count, cc)

        # Strategy 2: for countries not yet loaded, try guessed URLs
        remaining = [c for c in countries if c not in loaded_countries]
        if remaining:
            logger.info(
                "Trying guessed URLs for %d remaining countries", len(remaining)
            )
            guessed = _guess_zip_urls(base_url, remaining)
            for url in guessed:
                m = re.search(r"pc\d{4}_([A-Z]{2})_", url)
                if not m:
                    continue
                cc = m.group(1)
                if cc in loaded_countries:
                    continue
                count = _download_and_parse_zip(client, url, cc, cache_dir)
                if count > 0:
                    loaded_countries.add(cc)
                    logger.info("Loaded %d entries for %s", count, cc)

    logger.info(
        "Data loading complete: %d postal codes across %d countries",
        len(_lookup),
        len(loaded_countries),
    )


def lookup(country_code: str, postal_code: str) -> dict | None:
    """Look up NUTS codes for a given country + postal code.

    Returns a dict with nuts1, nuts2, nuts3 or None if not found.
    """
    # Handle Greece: ISO is GR but GISCO uses EL
    cc = country_code.upper()
    if cc == "GR":
        cc = "EL"

    key = (cc, normalize_postal_code(postal_code))
    nuts3 = _lookup.get(key)
    if nuts3 is None:
        return None

    # NUTS3 code structure: CC + 1 digit (NUTS1) + 1-2 digits (NUTS2) + 1 digit (NUTS3)
    # e.g. PL213 -> NUTS1=PL2, NUTS2=PL21, NUTS3=PL213
    # Length varies: country prefix (2 chars) + 1 char = NUTS1,
    #               country prefix (2 chars) + 2 chars = NUTS2,
    #               country prefix (2 chars) + 3 chars = NUTS3
    nuts1 = nuts3[:3]  # e.g. "PL2"
    nuts2 = nuts3[:4]  # e.g. "PL21"

    return {
        "nuts1": nuts1,
        "nuts2": nuts2,
        "nuts3": nuts3,
    }
