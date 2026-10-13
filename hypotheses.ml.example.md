# Hypotheses

**Topic:** Parameter-efficient fine-tuning compared with full fine-tuning for text classification on a public benchmark

## H1: LoRA fine-tuning reaches accuracy within a stated margin of full fine-tuning on SST-2.

- **Exposure:** Fine-tuning a pre-trained encoder with LoRA adapters, with full fine-tuning of the same encoder as the control.
- **Outcome:** Accuracy on the SST-2 validation split.
- **Estimand:** Difference in mean accuracy over random seeds, LoRA minus full fine-tuning.
- **Method:** At least three seeds per method, equal compute budget, hyperparameters tuned on a held-out split only; paired comparison over seeds.
- **Prediction:** The lower confidence bound of the accuracy difference is above a non-inferiority margin the researcher must state.
- **Falsified if:** The lower bound is at or below the margin.
- **Rationale:** Training fewer parameters may keep most of the accuracy at lower cost.
- **Limitations:**
  - One encoder and one dataset limit generality.

## H2: LoRA fine-tuning uses less GPU time than full fine-tuning at equal accuracy.

- **Exposure:** Same as H1.
- **Outcome:** GPU hours to reach the best validation accuracy of each run.
- **Estimand:** Ratio of mean GPU hours, LoRA over full fine-tuning.
- **Method:** Same runs as H1; time measured on the same hardware type.
- **Prediction:** The ratio is below 1 with a 95% confidence interval that excludes 1.
- **Falsified if:** The ratio is 1 or higher, or its interval includes 1.
- **Rationale:** Fewer trainable parameters reduce optimizer and gradient memory.
- **Limitations:**
  - Wall-clock time depends on hardware and implementation.

## Scope
One public text-classification benchmark, one pre-trained encoder, at least three seeds.

## Sources
### S1: Researcher note (DRAFT)
DRAFT — the researcher must name the encoder, the dataset revision and the compute budget before the plan is scored.
