import pytest

from experiment_design.config import RunConfig
from experiment_design.contracts import AgentOutput, Profile
from experiment_design.packs.infer import infer_research_type
from tests.integration.review.test_markdown_workflow import store_for
from tests.support.providers import Scripted


@pytest.mark.parametrize(
    "choice", ["comparative_benchmark", "controlled_experiment", "ablation", "unclear"]
)
def test_stage_model_infers_type_without_jev_and_records_usage(tmp_path, choice):
    store = store_for(tmp_path)
    store.update(profile=Profile(domain="se"))
    provider = Scripted([[("submit", {"research_type": choice})]])
    inferred = infer_research_type(store, provider, RunConfig(), AgentOutput(markdown="Brief"))
    assert inferred == (None if choice == "unclear" else choice)
    assert store.state.profile.research_type == inferred
    assert store.state.jev_calls == 0
    assert store.state.llm_tokens == 5
    event = [e for e in store.events() if e["kind"] == "activity"][-1]
    assert event["data"]["type"] == "exd.profile.inferred"
    assert event["data"]["actor"] == "stage"


def test_confirmed_type_and_legacy_profile_do_not_call_provider(tmp_path):
    store = store_for(tmp_path)
    provider = Scripted([])
    assert infer_research_type(store, provider, RunConfig(), AgentOutput(markdown="Brief")) is None
    store.update(profile=Profile(research_type="ablation"))
    assert (
        infer_research_type(store, provider, RunConfig(), AgentOutput(markdown="Brief"))
        == "ablation"
    )
    assert not provider.inputs
