from typing import Literal

from pydantic import BaseModel, Field


class NUTSResult(BaseModel):
    postal_code: str = Field(description="The queried postal code (normalized)")
    country_code: str = Field(description="ISO 3166-1 alpha-2 country code")
    match_type: Literal["exact", "estimated", "approximate"] = Field(
        description="How the result was determined"
    )
    nuts1: str = Field(description="NUTS level 1 code")
    nuts1_name: str | None = Field(default=None, description="NUTS level 1 region name (Latin script)")
    nuts1_confidence: float = Field(
        description="Confidence score for NUTS1 (0.0–1.0)", ge=0.0, le=1.0
    )
    nuts2: str = Field(description="NUTS level 2 code")
    nuts2_name: str | None = Field(default=None, description="NUTS level 2 region name (Latin script)")
    nuts2_confidence: float = Field(
        description="Confidence score for NUTS2 (0.0–1.0)", ge=0.0, le=1.0
    )
    nuts3: str = Field(description="NUTS level 3 code")
    nuts3_name: str | None = Field(default=None, description="NUTS level 3 region name (Latin script)")
    nuts3_confidence: float = Field(
        description="Confidence score for NUTS3 (0.0–1.0)", ge=0.0, le=1.0
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
    total_nuts_names: int = Field(
        default=0, description="Number of NUTS region names loaded"
    )
    extra_sources: int = Field(
        default=0, description="Number of extra ZIP source URLs configured"
    )
    data_stale: bool = Field(
        description="True if serving expired cache after a failed TERCET refresh"
    )
    last_updated: str = Field(
        description="ISO 8601 timestamp of when TERCET data was last successfully loaded"
    )
