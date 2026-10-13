"""Public output boundary: active plans and isolated historical exports."""

from pathlib import Path

from experiment_design.contracts import PublicationSnapshot
from experiment_design.persistence.store import Store
from experiment_design.publication.plan import export_plan, render_plan
from experiment_design.publication.plan import render_decision as render_decision


def render_markdown(snapshot, *args, **kwargs) -> str:
    if (
        isinstance(snapshot, PublicationSnapshot)
        and snapshot.run.get("plan_contract") == "exp-plan/v1"
    ):
        return render_plan(snapshot)
    from experiment_design.publication.history import render_markdown as render_historical

    return render_historical(snapshot, *args, **kwargs)


def export(store: Store, run_id: str, out_dir: Path, version: int | None = None) -> dict[str, Path]:
    if store.run(run_id).plan_contract == "exp-plan/v1":
        return export_plan(store, run_id, Path(out_dir), version)
    from experiment_design.publication.history import export as export_historical

    return export_historical(store, run_id, Path(out_dir), version)
