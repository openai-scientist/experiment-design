from experiment_design.config import RunConfig
from experiment_design.persistence.store import RunStore
from experiment_design.workflow.engine import advance
from tests.integration.review.test_markdown_workflow import happy_outputs
from tests.support.providers import Judge, Provider

SENTENCE = "one plan that tests every hypothesis"


def test_framing_and_design_ask_for_one_plan_over_every_hypothesis(tmp_path):
    source = tmp_path / "hypothesis.md"
    source.write_text(
        "## H1: first claim\n\n## H2: second claim\n\n## H3: third claim\n", encoding="utf-8"
    )
    store = RunStore.create(tmp_path / "runs", [source], {"run": RunConfig().model_dump()}, {})
    provider = Provider(happy_outputs())
    advance(store, provider, Judge(), RunConfig())
    for invocation in (0, 1):  # framing, design
        system = provider.inputs[provider.starts[invocation]][0]["content"]
        assert SENTENCE in system
        assert "hypothesis_id" in system
    workspace = provider.documents(0)
    text = "\n".join(workspace.values())
    assert all(f"## H{n}" in text for n in (1, 2, 3))
