"""Strict schema and repository checks for ``manifests/course.yaml``."""

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    StringConstraints,
    computed_field,
    field_validator,
    model_validator,
)

Slug = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
]


class CourseEntry(BaseModel):
    """One notebook and its learner-facing course metadata."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: Slug
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    path: str
    track: Slug
    module: Annotated[int, Field(ge=1, le=99)]
    duration: Annotated[int, Field(gt=0, le=480)]
    prerequisites: list[Slug]
    prerequisites_any: list[Slug] = Field(default_factory=list)
    skills: Annotated[list[Slug], Field(min_length=1)]
    activity_type: Literal["concept", "guided-lab", "student-exercise", "reference"]
    difficulty: Literal["beginner", "intermediate", "advanced"]

    @field_validator("path")
    @classmethod
    def _validate_notebook_path(cls, value: str) -> str:
        if not value or value != value.strip():
            raise ValueError("path must be a non-empty, trimmed repository-relative path")
        if "\\" in value:
            raise ValueError("path must use POSIX '/' separators")

        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("path must not be absolute or traverse outside the repository")
        if not path.parts or path.parts[0] != "tracks":
            raise ValueError("path must be located under tracks/")
        if path.suffix != ".ipynb":
            raise ValueError("path must reference an .ipynb notebook")
        return path.as_posix()

    @model_validator(mode="after")
    def _validate_entry_lists(self) -> "CourseEntry":
        if len(self.prerequisites) != len(set(self.prerequisites)):
            raise ValueError(f"entry {self.id!r} contains duplicate prerequisites")
        if len(self.prerequisites_any) != len(set(self.prerequisites_any)):
            raise ValueError(f"entry {self.id!r} contains duplicate alternate prerequisites")
        if self.id in {*self.prerequisites, *self.prerequisites_any}:
            raise ValueError(f"entry {self.id!r} cannot depend on itself")
        overlap = sorted(set(self.prerequisites) & set(self.prerequisites_any))
        if overlap:
            raise ValueError(
                f"entry {self.id!r} repeats prerequisites in all-of and one-of groups: "
                + ", ".join(overlap)
            )
        if len(self.skills) != len(set(self.skills)):
            raise ValueError(f"entry {self.id!r} contains duplicate skills")
        return self


class LearningPathVariant(BaseModel):
    """One ordered route through a curated learning path."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: Slug
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    entry_ids: Annotated[list[Slug], Field(min_length=1)]

    _calculated_duration: int = PrivateAttr(default=0)

    @model_validator(mode="after")
    def _validate_entry_ids(self) -> "LearningPathVariant":
        duplicates = sorted(
            entry_id for entry_id, count in Counter(self.entry_ids).items() if count > 1
        )
        if duplicates:
            raise ValueError(
                f"learning-path variant {self.id!r} contains duplicate entry IDs: "
                + ", ".join(duplicates)
            )
        return self

    @computed_field(return_type=int)
    @property
    def calculated_duration(self) -> int:
        """Total minutes for this variant, populated from the course entries."""
        return self._calculated_duration


class LearningPath(BaseModel):
    """A named learning path with one or more platform or audience variants."""

    model_config = ConfigDict(extra="forbid", strict=True)

    id: Slug
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    variants: Annotated[list[LearningPathVariant], Field(min_length=1)]

    @model_validator(mode="after")
    def _validate_variants(self) -> "LearningPath":
        duplicates = sorted(
            variant_id
            for variant_id, count in Counter(variant.id for variant in self.variants).items()
            if count > 1
        )
        if duplicates:
            raise ValueError(
                f"learning path {self.id!r} contains duplicate variant IDs: "
                + ", ".join(duplicates)
            )
        return self


