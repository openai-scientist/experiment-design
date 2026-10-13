"""Validated methodology content, layered without domain logic in workflow."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, ValidationError

from experiment_design.contracts import Profile, Record, WorkflowError


class RubricItem(Record):
    category: str
    check: str


class RegistryEntry(Record):
    name: str
    kind: Literal["dataset", "benchmark", "baseline", "metric"]
    tasks: list[str] = Field(default_factory=list)
    source_url: str
    hf_repo_id: str | None = None
    license: str | None = None


class _Layer(Record):
    role_addenda: dict[str, str] = Field(default_factory=dict)
    rubric: list[RubricItem] = Field(default_factory=list)
    validators: list[str] = Field(default_factory=list)
    calculators: list[str] = Field(default_factory=list)
    registry: list[RegistryEntry] = Field(default_factory=list)
    template: str = ""
    min_benchmarks: int | None = Field(default=None, ge=0)
    min_baselines: int | None = Field(default=None, ge=0)


@dataclass(frozen=True)
class Pack:
    layers: tuple[str, ...]
    notes: tuple[str, ...]
    role_addenda: dict[str, str]
    rubric: tuple[RubricItem, ...]
    validators: tuple[str, ...]
    calculators: frozenset[str]
    registry: tuple[RegistryEntry, ...]
    template: str
    min_benchmarks: int
    min_baselines: int


def load_pack(profile: Profile | None, *, root: Path | None = None) -> Pack:
    root = root or Path(__file__).parent
    requested = ["base"]
    if profile:
        requested.extend(name for name in (profile.domain, profile.research_type) if name)
    layers, notes = [], []
    addenda: dict[str, str] = {}
    rubric: dict[str, RubricItem] = {}
    registry: dict[tuple[str, str], RegistryEntry] = {}
    validators: dict[str, None] = {}
    calculators: set[str] = set()
    template, min_benchmarks, min_baselines = "", 1, 2
    for name in dict.fromkeys(requested):
        valid_name = re.fullmatch(r"[a-z][a-z0-9_]*", name)
        path = root / name / "pack.yaml" if valid_name else None
        if path is None or not path.is_file():
            if name == "base":
                raise WorkflowError("base pack is missing")
            notes.append(f"overlay '{name}' not available; running at base")
            continue
        try:
            layer = _Layer.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
            registry_path = path.with_name("registry.yaml")
            if registry_path.is_file():
                entries = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
                layer.registry.extend(RegistryEntry.model_validate(entry) for entry in entries)
            template_path = path.with_name("template.md")
            if template_path.is_file():
                layer.template = template_path.read_text(encoding="utf-8")
        except (OSError, yaml.YAMLError, ValidationError, TypeError) as exc:
            raise WorkflowError(f"Invalid pack '{name}': {exc}") from exc
        layers.append(name)
        for role, text in layer.role_addenda.items():
            addenda[role] = "\n\n".join(part for part in (addenda.get(role, ""), text) if part)
        rubric.update((item.category, item) for item in layer.rubric)
        registry.update(((item.kind, item.name.casefold()), item) for item in layer.registry)
        validators.update(dict.fromkeys(layer.validators))
        calculators.update(layer.calculators)
        if layer.template:
            template = layer.template
        if layer.min_benchmarks is not None:
            min_benchmarks = layer.min_benchmarks
        if layer.min_baselines is not None:
            min_baselines = layer.min_baselines
    return Pack(
        tuple(layers),
        tuple(notes),
        addenda,
        tuple(rubric.values()),
        tuple(validators),
        frozenset(calculators),
        tuple(registry.values()),
        template,
        min_benchmarks,
        min_baselines,
    )
