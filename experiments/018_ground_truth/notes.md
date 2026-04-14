# Experiment 018: Synthetic Ground Truth — Notes

## Design

Tests two independent axes (representation × attention) against known-structure data:

| Model | Embedding  | Attention    |
|-------|-----------|-------------|
| A     | Euclidean | Dot-product |
| B     | Euclidean | Distance    |
| C     | Circular  | Dot-product |
| D     | Circular  | Distance    |

### Tasks

| # | Task | Structure | Expected Winner |
|---|------|-----------|----------------|
| 1 | Phase prediction | Circular (wrap-sensitive) | D or B |
| 2 | Circular classification | Circular (phase-only) | D |
| 3 | Periodic sequence | Circular (incommensurate freq) | D or B |
| 4 | Sparse linear regression | Linear (32-d, 3 relevant) | A or C |
| 5 | Feature selection | Linear (noise masking) | A |
| 6 | Token classification | Linear (non-periodic) | A |
| 7 | Phase scramble | Killer test (relative phase) | D (if real) |

### Metrics

- Test loss (task-appropriate: angular, MSE, cross-entropy)
- Cohen's d effect size
- Paired t-test across 5 seeds
- Win/tie/loss matrix vs baseline (Model A)

## Results

**Verdict: Case 3 — HARD FALSIFICATION. Circular geometry adds nothing.**

### Per-Task Breakdown

| Task | Type | A: Euc+DP | B: Euc+Dist | C: Circ+DP | D: Circ+Dist |
|------|------|-----------|-------------|------------|--------------|
| 1. Phase prediction | circular | -0.2435 | -0.2435 | -0.2433 | -0.2433 |
| 2. Circular class | circular | 99.84% | 99.96% | 99.96% | 99.96% |
| 3. Periodic seq | circular | -0.1034 | -0.1035 | -0.1048 | -0.1046 |
| 4. Linear reg | linear | -1.8131 | -1.8150 | -1.8129 | -1.8144 |
| 5. Feature select | linear | -0.0757 | -0.0797 | **-0.7737** | **-0.7048** |
| 6. Token class | linear | 33.71% | 32.27% | 33.09% | 34.22% |
| 7. Phase scramble | killer | -1.5843 | -1.5846 | -1.5831 | -1.5838 |

(Metric = accuracy for classification, negative RMSE for regression, negative angular error for angular tasks. Higher = better.)

### Win Matrix (vs Model A baseline)

| Task | B: Euc+Dist | C: Circ+DP | D: Circ+Dist |
|------|-------------|------------|--------------|
| 1. Phase prediction | TIE d=+0.00 | TIE d=+0.02 | TIE d=+0.02 |
| 2. Circular class | TIE d=+0.72 | TIE d=+0.72 | TIE d=+0.72 |
| 3. Periodic seq | TIE d=-0.03 | TIE d=-0.51 | TIE d=-0.48 |
| 4. Linear reg | TIE d=-0.01 | TIE d=+0.00 | TIE d=-0.00 |
| 5. Feature select | TIE d=-0.18 | **LOSS** d=-15.44 | **LOSS** d=-17.42 |
| 6. Token class | LOSS d=-0.58 | TIE d=-0.20 | TIE d=+0.24 |
| 7. Phase scramble | TIE d=-0.01 | TIE d=+0.04 | TIE d=+0.02 |

### Key findings

1. **Circular tasks (1-3): No model wins.** All four architectures perform identically on phase prediction and within noise on periodic sequence. Circular classification hits ~100% for everyone. The circular inductive bias provides zero advantage even on explicitly circular data.

2. **Linear tasks (4-6): Circular embeddings HURT.** Task 5 (feature selection) shows a catastrophic failure — circular embeddings are 10x worse (RMSE 0.77 vs 0.076). The normalization to unit circles destroys the magnitude information needed for sparse feature discrimination. This is a real, meaningful difference (Cohen's d = -15 to -17).

3. **Killer test (7): Nothing works.** Phase scrambling makes the task essentially impossible for all models (angular loss ~1.0 = random). No model uses relative phase structure.

4. **Distance attention vs dot-product: No difference.** Comparing A vs B (same embedding, different attention) — they're identical across all tasks. The distance-based attention rule provides no benefit whatsoever.

### Interpretation

This is **Case 3: Hard falsification**.

- Distance-based attention: identical to dot-product (confounding factor: with normalized inputs, cosine similarity IS the distance metric, so the two mechanisms are mathematically equivalent in practice)
- Circular embeddings: neutral on circular tasks, actively harmful on linear tasks (destroys magnitude information)
- The "Signed Wheel" framework is dead — it was already falsified in experiments 011-017 for the prime/spiral/phase claims, and this experiment confirms that even the most charitable version (circular inductive bias) provides no benefit

### Why circular embeddings hurt on task 5

The CircularEmbedding normalizes each dimension pair to unit length: `(a/r, b/r)` where `r = sqrt(a² + b²)`. This destroys magnitude information. In feature selection (task 5), only 2 of 16 features matter, and their magnitudes carry the signal. After normalization, all features look similar — the model can't distinguish signal from noise.

This is not a bug — it's the fundamental problem with forcing representations onto a circle: you throw away scale.

### Runtime

- 140 runs (4 models × 7 tasks × 5 seeds) on AMD Ryzen 16c CPU
- ~96 minutes total (~41s per run)
- 60 epochs × 2048 samples per run

## Raw Data

See `results.json` for full training curves and per-seed metrics.

## Plots

- `training_curves.png` — test loss per task, averaged over seeds
- `radar_chart.png` — relative performance normalized per task