class CourseManifest(BaseModel):
    """Course graph plus filesystem coverage checks for every track notebook."""

    model_config = ConfigDict(extra="forbid", strict=True)

    entries: Annotated[list[CourseEntry], Field(min_length=1)]
    learning_paths: list[LearningPath] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_graph(self) -> "CourseManifest":
        ids = [entry.id for entry in self.entries]
        duplicate_ids = sorted(entry_id for entry_id, count in Counter(ids).items() if count > 1)
        if duplicate_ids:
            raise ValueError("course entry IDs must be unique: " + ", ".join(duplicate_ids))

        paths = [entry.path for entry in self.entries]
        duplicate_paths = sorted(path for path, count in Counter(paths).items() if count > 1)
        if duplicate_paths:
            raise ValueError("course entry paths must be unique: " + ", ".join(duplicate_paths))

        by_id = {entry.id: entry for entry in self.entries}
        unknown = sorted(
            {
                prerequisite
                for entry in self.entries
                for prerequisite in [*entry.prerequisites, *entry.prerequisites_any]
                if prerequisite not in by_id
            }
        )
        if unknown:
            raise ValueError("unknown prerequisite IDs: " + ", ".join(unknown))

        later_module_edges = sorted(
            f"{entry.id} -> {prerequisite}"
            for entry in self.entries
            for prerequisite in [*entry.prerequisites, *entry.prerequisites_any]
            if by_id[prerequisite].module > entry.module
        )
        if later_module_edges:
            raise ValueError(
                "prerequisites cannot point to a later module: " + ", ".join(later_module_edges)
            )

        indegree = {
            entry.id: len(entry.prerequisites) + len(entry.prerequisites_any)
            for entry in self.entries
        }
        dependants: dict[str, list[str]] = {entry.id: [] for entry in self.entries}
        for entry in self.entries:
            for prerequisite in [*entry.prerequisites, *entry.prerequisites_any]:
                dependants[prerequisite].append(entry.id)

        ready = [entry_id for entry_id, count in indegree.items() if count == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for dependant in dependants[current]:
                indegree[dependant] -= 1
                if indegree[dependant] == 0:
                    ready.append(dependant)

        if visited != len(self.entries):
            cycle_members = sorted(entry_id for entry_id, count in indegree.items() if count > 0)
            raise ValueError(
                "course prerequisite graph contains a cycle involving: " + ", ".join(cycle_members)
            )

        duplicate_learning_path_ids = sorted(
            path_id
            for path_id, count in Counter(path.id for path in self.learning_paths).items()
            if count > 1
        )
        if duplicate_learning_path_ids:
            raise ValueError(
                "learning path IDs must be unique: " + ", ".join(duplicate_learning_path_ids)
            )

        unknown_path_entries = sorted(
            {
                entry_id
                for path in self.learning_paths
                for variant in path.variants
                for entry_id in variant.entry_ids
                if entry_id not in by_id
            }
        )
        if unknown_path_entries:
            raise ValueError("unknown learning-path entry IDs: " + ", ".join(unknown_path_entries))

        for path in self.learning_paths:
            for variant in path.variants:
                earlier_entries: set[str] = set()
                for entry_id in variant.entry_ids:
                    entry = by_id[entry_id]
                    missing_all = sorted(
                        prerequisite
                        for prerequisite in entry.prerequisites
                        if prerequisite not in earlier_entries
                    )
                    if missing_all:
                        raise ValueError(
                            f"learning path {path.id!r} variant {variant.id!r} is not "
                            f"prerequisite-closed for {entry_id!r}; missing earlier all-of "
                            "prerequisites: " + ", ".join(missing_all)
                        )

                    if entry.prerequisites_any and not earlier_entries.intersection(
                        entry.prerequisites_any
                    ):
                        raise ValueError(
                            f"learning path {path.id!r} variant {variant.id!r} is not "
                            f"prerequisite-closed for {entry_id!r}; include one earlier "
                            "one-of prerequisite from: " + ", ".join(entry.prerequisites_any)
                        )
                    earlier_entries.add(entry_id)

                variant._calculated_duration = sum(
                    by_id[entry_id].duration for entry_id in variant.entry_ids
                )
        return self

    def filesystem_errors(self, repo_root: Path) -> list[str]:
        """Return missing, unsafe, or unregistered notebook-path errors."""
        repo_root = repo_root.resolve()
        errors: list[str] = []
        registered_paths = {entry.path for entry in self.entries}

        for entry in self.entries:
            target = (repo_root / entry.path).resolve()
            if not target.is_relative_to(repo_root):
                errors.append(f"{entry.id}: path resolves outside repository: {entry.path}")
            elif not target.is_file():
                errors.append(f"{entry.id}: notebook path does not exist: {entry.path}")

        tracks_root = repo_root / "tracks"
        discovered_paths = (
            {
                notebook.relative_to(repo_root).as_posix()
                for notebook in tracks_root.rglob("*.ipynb")
                if notebook.is_file()
            }
            if tracks_root.is_dir()
            else set()
        )

        for path in sorted(discovered_paths - registered_paths):
            errors.append(f"unregistered track notebook: {path}")
        for path in sorted(registered_paths - discovered_paths):
            if (repo_root / path).is_file():
                errors.append(f"registered notebook is outside track discovery: {path}")

        return errors
