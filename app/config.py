from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    tercet_base_url: str = (
        "https://gisco-services.ec.europa.eu/tercet/NUTS-2024/"
    )
    nuts_version: str = "2024"
    data_dir: str = "./data"
    db_cache_ttl_days: int = 30

    # Countries covered by TERCET (EU + EFTA + candidates + UK)
    countries: list[str] = [
        "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "EL", "ES",
        "FI", "FR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT",
        "NL", "PL", "PT", "RO", "SE", "SI", "SK",            # EU-27
        "CH", "IS", "LI", "NO",                                # EFTA
        "AL", "BA", "GE", "ME", "MD", "MK", "RS", "TR", "UA", "XK",  # Candidates
        "UK",                                                   # UK
    ]

    model_config = {"env_prefix": "PC2NUTS_"}


settings = Settings()
