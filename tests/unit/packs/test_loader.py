"""Protect layer order, fallback and merge behavior consumed by methodology packs."""

import pytest

from experiment_design.contracts import Profile, WorkflowError
from experiment_design.packs import load_pack


def layer(root, name, text):
    directory = root / name
    directory.mkdir()
    (directory / "pack.yaml").write_text(text, encoding="utf-8")


def test_base_is_always_loaded(tmp_path):
    layer(tmp_path, "base", "min_baselines: 2\n")
    pack = load_pack(None, root=tmp_path)
    assert pack.layers == ("base",)
    assert pack.notes == ()


def test_unknown_overlay_reports_fallback(tmp_path):
    layer(tmp_path, "base", "{}")
    pack = load_pack(Profile(domain="robotics"), root=tmp_path)
    assert pack.layers == ("base",)
    assert len(pack.notes) == 1 and "robotics" in pack.notes[0]


def test_overlay_merges_and_overrides_in_order(tmp_path):
    layer(
        tmp_path,
        "base",
        """
min_baselines: 2
role_addenda: {design: General}
calculators: [independent_pooled_t]
validators: [seed_policy]
rubric: [{category: statistics, check: Original}]
""",
    )
    layer(
        tmp_path,
        "ml",
        """
min_baselines: 4
role_addenda: {design: ML}
calculators: [paired_t]
validators: [seed_policy, split_overlap]
rubric: [{category: statistics, check: Updated}]
""",
    )
    layer(tmp_path, "ablation", "role_addenda: {design: Ablation}\n")
    (tmp_path / "ablation" / "template.md").write_text("# Ablation\n", encoding="utf-8")
    pack = load_pack(Profile(domain="ml", research_type="ablation"), root=tmp_path)
    assert pack.layers == ("base", "ml", "ablation")
    assert pack.min_baselines == 4
    assert pack.role_addenda["design"] == "General\n\nML\n\nAblation"
    assert pack.calculators == {"independent_pooled_t", "paired_t"}
    assert pack.validators == ("seed_policy", "split_overlap")
    assert [(item.category, item.check) for item in pack.rubric] == [("statistics", "Updated")]
    assert pack.template == "# Ablation\n"


@pytest.mark.parametrize("text", ["[invalid", "min_baselines: -1", "unknown_field: true"])
def test_invalid_pack_is_workflow_error(tmp_path, text):
    layer(tmp_path, "base", text)
    with pytest.raises(WorkflowError, match="pack"):
        load_pack(None, root=tmp_path)


def test_overlay_name_cannot_escape_pack_root(tmp_path):
    layer(tmp_path, "base", "{}")
    assert load_pack(Profile(domain="../outside"), root=tmp_path).layers == ("base",)


def test_start_run_persists_selected_profile_and_old_runs_default_to_none(tmp_path):
    from experiment_design.config import ExdConfig, LLMConfig
    from experiment_design.contracts import RunState
    from experiment_design.persistence.store import RunStore
    from experiment_design.workflow.engine import start_run

    source = tmp_path / "hypotheses.md"
    source.write_text("## H1: Claim\n", encoding="utf-8")
    cfg = ExdConfig(
        llm=LLMConfig(base_url="http://fake", model="fake"),
        runs_root=tmp_path / "runs",
        profile=Profile(domain="ml"),
    )
    store = start_run([source], cfg)
    assert RunStore(store.path).state.profile == Profile(domain="ml")
    assert RunState(run_id="old").profile is None
    legacy = start_run([source], ExdConfig(llm=cfg.llm, runs_root=cfg.runs_root))
    assert legacy.state.profile is None
