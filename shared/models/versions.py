"""Pydantic models for the managed and standalone version contract."""

from datetime import date
from typing import Literal

from packaging.version import InvalidVersion, Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    """Reject misspelled fields instead of silently dropping version metadata."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def _parse_version(value: str, *, field_name: str) -> str:
    """Return a non-empty PEP 440-compatible version string."""

    try:
        Version(value)
    except InvalidVersion as exc:
        raise ValueError(f"{field_name} is not a valid version: {value!r}") from exc
    return value


def _require_https(value: str, *, field_name: str) -> str:
    """Keep source authorities explicit and transport-protected."""

    if not value.startswith("https://"):
        raise ValueError(f"{field_name} must be an https:// URL")
    return value


class VersionEntry(StrictModel):
    """A single version-pinned dependency."""

    pinned: str
    min: str | None
    source_url: str
    verified_date: date
    notes: str = Field(min_length=1)

    @field_validator("pinned", "min")
    @classmethod
    def _validate_versions(cls, value: str | None, info) -> str | None:
        if value is None:
            return value
        return _parse_version(value, field_name=info.field_name)

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        return _require_https(value, field_name="source_url")

    @model_validator(mode="after")
    def _validate_minimum(self) -> "VersionEntry":
        if self.min is not None and Version(self.min) > Version(self.pinned):
            raise ValueError(f"min ({self.min}) must be <= pinned ({self.pinned})")
        return self


class VerificationMetadata(StrictModel):
    """Repository-wide rules for interpreting managed and standalone versions."""

    verified_date: date
    managed_policy: str = Field(min_length=1)
    standalone_policy: str = Field(min_length=1)


class ObservedVersionEntry(StrictModel):
    """A source-backed version in either the managed or standalone matrix."""

    version: str
    release_ref: str | None = None
    source_url: str
    verified_date: date
    notes: str = Field(min_length=1)

    @field_validator("version")
    @classmethod
    def _validate_version(cls, value: str) -> str:
        return _parse_version(value, field_name="version")

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        return _require_https(value, field_name="source_url")


class ManagedInferenceProfile(StrictModel):
    """A source-backed model/image pairing validated for one NVIDIA host profile."""

    runtime_image: str = Field(min_length=1)
    default_model: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    course_coverage: Literal["implemented-needs-live-uat", "not-implemented"]
    source_url: str
    verified_date: date
    notes: str = Field(min_length=1)

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        return _require_https(value, field_name="source_url")


class VersionRange(StrictModel):
    """An inclusive semver range [min, max] used by bundled CLI tools whose
    upstream installer tolerates a band of versions (e.g. OpenShell's
    install-openshell.sh MIN_VERSION / MAX_VERSION / PIN_VERSION gate).

    Both bounds are parsed via ``packaging.version.Version``; the validator
    asserts ``Version(min) <= Version(max)``. Malformed or reversed bounds
    raise ``pydantic.ValidationError``.
    """

    min: str
    max: str

    @model_validator(mode="after")
    def _validate_ordering(self) -> "VersionRange":
        try:
            min_v = Version(self.min)
            max_v = Version(self.max)
        except InvalidVersion as exc:
            raise ValueError(
                f"VersionRange: could not parse semver bounds (min={self.min!r}, "
                f"max={self.max!r}): {exc}"
            ) from exc
        if min_v > max_v:
            raise ValueError(f"VersionRange: min ({self.min}) must be <= max ({self.max})")
        return self


class CliToolEntry(StrictModel):
    """A CLI tool tracked in versions.yaml with install-mechanism metadata.

    Version-pin semantics:
    * ``expected_version`` uses strict equality for managed or observed tools.
    * ``expected_version_range`` represents an inclusive compatibility band.

    Exactly one mode is required. Installer-driven entries additionally require
    their version environment variable, installer URL, and one installer pin.
    """

    # One of: "installer-env" (we drive a version env var), "bundled-with-nemoclaw",
    # "sandbox-embedded" (not installed as a CLI), "nvm" (node via .nvmrc).
    source: Literal[
        "installer-env",
        "bundled-with-nemoclaw",
        "sandbox-embedded",
        "nvm",
        "npm",
        "pypi",
    ]
    # Compatibility scope makes it explicit whether the entry is managed by
    # NemoClaw, host-optional, or informational-only.
    compatibility_scope: Literal[
        "nemoclaw-managed",
        "host-optional",
        "standalone-observation",
        "unspecified",
    ] = "unspecified"
    source_url: str
    verified_date: date
    # install_tag is the upstream ref passed to installer (e.g. "v0.0.17" for nemoclaw).
    install_tag: str | None = None
    # install_version is the bare version string installers expect (e.g. "0.0.7" for ollama).
    install_version: str | None = None
    # expected_version is what `<tool> --version` MUST print after install (equality).
    expected_version: str | None = None
    # expected_version_range is an inclusive semver band the installed tool must fall within.
    # Range wins over expected_version when both are set (see docstring).
    expected_version_range: VersionRange | None = None
    # env_var is the installer's version env var name (e.g. "NEMOCLAW_INSTALL_TAG").
    env_var: str | None = None
    # install_url is the installer endpoint for documentation and prepare scripts.
    install_url: str | None = None
    notes: str = Field(min_length=1)

    @field_validator("source_url")
    @classmethod
    def _validate_source_url(cls, value: str) -> str:
        return _require_https(value, field_name="source_url")

    @field_validator("install_url")
    @classmethod
    def _validate_install_url(cls, value: str | None) -> str | None:
        if value is None:
            return value
        return _require_https(value, field_name="install_url")

    @field_validator("install_version", "expected_version")
    @classmethod
    def _validate_optional_versions(cls, value: str | None, info) -> str | None:
        if value is None:
            return value
        return _parse_version(value, field_name=info.field_name)

    @model_validator(mode="after")
    def _validate_pin_contract(self) -> "CliToolEntry":
        pin_count = int(self.expected_version is not None) + int(
            self.expected_version_range is not None
        )
        if pin_count != 1:
            raise ValueError(
                "exactly one of expected_version or expected_version_range is required"
            )

        if self.source == "installer-env":
            if not self.env_var or not self.install_url:
                raise ValueError("installer-env entries require env_var and install_url")
            installer_pin_count = int(self.install_tag is not None) + int(
                self.install_version is not None
            )
            if installer_pin_count != 1:
                raise ValueError(
                    "installer-env entries require exactly one install_tag or install_version"
                )
        return self


class VersionsConfig(StrictModel):
    """Top-level schema for versions.yaml (D-16)."""

    metadata: VerificationMetadata
    managed_stack: dict[str, ObservedVersionEntry]
    managed_inference_profiles: dict[str, ManagedInferenceProfile]
    standalone_observations: dict[str, ObservedVersionEntry]
    runtime: dict[str, VersionEntry]
    cli_tools: dict[str, CliToolEntry]
    python: dict[str, VersionEntry]
    javascript: dict[str, VersionEntry]
    tooling: dict[str, VersionEntry]

    @model_validator(mode="after")
    def _validate_managed_parity(self) -> "VersionsConfig":
        """Prevent duplicated consumer pins from drifting from the managed matrix."""

        required_managed = {
            "nemoclaw",
            "openshell",
            "openclaw",
            "hermes",
            "langchain_deepagents_code",
        }
        missing = required_managed - self.managed_stack.keys()
        if missing:
            raise ValueError(
                "managed_stack is missing required entries: " + ", ".join(sorted(missing))
            )

        required_profiles = {"dgx_spark", "dgx_station", "linux_nvidia_gpu"}
        missing_profiles = required_profiles - self.managed_inference_profiles.keys()
        if missing_profiles:
            raise ValueError(
                "managed_inference_profiles is missing required entries: "
                + ", ".join(sorted(missing_profiles))
            )

        strict_mappings = {
            "nemoclaw": "nemoclaw",
            "openclaw": "openclaw",
            "hermes": "hermes",
            "langchain_deepagents_code": "deepagents_code",
        }
        for managed_name, cli_name in strict_mappings.items():
            cli = self.cli_tools.get(cli_name)
            if cli is None:
                raise ValueError(f"cli_tools is missing required entry: {cli_name}")
            expected = self.managed_stack[managed_name].version
            if cli.expected_version != expected:
                raise ValueError(
                    f"managed_stack.{managed_name} ({expected}) must match "
                    f"cli_tools.{cli_name}.expected_version ({cli.expected_version})"
                )

        openshell_cli = self.cli_tools.get("openshell")
        if openshell_cli is None or openshell_cli.expected_version_range is None:
            raise ValueError("cli_tools.openshell must declare expected_version_range")
        openshell_managed = self.managed_stack["openshell"].version
        if (
            openshell_cli.expected_version_range.min != openshell_managed
            or openshell_cli.expected_version_range.max != openshell_managed
        ):
            raise ValueError("managed OpenShell must match both bounds of its exact CLI range")

        observation_mappings = {
            "deepagents_python": "deepagents_python",
            "deepagents_javascript": "deepagents_npm",
            "pi": "pi",
            "ollama": "ollama",
        }
        for observation_name, cli_name in observation_mappings.items():
            observation = self.standalone_observations.get(observation_name)
            cli = self.cli_tools.get(cli_name)
            if observation is None or cli is None:
                raise ValueError(
                    "standalone observation mapping is incomplete: "
                    f"{observation_name} -> {cli_name}"
                )
            if cli.expected_version != observation.version:
                raise ValueError(
                    f"standalone_observations.{observation_name} ({observation.version}) "
                    f"must match cli_tools.{cli_name}.expected_version "
                    f"({cli.expected_version})"
                )
        return self
