# Supported planning calculations

All production statistics are calculated in `science/power.py`. New runs require
an explicit analysis method and declared assumptions. Missing inputs or an
unsupported analysis return `not_computed` with reasons.

`independent_pooled_t` supports normal continuous outcomes, independent assigned
units, equal arm allocation and common variance. The SAP model must be
`Pooled two-sample t test`. Non-inferiority uses the signed null margin and a
one-sided test; superiority can use one or two sides.

`cluster_equal_size_t` supports a pooled t test of independent cluster means,
fixed equal cluster sizes, equal allocation and common variance. The SAP model
must be `Pooled t test of cluster means`, with cluster-level assignment and
analysis. Cluster mean SD is individual SD times `sqrt(ICC + (1-ICC)/size)`.
The degrees of freedom are `2*K-2`, where K is clusters per arm. Unequal or
informative sizes, mixed models, covariate adjustment and Welch inference are
unsupported. Selecting this calculator does not authorize changing the SAP.

Critical values use central Student t; power uses noncentral t probabilities.
Integer recruitment search recomputes degrees of freedom at each candidate.
Planning power under assumed inputs is not empirical evidence about those inputs.
Observed confidence intervals and reference-validated MDE/precision are unavailable;
the EDP describes the planned SAP confidence procedure.

Independent fixtures in `tests/fixtures/power_reference.json` were generated
before the Python implementation using R 4.6.0 stats through WebR. The generator
is `pilot/references/power_reference.R`; its SHA-256 is stored with the fixtures.
WebR is a one-time reference runtime, not a production dependency. Its Windows
worker loader required converting a local filesystem path to a file URL; the
R runtime, stats functions and numerical expressions were unchanged.

References: [R stats power.t.test](https://www.stat.ethz.ch/R-manual/R-devel/library/stats/html/power.t.test.html),
[SciPy noncentral t](https://docs.scipy.org/doc/scipy/reference/generated/scipy.stats.nct.html),
[WebR runtime](https://docs.r-wasm.org/webr/latest/downloading.html).
The published [PASS non-inferiority example](https://www.ncss.com/wp-content/themes/ncss/pdf/Procedures/PASS/Non-Inferiority_Tests_for_Two_Means_in_a_Cluster-Randomized_Design.pdf)
rounds power to .8059 at n=51, SD=.1, margin=.05 and alpha=.05 with cluster size 1.
It checks independent inference; it does not validate general cluster NI methods.

Historical normal approximations remain readable for schema-1 outputs and retain
their original meaning. They are not relabeled as validated t calculations.
