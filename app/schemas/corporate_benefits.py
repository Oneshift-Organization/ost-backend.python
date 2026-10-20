"""Informational opportunities, never an approval or an input to the score."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, HttpUrl, field_serializer


class CorporateOpportunity(BaseModel):
    id: str
    provider: Literal["aws", "azure", "gcp", "oci"]
    kind: Literal["credits", "support"]
    title: str
    summary: str
    eligibility: Literal["not_verified"] = "not_verified"
    requirements: list[str]
    source_url: HttpUrl

    @field_serializer("source_url")
    def serialize_source_url(self, value: HttpUrl) -> str:
        return str(value)


class CorporateBenefits(BaseModel):
    catalog_version: str
    reviewed_on: date
    score_effect: Literal["none"] = "none"
    opportunities: list[CorporateOpportunity] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)

    @field_serializer("reviewed_on")
    def serialize_reviewed_on(self, value: date) -> str:
        # Analysis JSONB persistence also uses model_dump() in Python mode.
        return value.isoformat()
