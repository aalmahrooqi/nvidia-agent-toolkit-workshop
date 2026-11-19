"""Focused tests for the strict course manifest contract."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from shared.models.course import CourseManifest


def _entry(
    entry_id: str,
    notebook_path: str,
    *,
    module: int = 1,
    prerequisites: list[str] | None = None,
    prerequisites_any: list[str] | None = None,
    **overrides: object,
) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": entry_id,
        "title": f"Title for {entry_id}",
        "path": notebook_path,
        "track": "test-track",
        "module": module,
        "duration": 20,
        "prerequisites": prerequisites or [],
        "prerequisites_any": prerequisites_any or [],
        "skills": ["test-skill"],
        "activity_type": "concept",
        "difficulty": "beginner",
    }
    entry.update(overrides)
    return entry


def _learning_path(
    path_id: str,
    variants: list[dict[str, object]],
    **overrides: object,
) -> dict[str, object]:
    path: dict[str, object] = {
        "id": path_id,
        "title": f"Title for {path_id}",
        "variants": variants,
    }
    path.update(overrides)
    return path


def _variant(
    variant_id: str,
    entry_ids: list[str],
    **overrides: object,
) -> dict[str, object]:
    variant: dict[str, object] = {
        "id": variant_id,
        "title": f"Title for {variant_id}",
        "entry_ids": entry_ids,
    }
    variant.update(overrides)
    return variant


def test_course_manifest_accepts_unique_acyclic_entries() -> None:
    manifest = CourseManifest.model_validate(
        {
            "entries": [
                _entry("first", "tracks/test/01_first.ipynb"),
                _entry(
                    "second",
                    "tracks/test/02_second.ipynb",
                    prerequisites=["first"],
                ),
            ]
        }
    )

    assert [entry.id for entry in manifest.entries] == ["first", "second"]
    assert manifest.learning_paths == []


def test_course_manifest_supports_all_of_and_one_of_prerequisites() -> None:
    manifest = CourseManifest.model_validate(
        {
            "entries": [
                _entry("concepts", "tracks/test/01_concepts.ipynb", duration=15),
                _entry("dgx", "tracks/test/02_dgx.ipynb", module=2, duration=25),
                _entry("brev", "tracks/test/03_brev.ipynb", module=2, duration=20),
                _entry(
                    "policy",
                    "tracks/test/04_policy.ipynb",
                    module=3,
                    duration=30,
                    prerequisites=["concepts"],
                    prerequisites_any=["dgx", "brev"],
                ),
            ],
            "learning_paths": [
                _learning_path(
                    "core",
                    [
                        _variant("dgx-spark", ["concepts", "dgx", "policy"]),
                        _variant("brev-cloud", ["concepts", "brev", "policy"]),
                    ],
                )
            ],
        }
    )

    variants = manifest.learning_paths[0].variants
    assert variants[0].entry_ids == ["concepts", "dgx", "policy"]
    assert variants[0].calculated_duration == 70
    assert variants[1].calculated_duration == 65
    assert manifest.model_dump()["learning_paths"][0]["variants"][0]["calculated_duration"] == 70


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        (
            [
                _entry("duplicate", "tracks/test/01.ipynb"),
                _entry("duplicate", "tracks/test/02.ipynb"),
            ],
            "IDs must be unique",
        ),
        (
            [
                _entry("first", "tracks/test/same.ipynb"),
                _entry("second", "tracks/test/same.ipynb"),
            ],
            "paths must be unique",
        ),
        (
            [_entry("first", "tracks/test/01.ipynb", prerequisites=["missing"])],
            "unknown prerequisite IDs",
        ),
        (
            [
                _entry("first", "tracks/test/01.ipynb", prerequisites=["second"]),
                _entry("second", "tracks/test/02.ipynb", prerequisites=["first"]),
            ],
            "contains a cycle",
        ),
    ],
)
def test_course_manifest_rejects_invalid_graphs(
    entries: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        CourseManifest.model_validate({"entries": entries})


@pytest.mark.parametrize(
    ("entries", "message"),
    [
        (
            [
                _entry(
                    "lesson",
                    "tracks/test/lesson.ipynb",
                    prerequisites_any=["missing"],
                )
            ],
            "unknown prerequisite IDs",
        ),
        (
            [
                _entry(
                    "lesson",
                    "tracks/test/lesson.ipynb",
                    prerequisites_any=["lesson"],
                )
            ],
            "cannot depend on itself",
        ),
        (
            [
                _entry(
                    "early",
                    "tracks/test/early.ipynb",
                    module=1,
                    prerequisites_any=["later"],
                ),
                _entry("later", "tracks/test/later.ipynb", module=2),
            ],
            "cannot point to a later module",
        ),
        (
            [
                _entry(
                    "first",
                    "tracks/test/first.ipynb",
                    prerequisites_any=["second"],
                ),
                _entry(
                    "second",
                    "tracks/test/second.ipynb",
                    prerequisites=["first"],
                ),
            ],
            "contains a cycle",
        ),
    ],
)
def test_course_manifest_validates_alternate_prerequisite_edges(
    entries: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        CourseManifest.model_validate({"entries": entries})


@pytest.mark.parametrize(
    "override",
    [
        {"prerequisites_any": ["first", "first"]},
        {"prerequisites": ["first"], "prerequisites_any": ["first"]},
    ],
)
def test_course_manifest_rejects_ambiguous_prerequisite_groups(
    override: dict[str, object],
) -> None:
    entries = [
        _entry("first", "tracks/test/first.ipynb"),
        _entry("second", "tracks/test/second.ipynb", **override),
    ]
    with pytest.raises(ValidationError):
        CourseManifest.model_validate({"entries": entries})


@pytest.mark.parametrize(
    "override",
    [
        {"duration": 0},
        {"module": "1"},
        {"difficulty": "expert"},
        {"path": "../outside.ipynb"},
        {"path": "docs/lesson.ipynb"},
        {"unexpected": "field"},
    ],
)
def test_course_manifest_rejects_incomplete_or_invalid_metadata(
    override: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        CourseManifest.model_validate(
            {"entries": [_entry("lesson", "tracks/test/lesson.ipynb", **override)]}
        )


def test_course_manifest_rejects_legacy_has_exercises_metadata() -> None:
    entry = _entry("lesson", "tracks/test/lesson.ipynb")
    entry.pop("activity_type")
    entry["has_exercises"] = False

    with pytest.raises(ValidationError, match="activity_type|has_exercises"):
        CourseManifest.model_validate({"entries": [entry]})


@pytest.mark.parametrize(
    "activity_type",
    ["workshop", "exercise", True, 1, None],
)
def test_course_manifest_rejects_invalid_activity_type(activity_type: object) -> None:
    with pytest.raises(ValidationError, match="activity_type"):
        CourseManifest.model_validate(
            {
                "entries": [
                    _entry(
                        "lesson",
                        "tracks/test/lesson.ipynb",
                        activity_type=activity_type,
                    )
                ]
            }
        )


@pytest.mark.parametrize(
    ("learning_paths", "message"),
    [
        (
            [
                _learning_path("core", [_variant("dgx", ["first"])]),
                _learning_path("core", [_variant("brev", ["first"])]),
            ],
            "learning path IDs must be unique",
        ),
        (
            [
                _learning_path(
                    "core",
                    [
                        _variant("same", ["first"]),
                        _variant("same", ["first"]),
                    ],
                )
            ],
            "duplicate variant IDs",
        ),
        (
            [_learning_path("core", [_variant("default", ["first", "first"])])],
            "duplicate entry IDs",
        ),
        (
            [_learning_path("core", [_variant("default", ["missing"])])],
            "unknown learning-path entry IDs",
        ),
        (
            [
                _learning_path(
                    "core",
                    [_variant("default", ["dependent", "first"])],
                )
            ],
            "missing earlier all-of prerequisites",
        ),
        (
            [_learning_path("core", [_variant("default", ["choice-dependent"])])],
            "include one earlier one-of prerequisite",
        ),
    ],
)
def test_course_manifest_rejects_invalid_learning_paths(
    learning_paths: list[dict[str, object]], message: str
) -> None:
    entries = [
        _entry("first", "tracks/test/first.ipynb"),
        _entry("alternate", "tracks/test/alternate.ipynb"),
        _entry(
            "dependent",
            "tracks/test/dependent.ipynb",
            prerequisites=["first"],
        ),
        _entry(
            "choice-dependent",
            "tracks/test/choice.ipynb",
            prerequisites_any=["first", "alternate"],
        ),
    ]
    with pytest.raises(ValidationError, match=message):
        CourseManifest.model_validate({"entries": entries, "learning_paths": learning_paths})


def test_learning_path_duration_is_calculated_not_authored() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CourseManifest.model_validate(
            {
                "entries": [_entry("first", "tracks/test/first.ipynb")],
                "learning_paths": [
                    _learning_path(
                        "core",
                        [_variant("default", ["first"], calculated_duration=999)],
                    )
                ],
            }
        )


def test_shipped_manifest_identifies_independent_student_exercises(repo_root: Path) -> None:
    """Keep independent assessment labels aligned with the shipped exercise triads."""
    raw = yaml.safe_load((repo_root / "manifests/course.yaml").read_text(encoding="utf-8"))
    manifest = CourseManifest.model_validate(raw)

    student_exercises = [
        entry.id for entry in manifest.entries if entry.activity_type == "student-exercise"
    ]
    assert student_exercises == [
        "m2-sandbox-policies",
        "m4-nasa-apod",
    ]


def test_course_manifest_checks_complete_track_notebook_coverage(tmp_path: Path) -> None:
    tracks = tmp_path / "tracks" / "test"
    tracks.mkdir(parents=True)
    registered = tracks / "registered.ipynb"
    registered.write_text("{}", encoding="utf-8")

    manifest = CourseManifest.model_validate(
        {"entries": [_entry("registered", "tracks/test/registered.ipynb")]}
    )
    assert manifest.filesystem_errors(tmp_path) == []

    (tracks / "unregistered.ipynb").write_text("{}", encoding="utf-8")
    assert manifest.filesystem_errors(tmp_path) == [
        "unregistered track notebook: tracks/test/unregistered.ipynb"
    ]

    registered.unlink()
    errors = manifest.filesystem_errors(tmp_path)
    assert "registered: notebook path does not exist: tracks/test/registered.ipynb" in errors
