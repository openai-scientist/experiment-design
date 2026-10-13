from experiment_design.agents.benchmark.models import Resource, ResourcesDoc
from experiment_design.agents.benchmark.registry import registry_entries
from experiment_design.contracts import Profile
from experiment_design.packs import load_pack


def test_registry_is_scoped_to_loaded_domain():
    ml = registry_entries(load_pack(Profile(domain="ml")))
    se = registry_entries(load_pack(Profile(domain="se")))
    assert "SST-2" in {entry.name for entry in ml}
    assert "Defects4J" not in {entry.name for entry in ml}
    assert {entry.name for entry in se} >= {
        "SWE-bench",
        "Defects4J",
        "HumanEval",
        "CodeXGLUE",
        "BigCloneBench",
    }
    assert all(entry.source_url.startswith("https://") for entry in (*ml, *se))


def test_resource_document_preserves_unknown_and_cited_statuses():
    resource = Resource(
        resource_id="r",
        kind="baseline",
        name="Baseline",
        origin="registry",
        source_url="https://example.test/paper",
        status="cited",
    )
    doc = ResourcesDoc(
        resources=[resource], minimums={"benchmark": 1, "baseline": 2}, shortfalls=[]
    )
    restored = ResourcesDoc.model_validate_json(doc.model_dump_json(by_alias=True))
    assert restored.resources[0].status == "cited"
    assert restored.resources[0].revision is None
