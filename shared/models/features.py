"""Pydantic models for source-backed course feature status."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class FeatureEntry(BaseModel):
    """Separate upstream product support from this course's verification state."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    upstream_status: Literal[
        "supported",
        "tested",
        "caveated",
        "experimental",
        "deprecated",
        "unknown",
    ]
    course_status: Literal[
        "live-verified",
        "statically-verified",
        "needs-live-uat",
        "planned",
        "excluded",
        "blocked",
    ]
    affected_versions: list[str] = Field(min_length=1)
    evidence_url: str
    verified_date: date
    conditions: list[str] = Field(default_factory=list)
    issue_url: str | None = None
    guidance: str = Field(min_length=1)

    @field_validator("evidence_url", "issue_url")
    @classmethod
    def _validate_urls(cls, value: str | None, info) -> str | None:
        if value is not None and not value.startswith("https://"):
            raise ValueError(f"{info.field_name} must be an https:// URL")
        return value


class FeatureStatusConfig(BaseModel):
    """Top-level schema for feature_status.yaml."""

    model_config = ConfigDict(extra="forbid")

    features: list[FeatureEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reject_ambiguous_or_placeholder_entries(self) -> "FeatureStatusConfig":
        names = [feature.name for feature in self.features]
        if len(names) != len(set(names)):
            raise ValueError("feature_status.yaml feature names must be unique")

        placeholders = [
            feature.name
            for feature in self.features
            if any(
                "TBD" in value.upper() for value in (feature.evidence_url, feature.issue_url or "")
            )
        ]
        if placeholders:
            raise ValueError(
                "feature_status.yaml URLs must be concrete; placeholder found for "
                + ", ".join(placeholders)
            )
        return self
