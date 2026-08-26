from typing import Literal

from pydantic import BaseModel, Field


class TerritoryInfo(BaseModel):
    """Identifies an EU outermost region, OCT, or other non-NUTS territory."""

    id: str = Field(description="Registry id — the ISO code where one exists, else e.g. 'ES-CN'")
    iso: str | None = Field(default=None, description="ISO 3166-1 alpha-2 code, where one exists")
    name: str = Field(description="Territory name")
    status: Literal["outermost_region", "oct", "other"] = Field(
        description=(
            "'outermost_region' — Art. 349 TFEU, full EU territory; "
            "'oct' — Part Four TFEU, associated with but not part of the EU; "
            "'other' — any other territory outside the ordinary NUTS country set"
        )
    )
    administering_country: str = Field(description="ISO 3166-1 alpha-2 code of the administering country")
    legal_basis: str | None = Field(default=None, description="Treaty provision establishing the status")
    note: str | None = Field(default=None, description="Plain-language explanation of the territory's position")
    nuts_coverage: Literal["full", "tercet_entry_only", "none"] = Field(
        description=(
            "'full' — Eurostat classifies the territory and the code resolved; "
            "'tercet_entry_only' — the territory is outside NUTS but the GISCO TERCET "
            "file carries this exact code; 'none' — no NUTS code exists, all nuts fields null"
        )
    )


class NUTSResult(BaseModel):
    postal_code: str = Field(description="The queried postal code (normalized)")
    country_code: str = Field(description="ISO 3166-1 alpha-2 country code")
    code_system: Literal["NUTS", "ITL"] = Field(
        default="NUTS",
        description=(
            "Territorial coding scheme of the nuts1/2/3 fields. 'NUTS' for GISCO-sourced "
            "EU/EFTA/candidate data; 'ITL' for UK data from the ONS NSPL."
        ),
    )
    match_type: Literal["exact", "estimated", "approximate"] | None = Field(
        default=None,
        description="How the result was determined; null when no NUTS code was produced",
    )
    nuts1: str | None = Field(default=None, description="NUTS level 1 code")
    nuts1_name: str | None = Field(default=None, description="NUTS level 1 region name (Latin script)")
    nuts1_confidence: float | None = Field(
        default=None, description="Confidence score for NUTS1 (0.0–1.0)", ge=0.0, le=1.0
    )
    nuts2: str | None = Field(default=None, description="NUTS level 2 code")
    nuts2_name: str | None = Field(default=None, description="NUTS level 2 region name (Latin script)")
    nuts2_confidence: float | None = Field(
        default=None, description="Confidence score for NUTS2 (0.0–1.0)", ge=0.0, le=1.0
    )
    nuts3: str | None = Field(default=None, description="NUTS level 3 code")
    nuts3_name: str | None = Field(default=None, description="NUTS level 3 region name (Latin script)")
    nuts3_confidence: float | None = Field(
        default=None, description="Confidence score for NUTS3 (0.0–1.0)", ge=0.0, le=1.0
    )
    territory: TerritoryInfo | None = Field(
        default=None,
        description="Present when the postal code lies in an outermost region, an OCT, or another non-NUTS territory",
    )


class ErrorResponse(BaseModel):
    detail: str


class PatternResponse(BaseModel):
    country_code: str = Field(description="ISO 3166-1 alpha-2 country code")
    regex: str = Field(description="Regex pattern for postal code validation")
    example: str = Field(description="Example postal code inputs")


class HealthResponse(BaseModel):
    status: str
    total_postal_codes: int
    total_estimates: int
    nuts_version: str
    total_nuts_names: int = Field(default=0, description="Number of NUTS region names loaded")
    extra_sources: int = Field(default=0, description="Number of extra ZIP source URLs configured")
    patterns_version: str = Field(description="Version of the postal_patterns.json file")
    data_stale: bool = Field(description="True if serving expired cache after a failed TERCET refresh")
    last_updated: str = Field(
        description="ISO 8601 timestamp of when TERCET data was last successfully loaded"
    )
    token_db_stale: bool | None = None
    estimates_refresh_stale: bool | None = None
    geocoder_configured: bool = Field(default=False, description="True if PC2NUTS_PHOTON_URL is set")
    pip_ready: bool = Field(default=False, description="True if NUTS polygons loaded for /resolve")


class GeocodeInfo(BaseModel):
    status: Literal[
        "ok",
        "snapped",
        "no_result",
        "pip_outside",
        "no_address",
        "not_attempted",
        "geocoder_unavailable",
    ] = Field(description="What the geocode fallback did")
    lat: float | None = None
    lon: float | None = None
    nuts3: str | None = None
    snap_km: float | None = Field(
        default=None, description="Distance (km) the point was snapped to the nearest NUTS-3 region"
    )


class ResolveResponse(BaseModel):
    country_code: str
    postal_code: str
    resolved_via: Literal["postal", "geocode", "none"] = Field(
        description="Which path produced the returned NUTS"
    )
    match_type: str | None = Field(description="Postal-path match type")
    nuts1: str | None = None
    nuts1_name: str | None = None
    nuts2: str | None = None
    nuts2_name: str | None = None
    nuts3: str | None = None
    nuts3_name: str | None = None
    nuts3_confidence: float | None = None
    territory: TerritoryInfo | None = None
    geocode: GeocodeInfo
