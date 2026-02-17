from pydantic import BaseModel, Field


class NUTSResult(BaseModel):
    postal_code: str = Field(description="The queried postal code (normalized)")
    country_code: str = Field(description="ISO 3166-1 alpha-2 country code")
    nuts1: str = Field(description="NUTS level 1 code")
    nuts2: str = Field(description="NUTS level 2 code")
    nuts3: str = Field(description="NUTS level 3 code")


class ErrorResponse(BaseModel):
    detail: str


class PatternResponse(BaseModel):
    country_code: str = Field(description="ISO 3166-1 alpha-2 country code")
    regex: str = Field(description="Regex pattern for postal code validation")
    example: str = Field(description="Example postal code inputs")


class HealthResponse(BaseModel):
    status: str
    total_postal_codes: int
    nuts_version: str
