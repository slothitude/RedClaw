# RedClaw + free GLM-5.1: 35% SWE-bench patch rate, 95 minutes, $0. Here's what the AGI loop learned.

## TL;DR

| Metric | Value |
|--------|-------|
| Instances | 20 (SWE-bench Lite, seed=42) |
| Patched | 7 (35%) |
| Failed | 10 (no patch, timeout, or wrong fix) |
| Clone failures | 3 (Windows long path, exit 128) |
| Total time | 95 min |
| Cost | $0 (ZAI free tier) |
| Model | GLM-5.1 via ZAI (free) |

## The result that matters

7/20 patched. Not headline-grabbing. But the interesting thing isn't the number — it's *what the agent learned about itself while running*.

## The surgical vs. brute-force finding

This was the key discovery, and the agent's dream synthesis didn't catch it (more on that below).

| | Successes | Failures |
|---|-----------|----------|
| Avg tool calls | 3 | 29 |
| Avg time | 174s | 360s |
| Strategy | read file → edit file → done | bash → bash → bash → ... (29 loops) |

Successful patches followed a clean pattern:
1. `read_file` on the relevant source
2. `edit_file` with a targeted fix
3. Done

Failed attempts spiraled through bash command after bash command, trying to compile, test, and debug their way to a fix they never found. The agent never stepped back to just *read the code*.

This is a meta-pattern about *tool selection strategy*, not domain knowledge. And it's exactly the kind of thing an agent should learn from its own execution traces.

## What dream synthesis encoded

The AGI loop ran 3 dream synthesis passes during the 95-minute run (after 10+ entombments accumulated). Here's what the crypt produced:

**Dharma (cross-cutting patterns):**

> *Derived State Invalidation is the Dominant Failure Mode*: When identity or configuration mutates, downstream artifacts stale silently. Explicitly propagate mutations or force regeneration of dependent state.
>
> *Boundary Sanitization Remains Systemically Incomplete*: Validators across frameworks consistently fail to enforce strict boundaries. Probe leading/trailing whitespace, empty strings, unicode edges before attempting complex fixes.
>
> *Serialization Desyncs Leak Platform Assumptions*: Pickling/unpickling bakes environment-specific defaults into state that doesn't restore identically.

**Coder bloodline wisdom:**

> - *Surgical boundary fixes outperform broad refactors*: targeted edits to validation logic deliver reliable fixes without side effects.
> - *Identity fields must flow through all derived computations*: any identity mutation requires tracing every downstream artifact.

**DNA evolution (generation 54):**

| Trait | Value |
|-------|-------|
| Speed | 0.41 |
| Accuracy | 0.63 |
| Creativity | 0.22 |
| Persistence | 1.00 |

Persistence maxed out (the agent never gives up), accuracy trending up, creativity low (appropriate for deterministic bug-fixing).

## What dream synthesis missed

The dharma and bloodline output is *domain-specific* — it learned about Django validators, serialization edge cases, and boundary sanitization. Good stuff for fixing Django bugs.

But it completely missed the meta-pattern: **the agent's own tool selection strategy was the strongest predictor of success**. Records were summarized as:

```
- [coder] OK: Fix username validators
- [coder] FAIL: Fix autoreload module
```

The `tool_calls` field existed in the entombed records but wasn't included in the dream synthesis prompt. So the LLM had no way to see that successes used 3 calls and failures used 29.

This is now fixed. The records summary fed to dream synthesis includes `(N calls)` per record, and the synthesis prompt explicitly asks for efficiency pattern analysis.

## The carry-forward argument

Here's the part that matters beyond this single run:

**Crypt wisdom persists across model upgrades.**

The bloodline wisdom, dharma, and DNA traits are stored in `~/.redclaw/crypt/` as plain markdown and JSON. They're injected into the system prompt at session start. When you switch from GLM-5.1 to Claude Sonnet, the new model inherits everything the old model learned.

The agent's "personality" — its accumulated experience about what works and what doesn't — is decoupled from any specific LLM. You can:
- Train on a free model
- Deploy on a capable model
- Keep the wisdom

This is the core value prop of RedClaw's AGI loop. Not that it achieves high scores on benchmarks, but that it *accumulates transferable experience* that makes every future run better regardless of the underlying model.

## The patches

For reference, the 7 successful patches:

1. **django-11099** — Regex anchors `^...$` → `\A...\Z` in username validators (176s)
2. **django-14382** — Trailing slash strip in template directory validation (174s)
3. **django-12915** — Add `get_response_async` to StaticFilesHandlerMixin (186s)
4. **django-11422** — Handle modules without `__spec__` in autoreload (489s)
5. **django-17087** — Use `__qualname__` instead of `__name__` in serializer (220s)
6. **django-11133** — Handle `memoryview` in `HttpResponse.make_bytes` (290s)
7. **matplotlib-25498** — Call `autoscale_None()` before `_draw_all` in colorbar (445s)

All surgical, targeted edits. The agent read the relevant file, identified the specific line, and made a minimal fix.

## Next steps

1. **Re-run with fixed dream synthesis** — the efficiency meta-pattern should now be captured in bloodline wisdom, potentially shifting the agent away from bash brute-force on subsequent runs
2. **Counterfactual injection** — newly added post-run analysis injects "what should have happened" into entombed records, giving dream synthesis richer signal
3. **Controlled comparison** — same 20 instances, same model, but with accumulated crypt wisdom from this run

## Try it

```bash
pip install -e .
python -m redclaw --agi --provider zai --model glm-5.1
```

The crypt accumulates from the first run. By run 3, your agent has domain-specific wisdom about your codebase.

GitHub: https://github.com/slothitude/RedClaw
