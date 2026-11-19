# SPDX-License-Identifier: Apache-2.0
"""Tests for source-backed feature and course verification status."""

from copy import deepcopy

import pytest
import yaml
from pydantic import ValidationError

from shared.models.features import FeatureStatusConfig


def _load(repo_root):
    return yaml.safe_load((repo_root / "feature_status.yaml").read_text(encoding="utf-8"))


def test_feature_status_round_trip(repo_root):
    config = FeatureStatusConfig(**_load(repo_root))

    assert config.features
    assert all(feature.evidence_url.startswith("https://") for feature in config.features)
    assert {
        "upstream_status",
        "course_status",
    } <= config.features[0].model_fields_set


def test_feature_status_rejects_unknown_fields(repo_root):
    data = _load(repo_root)
    data["features"][0]["status"] = "demo-safe"

    with pytest.raises(ValidationError, match="extra_forbidden"):
        FeatureStatusConfig(**data)


def test_feature_status_rejects_duplicate_names(repo_root):
    data = _load(repo_root)
    duplicate = deepcopy(data["features"][0])
    data["features"].append(duplicate)

    with pytest.raises(ValidationError, match="must be unique"):
        FeatureStatusConfig(**data)


def test_feature_status_rejects_non_https_evidence(repo_root):
    data = _load(repo_root)
    data["features"][0]["evidence_url"] = "http://example.com/evidence"

    with pytest.raises(ValidationError, match="https://"):
        FeatureStatusConfig(**data)
