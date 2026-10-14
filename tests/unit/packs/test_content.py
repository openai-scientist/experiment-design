"""Pack content must compose into usable methodology and registered tools."""

import pytest

from experiment_design.contracts import Profile
from experiment_design.packs import load_pack


@pytest.mark.parametrize("domain", [None, "ml", "se"])
@pytest.mark.parametrize(
    "research_type",
    [
        None,
        "comparative_benchmark",
        "controlled_experiment",
        "ablation",
    ],
)
def test_supported_profiles_keep_common_scientific_rubric(domain, research_type):
    pack = load_pack(Profile(domain=domain, research_type=research_type))
    assert not pack.notes
    assert {item.category for item in pack.rubric} >= {
        "leakage",
        "baseline",
        "control",
        "statistics",
        "reproducibility",
    }
    assert pack.min_benchmarks == 1 and pack.min_baselines == 2
    assert {"independent_pooled_t", "cluster_equal_size_t"} <= pack.calculators
    if research_type:
        assert pack.template.startswith("# ")
    if domain:
        assert {"design", "methodologist", "skeptic"} <= pack.role_addenda.keys()


def test_pack_validators_are_registered():
    validators = pytest.importorskip("experiment_design.packs.validators")
    for domain in (None, "ml", "se"):
        assert set(load_pack(Profile(domain=domain)).validators) <= validators.VALIDATORS.keys()


def test_pack_calculators_are_registered():
    registry = pytest.importorskip("experiment_design.science.registry")
    for research_type in (None, "comparative_benchmark", "controlled_experiment", "ablation"):
        assert (
            load_pack(Profile(research_type=research_type)).calculators
            <= registry.CALCULATORS.keys()
        )
