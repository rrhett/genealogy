"""Dataset loader for a genealogy workspace root."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import ValidationError

from genealogy.schema import (
    IndividualFrontmatter,
    Relationship,
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
class LoadError:
    path: Path
    message: str


@dataclass
class Dataset:
    """In-memory representation of a genealogy workspace."""

    root: Path
    individuals: dict[str, LoadedIndividual] = field(default_factory=dict)
    sources: dict[str, LoadedSource] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    load_errors: list[LoadError] = field(default_factory=list)

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"


def load_dataset(root: Path | str) -> Dataset:
    """Load all individuals, sources, and relationships under `<root>/data/`.

    Captures schema/parse errors in `dataset.load_errors` instead of aborting early.
    """
    root_path = Path(root).resolve()
    ds = Dataset(root=root_path)

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

    rel_path = ds.data_dir / "relationships.yaml"
    if rel_path.exists():
        text = rel_path.read_text(encoding="utf-8").strip()
        if text:
            try:
                raw_rel = yaml.safe_load(text)
                if raw_rel is None:
                    raw_rel = {"relationships": []}
                parsed = RelationshipsFile.model_validate(raw_rel)
                ds.relationships = parsed.relationships
            except (ValueError, yaml.YAMLError, ValidationError) as exc:
                ds.load_errors.append(LoadError(rel_path, str(exc)))

    return ds
