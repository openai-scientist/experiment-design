"""Independent reference generator. Run: uv run --with statsmodels tests/fixtures/stats_reference.py

Never import experiment_design. Paired power uses statsmodels; Wilcoxon/McNemar
also use seeded Monte Carlo tests, rather than the production planning formulas.
Bootstrap uses SciPy's documented percentile method; Holm uses statsmodels.
"""

import hashlib
import json
import math
import platform
from pathlib import Path

import numpy as np
import scipy
import statsmodels
from scipy import stats
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.power import TTestPower


def quantity(value):
    return {"value": value, "provenance": "assumption", "unit": ""}


def generate():
    cases = []
    power = TTestPower()
    for effect, count in ((0.5, 20), (0.3, 40)):
        required = math.ceil(power.solve_power(effect_size=effect, alpha=0.05, power=0.8))
        cases.append(
            {
                "name": f"paired-{effect}",
                "request": {
                    "calculator_id": "paired_t",
                    "effect": quantity(effect),
                    "sd": quantity(1),
                    "n_pairs": count,
                    "target_power": 0.8,
                    "alpha": 0.05,
                    "sidedness": "two_sided",
                },
                "expected": {
                    "power": float(power.power(effect, count, 0.05)),
                    "required_n_per_arm": required,
                },
            }
        )
    for effect, seed in ((0.5, 1729), (0.35, 1730)):
        paired_n = math.ceil(power.solve_power(effect_size=effect, alpha=0.05, power=0.8))
        required = math.ceil(paired_n / (3 / math.pi))
        values = np.random.default_rng(seed).normal(effect, 1, size=(20000, required))
        observed = float(np.mean(stats.wilcoxon(values, axis=1, method="approx").pvalue <= 0.05))
        cases.append(
            {
                "name": f"wilcoxon-{effect}",
                "request": {
                    "calculator_id": "wilcoxon_signed_rank",
                    "effect": quantity(effect),
                    "sd": quantity(1),
                    "target_power": 0.8,
                    "alpha": 0.05,
                    "sidedness": "two_sided",
                },
                "expected": {"required_n_per_arm": required},
                "monte_carlo_power": observed,
                "simulations": 20000,
                "seed": seed,
            }
        )
    for p10, p01, count, seed in ((0.15, 0.05, 200, 1731), (0.25, 0.1, 100, 1732)):
        counts = np.random.default_rng(seed).multinomial(count, [p10, p01, 1 - p10 - p01], 20000)
        discordant = counts[:, 0] + counts[:, 1]
        statistic = np.divide(
            (counts[:, 0] - counts[:, 1]) ** 2,
            discordant,
            out=np.zeros(20000),
            where=discordant > 0,
        )
        observed = float(np.mean(stats.chi2.sf(statistic, 1) <= 0.05))
        cases.append(
            {
                "name": f"mcnemar-{p10}",
                "request": {
                    "calculator_id": "mcnemar",
                    "p_discordant_10": quantity(p10),
                    "p_discordant_01": quantity(p01),
                    "n_pairs": count,
                    "alpha": 0.05,
                    "sidedness": "two_sided",
                },
                "expected": {"power": observed},
                "tolerance": 0.02,
                "simulations": 20000,
                "seed": seed,
            }
        )
    for p_values in ([0.01, 0.04, 0.03], [0.2, 0.001, 0.08, 0.049]):
        cases.append(
            {
                "name": f"holm-{len(p_values)}",
                "request": {"calculator_id": "holm", "p_values": p_values},
                "expected": {"adjusted": multipletests(p_values, method="holm")[1].tolist()},
            }
        )
    for probability in (0.56, 0.71):
        cases.append(
            {
                "name": f"a12-{probability}",
                "request": {"calculator_id": "a12", "a12": quantity(probability)},
                "normalized": {"d": float(math.sqrt(2) * stats.norm.ppf(probability))},
            }
        )
    for delta in (0.12, 0.42):
        cases.append(
            {
                "name": f"cliff-{delta}",
                "request": {"calculator_id": "cliffs_delta", "cliffs_delta": quantity(delta)},
                "normalized": {"a12": (delta + 1) / 2},
            }
        )
    for values, seed in (([1, 2, 3, 4, 7], 1733), ([2, 2, 3, 8, 9, 10], 1734)):
        ci = stats.bootstrap(
            (np.asarray(values),),
            np.mean,
            n_resamples=20000,
            method="percentile",
            rng=np.random.default_rng(seed),
        ).confidence_interval
        cases.append(
            {
                "name": f"bootstrap-{seed}",
                "request": {
                    "calculator_id": "bootstrap_ci",
                    "values": values,
                    "confidence": 0.95,
                    "resamples": 20000,
                    "random_seed": seed,
                },
                "expected": {"ci_low": float(ci.low), "ci_high": float(ci.high)},
            }
        )
    return {
        "provenance": {
            "command": "uv run --with statsmodels tests/fixtures/stats_reference.py",
            "python": platform.python_version(),
            "scipy": scipy.__version__,
            "statsmodels": statsmodels.__version__,
            "numpy": np.__version__,
            "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "sources": [
                "https://www.statsmodels.org/stable/generated/statsmodels.stats.power.TTestPower.html",
                "https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.bootstrap.html",
            ],
        },
        "cases": cases,
    }


if __name__ == "__main__":
    Path(__file__).with_suffix(".json").write_text(
        json.dumps(generate(), indent=2) + "\n", encoding="utf-8"
    )
