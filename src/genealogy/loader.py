"""Dataset loader for a genealogy workspace root."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from genealogy.schema import (
    _YAML_SAFE_LOADER,
    IndividualFrontmatter,
    PlaceFrontmatter,
    Relationship,
    RelationshipConstraint,
    RelationshipsFile,
    SourceFrontmatter,
    read_markdown_frontmatter,
)


@dataclass
class LoadedIndividual:
    path: Path
    data: IndividualFrontmatter
    body: str


@dataclass
class LoadedSource:
    path: Path
    data: SourceFrontmatter
    body: str


@dataclass
class LoadedPlace:
    path: Path
    data: PlaceFrontmatter
    body: str


@dataclass
class LoadError:
    path: Path
    message: str


@dataclass
class Dataset:
    """In-memory representation of a genealogy workspace."""

    root: Path
    individuals: dict[str, LoadedIndividual] = field(default_factory=dict)
    sources: dict[str, LoadedSource] = field(default_factory=dict)
    places: dict[str, LoadedPlace] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    constraints: list[RelationshipConstraint] = field(default_factory=list)
    load_errors: list[LoadError] = field(default_factory=list)
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def focal_person_id(self) -> str | None:
        fp = self.config.get("focal_person")
        return fp.strip() if isinstance(fp, str) and fp.strip() else None

    @property
    def artifacts_dir(self) -> Path:
        env_dir = os.environ.get("GENEALOGY_ARTIFACTS_DIR")
        if env_dir:
            p = Path(env_dir).expanduser()
            return p.resolve() if p.is_absolute() else (self.root / p).resolve()
        cfg_dir = self.config.get("artifacts_dir")
        if isinstance(cfg_dir, str) and cfg_dir.strip():
            p = Path(cfg_dir.strip()).expanduser()
            return p.resolve() if p.is_absolute() else (self.root / p).resolve()
        return (self.root / "artifacts").resolve()

    def resolve_artifact_path(self, artifact_path: str) -> Path:
        """Resolve a logical artifact path (e.g. 'artifacts/foo.png') against artifacts_dir."""
        norm = artifact_path.replace("\\", "/").lstrip("./")
        if norm.startswith("artifacts/"):
            rel = norm[len("artifacts/") :]
            return (self.artifacts_dir / rel).resolve()
        p = Path(artifact_path).expanduser()
        return p.resolve() if p.is_absolute() else (self.root / p).resolve()

    def logical_artifact_path(self, file_path: Path) -> str:
        """Return canonical logical 'artifacts/<rel>' path for a file inside artifacts_dir."""
        rel = file_path.resolve().relative_to(self.artifacts_dir)
        return f"artifacts/{str(rel).replace(os.sep, '/')}"


def load_workspace_config(root: Path) -> dict[str, Any]:
    """Load optional `<root>/.genealogy.yaml` configuration."""
    cfg_path = root / ".genealogy.yaml"
    if not cfg_path.exists():
        return {}
    try:
        data = yaml.load(cfg_path.read_text(encoding="utf-8"), Loader=_YAML_SAFE_LOADER)
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def load_dataset(root: Path | str) -> Dataset:
    """Load all individuals, sources, and relationships under `<root>/data/`.

    Captures schema/parse errors in `dataset.load_errors` instead of aborting early.
    """
    root_path = Path(root).resolve()
    ds = Dataset(root=root_path, config=load_workspace_config(root_path))

    ind_dir = ds.data_dir / "individuals"
    if ind_dir.exists():
        for md_path in sorted(ind_dir.glob("*.md")):
            try:
                raw_meta, body = read_markdown_frontmatter(md_path)
                model = IndividualFrontmatter.model_validate(raw_meta)
                if model.id in ds.individuals:
                    ds.load_errors.append(
                        LoadError(md_path, f"Duplicate individual ID '{model.id}'")
                    )
                    continue
                if md_path.stem != model.id:
                    ds.load_errors.append(
                        LoadError(
                            md_path,
                            f"Filename '{md_path.name}' does not match frontmatter id '{model.id}'",
                        )
                    )
                ds.individuals[model.id] = LoadedIndividual(path=md_path, data=model, body=body)
            except (ValueError, yaml.YAMLError, ValidationError) as exc:
                ds.load_errors.append(LoadError(md_path, str(exc)))

    src_dir = ds.data_dir / "sources"
    if src_dir.exists():
        for md_path in sorted(src_dir.glob("*.md")):
            try:
                raw_meta, body = read_markdown_frontmatter(md_path)
                model = SourceFrontmatter.model_validate(raw_meta)
                if model.id in ds.sources:
                    ds.load_errors.append(
                        LoadError(md_path, f"Duplicate source ID '{model.id}'")
                    )
                    continue
                if md_path.stem != model.id:
                    ds.load_errors.append(
                        LoadError(
                            md_path,
                            f"Filename '{md_path.name}' does not match frontmatter id '{model.id}'",
                        )
                    )
                ds.sources[model.id] = LoadedSource(path=md_path, data=model, body=body)
            except (ValueError, yaml.YAMLError, ValidationError) as exc:
                ds.load_errors.append(LoadError(md_path, str(exc)))

    places_dir = ds.data_dir / "places"
    if places_dir.exists():
        for md_path in sorted(places_dir.glob("*.md")):
            try:
                raw_meta, body = read_markdown_frontmatter(md_path)
                model = PlaceFrontmatter.model_validate(raw_meta)
                if model.id in ds.places:
                    ds.load_errors.append(
                        LoadError(md_path, f"Duplicate place ID '{model.id}'")
                    )
                    continue
                if md_path.stem != model.id:
                    ds.load_errors.append(
                        LoadError(
                            md_path,
                            f"Filename '{md_path.name}' does not match frontmatter id '{model.id}'",
                        )
                    )
                ds.places[model.id] = LoadedPlace(path=md_path, data=model, body=body)
            except (ValueError, yaml.YAMLError, ValidationError) as exc:
                ds.load_errors.append(LoadError(md_path, str(exc)))

    rel_path = ds.data_dir / "relationships.yaml"
    if rel_path.exists():
        text = rel_path.read_text(encoding="utf-8").strip()
        if text:
            try:
                raw_rel = yaml.load(text, Loader=_YAML_SAFE_LOADER)
                if raw_rel is None:
                    raw_rel = {"relationships": []}
                parsed = RelationshipsFile.model_validate(raw_rel)
                ds.relationships = parsed.relationships
                ds.constraints = parsed.constraints
            except (ValueError, yaml.YAMLError, ValidationError) as exc:
                ds.load_errors.append(LoadError(rel_path, str(exc)))

    return ds
