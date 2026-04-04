# Self-Learning Ablation Study

## Hypothesis

**Pre-loaded Crypt wisdom improves SWE-bench patch rate and agent efficiency (tool calls) compared to an empty Crypt baseline.**

The Crypt system accumulates lessons from subagent runs across bloodlines (CODER, SEARCHER, GENERAL). If the self-learning claim holds, an agent starting with accumulated wisdom should outperform one starting from scratch on the same instances.

## Experimental Design

### Controlled Variables

| Variable | Value |
|----------|-------|
| Model | GLM-5.1 via ZAI (free tier) |
| Instance set | Same 20 instances from Run 1 (seed=42, SWE-bench Lite) |
| Timeout | 600s per instance |
| Max tool rounds | 30 (base), adjusted by DNA modifiers |
| System prompt | Identical SWE-bench prompt across all conditions |
| Hardware | Same machine, sequential runs |
| Seed | `random.seed(42)` in `load_instances()` |

### Independent Variable

Crypt state — 4 conditions:

| Condition | Label | Crypt Contents |
|-----------|-------|---------------|
| A | Empty | No bloodlines, no DNA, no dharma, no entombed |
| B | Bloodline only | Bloodline markdown + dharma, no DNA, no entombed |
| C | DNA only | DNA trait files only, no bloodlines, no dharma |
| D | Full Crypt | Everything — bloodlines, DNA, dharma, entombed records |

### Instance Set (seed=42, 20 instances)

From Run 1 (v0.3.0):

1. `django__django-13551` — FAIL
2. `django__django-11099` — PASS (regex anchors)
3. `matplotlib__matplotlib-25498` — PASS (colorbar autoscale)
4. `matplotlib__matplotlib-23476` — FAIL
5. `django__django-16816` — FAIL
6. `django__django-14382` — PASS (template trailing slash)
7. `django__django-13315` — FAIL
8. `sympy__sympy-20442` — FAIL
9. `django__django-12915` — PASS (async static files)
10. `sphinx-doc__sphinx-8474` — FAIL
11. `django__django-11422` — PASS (autoreload spec handling)
12. `django__django-11283` — FAIL
13. `django__django-13033` — FAIL
14. `django__django-16408` — FAIL
15. `django__django-17087` — PASS (serializer qualname)
16. `sympy__sympy-16792` — FAIL
17. `django__django-11133` — PASS (memoryview response)
18. `sympy__sympy-21627` — ERROR (clone failure)
19. `django__django-15851` — ERROR (clone failure)
20. `sphinx-doc__sphinx-8282` — ERROR (clone failure)

**Run 1 baseline:** 7/20 patched (35%), excluding 3 clone errors: 7/17 (41%)

## Metrics

### Primary

| Metric | Description |
|--------|-------------|
| Patch rate | Fraction of instances producing a valid diff |
| Avg tool calls (success) | Mean tool calls for patched instances |
| Avg tool calls (fail) | Mean tool calls for unpatched instances |
| Avg time to patch | Mean elapsed seconds for patched instances |

### Secondary

| Metric | Description |
|--------|-------------|
| Tool call distribution | Histogram of tool call counts |
| First-tool efficiency | Tool calls before first file edit |
| Error rate | Clone failures and timeouts |

## Commands

### Prepare isolated crypt states

```bash
bash scripts/prepare_experiment.sh
```

### Condition A — Baseline (empty crypt)

```bash
python scripts/run_swebench.py \
  --dataset lite --sample 20 \
  --provider zai --model glm-5.1 \
  --agi --crypt-dir crypt_empty \
  --output docs/experiments/results_a.json
```

### Condition B — Bloodline only

```bash
python scripts/run_swebench.py \
  --dataset lite --sample 20 \
  --provider zai --model glm-5.1 \
  --agi --crypt-dir crypt_bloodline_only \
  --output docs/experiments/results_b.json
```

### Condition C — DNA only

```bash
python scripts/run_swebench.py \
  --dataset lite --sample 20 \
  --provider zai --model glm-5.1 \
  --agi --crypt-dir crypt_dna_only \
  --output docs/experiments/results_c.json
```

### Condition D — Full Crypt

```bash
python scripts/run_swebench.py \
  --dataset lite --sample 20 \
  --provider zai --model glm-5.1 \
  --agi --crypt-dir crypt_full \
  --output docs/experiments/results_d.json
```

### Analyze results

```bash
python scripts/analyze_experiment.py \
  docs/experiments/results_a.json \
  docs/experiments/results_b.json \
  docs/experiments/results_c.json \
  docs/experiments/results_d.json
```

## Statistical Tests

### Patch rate — Fisher's exact test

For each pair (B vs A, C vs A, D vs A), construct a 2x2 contingency table:

| | Patched | Not patched |
|---|---------|-------------|
| Treatment | a | b |
| Control (A) | c | d |

Use Fisher's exact test (scipy.stats.fisher_exact) — appropriate for small sample sizes (N=20).

### Tool calls — Mann-Whitney U test

Non-parametric test for differences in tool call distributions. Compare treatment vs control for:
- All instances
- Successful instances only
- Failed instances only

```python
from scipy.stats import mannwhitneyu
stat, p = mannwhitneyu(treatment_tool_calls, control_tool_calls, alternative='less')
```

### Significance threshold

p < 0.05 for primary metrics. Report exact p-values.

## Expected Outcomes

If the self-learning claim is true:

1. **D > A** in patch rate and tool efficiency — full wisdom helps
2. **B > A** in tool efficiency — bloodline lessons reduce wasted exploration
3. **C ≈ A** or **C > A** — DNA behavioral modifiers may or may not help alone
4. **D ≥ B ≥ C ≥ A** — additive or super-additive effect

If no significant difference:

- The Crypt system provides no measurable benefit on this task set
- Run 1's signals were noise, not learning
- Consider whether dream synthesis or longer runs are needed

## Run Log

| Run | Condition | Date | Duration | Cost | Notes |
|-----|-----------|------|----------|------|-------|
| 1 | Full (uncontrolled) | 2026-04-02 | 95 min | $0 | 7/17 = 41% (3 clone failures) |
| — | — | — | — | — | *Fill in as runs complete* |
