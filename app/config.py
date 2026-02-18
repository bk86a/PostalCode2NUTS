import json
import re
from pathlib import Path

from pydantic_settings import BaseSettings

_defaults = json.loads((Path(__file__).parent / "settings.json").read_text())


class Settings(BaseSettings):
    tercet_base_url: str = _defaults["tercet_base_url"]
    data_dir: str = "./data"
    db_cache_ttl_days: int = 30
    estimates_csv: str = "./tests/tercet_missing_codes.csv"
    extra_sources: str = ""
    rate_limit: str = "60/minute"
    startup_timeout: int = 300
    docs_enabled: bool = True
    cors_origins: str = "*"

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


settings = Settings()
