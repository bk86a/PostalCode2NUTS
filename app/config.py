import re

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    tercet_base_url: str = (
        "https://gisco-services.ec.europa.eu/tercet/NUTS-2024/"
    )
    data_dir: str = "./data"
    db_cache_ttl_days: int = 30
    estimates_csv: str = "./tests/tercet_missing_codes.csv"

    # Countries with TERCET flat files available
    countries: list[str] = [
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES",
        "FI", "FR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
        "NL", "PL", "PT", "RO", "SE", "SI", "SK",  # EU-27
        "CH", "IS", "LI", "NO",                      # EFTA
        "MK", "RS", "TR",                             # Candidates
    ]

    model_config = {"env_prefix": "PC2NUTS_"}

    @property
    def nuts_version(self) -> str:
        """Derive NUTS version from the base URL (e.g. 'NUTS-2024' → '2024')."""
        m = re.search(r"NUTS-(\d{4})", self.tercet_base_url)
        if m:
            return m.group(1)
        return "unknown"


settings = Settings()
