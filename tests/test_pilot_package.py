import re
from pathlib import Path

import pytest
from conftest import design

from experiment_design.contracts import Claim, ClaimField, Quantity
from experiment_design.package import intake_gaps, load_package
from experiment_design.patch import package_claim
from experiment_design.science.power import compute_scenario


def test_pilot_case_has_tension():
    package = load_package(Path(__file__).parents[1] / "hypothesis.example.md")
    assert "estimand" in intake_gaps(package)
    proposed = design()
    proposed.claim = Claim(
        **{
            key: ClaimField(value=value, origin="package" if value is not None else "missing")
            for key, value in package_claim(package).items()
        }
    )
    assumptions = {a.id: a.quantity for a in package.assumptions}
    proposed.sampling.cluster.size = assumptions["A1"]
    proposed.sampling.cluster.icc = assumptions["A2"]
    proposed.sampling.cluster.clusters_per_arm = 20
    source = package.sources[0]
    sd = re.search(r"standard deviation.*?about (\d+) points", source.text)
    assert sd is not None
    proposed.scenarios[0].sd = Quantity(
        value=float(sd.group(1)),
        unit=package.outcome.unit,
        provenance="source",
        source_ref=source.id,
    )
    proposed.analysis.alpha = 0.025
    proposed.analysis.sidedness = "one_sided"
    result = compute_scenario(proposed, proposed.scenarios[0])
    assert result.power == pytest.approx(0.730, abs=0.005)
    assert result.required_clusters_per_arm == 24
    assert result.required_n_per_arm == 600
