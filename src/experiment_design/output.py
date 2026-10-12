"""Public output boundary: active plans and isolated historical exports."""

from pathlib import Path

from experiment_design.contracts import PublicationSnapshot
from experiment_design.experiment_plan import export_plan, render_plan
from experiment_design.experiment_plan import render_decision as render_decision
from experiment_design.runtime.store import Store


def render_markdown(snapshot, *args, **kwargs) -> str:
    if (
        isinstance(snapshot, PublicationSnapshot)
        and snapshot.run.get("plan_contract") == "exp-plan/v1"
    ):
        return render_plan(snapshot)
    from experiment_design.legacy_output import render_markdown as render_historical

    return render_historical(snapshot, *args, **kwargs)


def export(store: Store, run_id: str, out_dir: Path, version: int | None = None) -> dict[str, Path]:
    if store.run(run_id).plan_contract == "exp-plan/v1":
        return export_plan(store, run_id, Path(out_dir), version)
    from experiment_design.legacy_output import export as export_historical

    return export_historical(store, run_id, Path(out_dir), version)
