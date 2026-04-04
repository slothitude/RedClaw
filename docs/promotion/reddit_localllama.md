# I ran SWE-bench on free GLM-5.1 using my self-learning agent — 41% patch rate, $0, 95 min, and the agent was evolving in real time

**Subreddit:** r/LocalLLaMA

---

Hey r/LocalLLaMA,

I ran my self-learning coding agent (RedClaw) against 20 SWE-bench Lite instances using free GLM-5.1 via ZAI. Results:

| Instance | Time | Result |
|---|---|---|
| django-11099 (username regex anchors) | 176s | Patched |
| django-14382 (trailing slash in validate_name) | 174s | Patched |
| django-12915 (async static files handler) | 186s | Patched |
| django-17087 (qualname serialization) | 220s | Patched |
| django-11133 (memoryview handling) | 290s | Patched |
| django-11422 (autoreload module spec) | 489s | Patched |
| matplotlib-25498 (colorbar update) | 445s | Patched |

**7/17 resolvable instances (41%)**, $0 cost, 95 minutes total. 3 instances failed at git clone (Windows long path issue — not an agent failure).

## The interesting finding

| | Successes | Failures |
|---|-----------|----------|
| Avg tool calls | 3 | 29 |
| Strategy | read_file → edit_file | bash brute force loops |

Successful patches followed a clean 3-step pattern: read the relevant source file, make a targeted edit, done. Failures spiraled through bash command after bash command (averaging 29 calls), never stopping to just read the code.

## The self-learning part

RedClaw has a "Crypt" system:
- **Entombment** — every subagent run gets recorded with task, success, lessons, tool call count
- **Bloodline wisdom** — per-type files (coder.md, searcher.md, general.md) with structured sections
- **Dream synthesis** — after 10+ entombments, an LLM consolidation pass refines the wisdom
- **DNA traits** — speed/accuracy/creativity/persistence evolve per bloodline

During this run, 3 dream synthesis passes fired. The agent produced dharma like:

> *Surgical boundary fixes outperform broad refactors: targeted edits to validation logic deliver reliable fixes without side effects.*

It caught domain patterns but missed the tool-efficiency meta-pattern (3 vs 29 calls). That's now fixed — the synthesis prompt asks for efficiency analysis.

The bloodline wisdom from this run persists. Next run starts smarter.

## Works with any local model

```bash
redclaw --provider ollama --model llama3
redclaw --provider ollama --model deepseek-coder-v2
redclaw --provider ollama --model qwen2.5-coder:7b
redclaw --provider openai --base-url http://localhost:1234/v1
```

MIT licensed, Python 3.11+, pip install or standalone exe.

GitHub: https://github.com/slothitude/RedClaw

Full writeup: docs/promotion/swebench_results_post.md

What local models are you running for coding tasks? I'd love to see how the bloodline system adapts to different model strengths.
