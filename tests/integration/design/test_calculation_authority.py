"""Calculator availability is evidence, not a verdict on scientific design validity."""

import pytest

from experiment_design.agents.tools import ToolContext, build_tools
from experiment_design.persistence.store import Store
from tests.support.scenarios import (
    source_context,
    source_snapshot,
    supported_design,
)


def test_unsupported_power_still_rejects_forged_source_override(tmp_path):
    store = Store(tmp_path / "source-guard.db")
    try:
        run = store.create_staged_run(source_snapshot(), {})
        context = store.save_context(run, source_context())
        draft = supported_design()
        draft.analysis.method_id = "clustered_noninferiority_mixed_model"
        draft.analysis.model = "Mixed model with class random intercept"
        version = store.add_version(run, draft, author="design", parent=None)
        tool = build_tools(ToolContext(store, run, context.package, version, set()))[
            "compute_power"
        ]
        with pytest.raises(ValueError, match="source span does not support"):
            tool.handler(
                {
                    "overrides": {
                        "sd": {
                            "value": 99,
                            "unit": "points",
                            "provenance": "source",
                            "source_ref": "S1",
                            "source_span": "Standard deviation 10 points",
                        }
                    }
                }
            )
        assert store.version(run).design.scenarios[0].sd.value == 10
    finally:
        store.close()
