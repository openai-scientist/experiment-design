# Independent reference calculations using R stats, unrelated to Python production.
cases <- list(
  independent51 = list(n=51, delta=.05, sd=.1, alpha=.05, sidedness="one.sided"),
  cluster20 = list(n=20, delta=3, sd=10*sqrt(.1+.9/25), alpha=.025, sidedness="one.sided"),
  cluster24 = list(n=24, delta=3, sd=10*sqrt(.1+.9/25), alpha=.025, sidedness="one.sided"),
  reversed20 = list(n=20, delta=3, sd=10*sqrt(.1+.9/25), alpha=.025, sidedness="one.sided"),
  superiority64 = list(n=64, delta=.5, sd=1, alpha=.05, sidedness="two.sided")
)
reference_power <- function(x) {
  power.t.test(n=x$n, delta=x$delta, sd=x$sd, sig.level=x$alpha,
               type="two.sample", alternative=x$sidedness, strict=TRUE)$power
}
