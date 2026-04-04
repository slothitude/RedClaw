# Show HN: RedClaw – self-learning AI agent with DNA evolution, SWE-bench results, and a Crypt

**URL:** https://github.com/slothitude/RedClaw

---

It was 2:42am. I'd been debugging the dream synthesis module for six hours. The agent had just completed its first SWE-bench run — 7/17 patches, $0 cost, on a free model. And the most interesting finding wasn't the patch rate. It was that the agent had discovered something about itself that I hadn't programmed: successful fixes used 3 tool calls. Failures used 29.

The agent was learning how it worked. That's when I knew this was worth sharing.

## The problem

Every AI coding agent forgets everything between sessions. Same prompt, same mistakes, every time. No learning, no memory of what worked. Claude Code, Aider, Cline — they're stateless. Session ends, wisdom dies.

## What RedClaw does differently

RedClaw has a subsystem called the Crypt. Every task a subagent completes gets "entombed" — a JSON record with task, type, success/failure, lessons, and tool call count. Lessons are extracted and merged into bloodline wisdom files (coder, searcher, general). The next subagent of that type inherits ALL accumulated wisdom.

After 10+ entombments, the dream cycle fires — an LLM-powered synthesis that consolidates patterns across all records. It rewrites the cross-cutting dharma document and merges into bloodline files. The agent literally dreams and wakes up smarter.

DNA traits (speed, accuracy, creativity, persistence) evolve per bloodline via weighted moving average after each task. CODER starts accuracy-heavy, SEARCHER starts speed-heavy. The traits produce concrete TraitModifiers that change timeout limits, turn caps, and prompt style.

A SOUL constitution (LEARNING > PERFORMANCE, HONESTY > OPTIMIZATION, etc.) is loaded with SHA256 integrity verification. The agent cannot start without it intact. Karma scores every action against those principles.

## SWE-bench results

| | Successes | Failures |
|---|-----------|----------|
| Avg tool calls | 3 | 29 |
| Strategy | read_file → edit_file | bash → bash → bash... |

7/17 resolvable instances patched (41%) on free GLM-5.1, 95 minutes, $0. All 7 patches were surgical — read the source, make a targeted edit, done.

Dream synthesis captured domain patterns beautifully:

> *Surgical boundary fixes outperform broad refactors: targeted edits to validation logic deliver reliable fixes without side effects.*

But it missed the meta-pattern — that tool selection strategy (3 calls vs 29) was the strongest predictor of success. This is now fixed: records summaries include tool call counts, and the synthesis prompt asks for efficiency analysis.

## Honest self-assessment

DNA traits shifted across the run (persistence maxed at 1.0, accuracy trending up) but didn't produce a measurable within-run improvement in patch rate. The model ceiling is real — GLM-5.1 is free, not frontier. The carry-forward argument is between-run, not within-run. Run 2 with pre-loaded bloodline wisdom is the real test.

The lineage: meeseeks-not-just-ralph → meeseeks-agi-system → RedClaw. Three weekends of building something that learns.

## Install

```bash
pip install -e .
python -m redclaw --provider openai --model gpt-4o
python -m redclaw --provider ollama --model llama3
python -m redclaw --agi  # autonomous mode
```

Works with OpenAI, Anthropic, Ollama, Groq, DeepSeek, OpenRouter, ZAI, or any custom endpoint. 6 interfaces: CLI, web, Telegram, Godot 4.6 GUI, dashboard, AGI.

GLM-5.1 is going open source this week. When it does, RedClaw's crypt wisdom carries forward — train on free, deploy on capable, keep the wisdom.

GitHub: https://github.com/slothitude/RedClaw

What would you put in an AI's constitution?
