# Experiment Plan: LoRA versus full fine-tuning on SST-2

Illustration only. The agents have not run, there are no results and the researcher has not accepted this plan. It follows `hypotheses.ml.example.md`.

## 1. Question and decision rule

Does LoRA fine-tuning of a pre-trained encoder reach validation accuracy within a researcher-stated margin of full fine-tuning on SST-2 (H1), and does it use less GPU time (H2)?

H1 is concluded only if the lower confidence bound of the accuracy difference exceeds the margin. Missing the bound means non-inferiority is not shown; it does not show that LoRA is worse. H2 is concluded only if the confidence interval of the GPU-time ratio excludes 1.

## 2. Experiment E1: comparative benchmark

- Dataset: SST-2 from the GLUE collection. Revision, license and split sizes: needs verification against the dataset card before launch.
- Methods: LoRA and full fine-tuning of the same pre-trained encoder (name: Proposal — confirm before launch).
- Baselines: full fine-tuning is the primary baseline. A second published baseline is needed and is not yet chosen.
- Splits: use the official train split for training, a held-out part of train for tuning, and the validation split only for reporting. Never tune on the reported split.
- Seeds: at least three, the same seeds for both methods.
- Compute: equal GPU-hour budget per run; record hardware type and library versions.
- Contamination: record whether the encoder pre-training data may include SST-2 and report it as a limitation.

## 3. Prespecified analysis

Paired difference in accuracy over seeds with a percentile bootstrap interval, and the ratio of mean GPU hours. Report all seeds. A negative or inconclusive result is a valid outcome and is reported as such.

## 4. Limitations

One encoder and one dataset. Sample size is the seed count and gives low power; no power claim is made here.
