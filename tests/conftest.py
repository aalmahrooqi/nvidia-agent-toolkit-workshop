"""Shared test fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def repo_root() -> Path:
    """Return the repository root directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def versions_yaml(repo_root: Path) -> Path:
    """Return path to versions.yaml."""
    return repo_root / "versions.yaml"


@pytest.fixture
def feature_status_yaml(repo_root: Path) -> Path:
    """Return path to feature_status.yaml."""
    return repo_root / "feature_status.yaml"
