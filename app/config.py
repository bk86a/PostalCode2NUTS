import json
import re
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

_settings_path = Path(__file__).parent / "settings.json"
try:
    _defaults = json.loads(_settings_path.read_text())
except (json.JSONDecodeError, OSError) as _exc:
    raise SystemExit(f"Fatal: failed to load {_settings_path}: {_exc}") from _exc


class Settings(BaseSettings):
    tercet_base_url: str = _defaults["tercet_base_url"]
    data_dir: str = "./data"
    db_cache_ttl_days: int = 30
    estimates_csv: str = "./tercet_missing_codes.csv"
    extra_sources: str = ""
    trusted_tokens_raw: str = Field(default="", validation_alias="PC2NUTS_TRUSTED_TOKENS")
    token_db_url: str = ""
    token_db_auth_token: str = ""
    token_refresh_seconds: int = Field(default=60, ge=1)
    rate_limit: str = _defaults.get("rate_limit", "60/minute")
    rate_limit_headers: bool = _defaults.get("rate_limit_headers", True)
    cache_max_age: int = _defaults.get("cache_max_age", 3600)
    startup_timeout: int = 300
    docs_enabled: bool = True
    cors_origins: str = "*"
    access_log_file: str = ""
    access_log_max_mb: int = 10
    access_log_backup_count: int = 5

    # Countries with TERCET flat files available
    countries: list[str] = _defaults["countries"]

    model_config = {"env_prefix": "PC2NUTS_"}

    @property
    def extra_source_urls(self) -> list[str]:
        """Parse PC2NUTS_EXTRA_SOURCES comma-separated list into URL list."""
        if not self.extra_sources.strip():
            return []
        return [u.strip() for u in self.extra_sources.split(",") if u.strip()]

    @property
    def trusted_tokens(self) -> frozenset[str]:
        """Parse PC2NUTS_TRUSTED_TOKENS comma-separated list into a frozenset.

        Whitespace around tokens is stripped; empty entries are dropped.
        Returns an empty frozenset when unset or empty (auth bypass disabled).
        """
        raw = self.trusted_tokens_raw
        if not raw.strip():
            return frozenset()
        return frozenset(t.strip() for t in raw.split(",") if t.strip())

    @property
    def nuts_version(self) -> str:
        """Derive NUTS version from the base URL (e.g. 'NUTS-2024' → '2024')."""
        m = re.search(r"NUTS-(\d{4})", self.tercet_base_url)
        if m:
            return m.group(1)
        return "unknown"

    @property
    def confidence_map(self) -> dict:
        return _defaults["confidence_map"]

    @property
    def approximate_confidence_caps(self) -> dict:
        return _defaults["approximate_confidence_caps"]

    @property
    def approximate_min_confidence(self) -> float:
        return _defaults["approximate_min_confidence"]

    @property
    def single_nuts3_fallback(self) -> dict[str, str]:
        """Country → NUTS3 code mapping for territories Eurostat treats as a single
        nationwide unit at every NUTS level (e.g. ME → ME000). Merged into the
        auto-detected single-NUTS3 set so countries with no TERCET data still
        resolve via the Tier 5 fallback."""
        return _defaults.get("single_nuts3_fallback", {})


settings = Settings()
