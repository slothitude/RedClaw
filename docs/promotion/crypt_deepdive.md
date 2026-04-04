# How RedClaw's Crypt turns every coding task into permanent wisdom

Most AI coding agents are stateless. You ask them to fix a bug, they fix it (or don't), and the session ends. Next session, same agent, same mistakes. No learning, no memory, no improvement.

RedClaw's Crypt system is an attempt to fix that. Here's how it works, with actual code from the codebase.

## The problem: agents forget everything

When you use Claude Code or Aider to fix a bug, the agent might learn something in that session — "oh, this project uses pytest, not unittest" — but that knowledge dies when the session closes. The next time you ask it to fix a similar bug, it starts from zero.

This isn't a prompt engineering problem. It's a data persistence problem. The agent needs a mechanism to accumulate and transfer experience across sessions.

## Entombment: recording what happened

Every time a subagent completes a task in RedClaw, the result gets "entombed" — a JSON record capturing what happened:

```python
# redclaw/crypt/crypt.py — entomb()
record: dict[str, Any] = {
    "id": sub_id,
    "task": task[:500],
    "type": subagent_type.value,
    "success": result.success,
    "output_preview": (result.output or "")[:300],
    "error": result.error,
    "tool_calls": result.tool_calls,
    "lessons": [{"text": l.text, "category": l.category} for l in lessons],
    "timestamp": datetime.now(timezone.utc).isoformat(),
}

# Save entombed record
record_path = self.entombed_dir / f"{sub_id}.json"
record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
```

The `tool_calls` field is critical — it records how many tool invocations the subagent used. This turned out to be the strongest predictor of success in our SWE-bench run.

## Bloodlines: typed wisdom per agent role

RedClaw has three subagent types — CODER, SEARCHER, and GENERAL. Each has its own bloodline wisdom file with structured sections:

```python
# redclaw/crypt/crypt.py — _init_bloodline()
content = (
    f"# {subagent_type.value.title()} Bloodline Wisdom\n"
    "\n"
    "## Successful Patterns\n"
    "\n"
    "## Warnings\n"
    "\n"
    "## Tool Insights\n"
)
```

After entombment, lessons are extracted from the result and merged into the bloodline file:

```python
# Update bloodline with lessons
self._init_bloodline(subagent_type)
for lesson in lessons:
    self.update_bloodline(subagent_type, lesson.text, lesson.category)
```

The deduplication is important — near-duplicate lessons are skipped to prevent wisdom bloat:

```python
# Dedup: check if this lesson (or a near-duplicate) already exists
lesson_lower = lesson.lower().strip()
existing_lines = content.split("\n")
for line in existing_lines:
    stripped = line.lstrip("- *").strip().lower()
    if stripped and (stripped == lesson_lower or stripped in lesson_lower or lesson_lower in stripped):
        return  # skip duplicate
```

When a new subagent spawns, it inherits the full accumulated wisdom of its bloodline. A CODER meeseek spawned for task #50 carries lessons from tasks #1–49.

## Dream synthesis: periodic consolidation

After 10+ new entombments with a 30-minute cooldown, RedClaw fires the Brahman Dream — an LLM-powered synthesis pass. It reads all new entombed records, identifies cross-cutting patterns, and rewrites the dharma document (shared wisdom across all bloodlines):

```python
# redclaw/crypt/dream.py — triggers
_MIN_ENTOMBMENTS = 10
_COOLDOWN_SECONDS = 1800  # 30 minutes
```

The dream synthesis prompt asks the LLM to:
1. Identify domain patterns from success/failure records
2. Generate bloodline-specific updates
3. Compare tool call counts between successes and failures (efficiency patterns)
4. Produce cross-cutting dharma insights

This is a simplified version of what human sleep does for learning — consolidating scattered experiences into refined patterns.

In our SWE-bench run, dream synthesis produced dharma like:

> *Derived State Invalidation is the Dominant Failure Mode*: When identity or configuration mutates, downstream artifacts stale silently.

> *Surgical boundary fixes outperform broad refactors*: targeted edits to validation logic deliver reliable fixes without side effects.

## DNA evolution: traits that change behavior

Each bloodline has evolving traits (speed, accuracy, creativity, persistence) on a 0.0–1.0 scale. After each entombment, traits shift via weighted moving average (alpha=0.3):

```python
# redclaw/crypt/dna.py — evolve()
alpha = 0.3

# Speed: increase if low tool calls + high success, decrease if timeouts
speed_signal = (1.0 - min(avg_tool_calls / 8.0, 1.0)) * success_rate - timeout_rate * 0.5
current.speed = current.speed * (1 - alpha) + max(0.0, speed_signal) * alpha

# Accuracy: increase on success, decrease on failure
current.accuracy = current.accuracy * (1 - alpha) + success_rate * alpha

# Persistence: increase when tasks succeed despite retries
current.persistence = current.persistence * (1 - alpha) + (1.0 - timeout_rate) * alpha
```

These traits produce concrete runtime parameters:

```python
@dataclass
class TraitModifiers:
    timeout_multiplier: float = 1.0     # 0.7 - 1.3 based on persistence
    max_turns_modifier: int = 0         # -2 to +2 based on speed
    prompt_style: str = "balanced"      # cautious/balanced/aggressive/creative
```

CODER starts accuracy-heavy (0.8), SEARCHER starts speed-heavy (0.8). After 50 tasks, the traits reflect actual performance — not assumptions.

## The SWE-bench surgical pattern

In our SWE-bench Lite run (20 instances, free GLM-5.1), the agent discovered something we didn't program:

| | Successes (7) | Failures (10) |
|---|-----------|----------|
| Avg tool calls | 3 | 29 |
| Strategy | read→edit | bash loops |

All 7 successful patches followed: `read_file` the source → `edit_file` a targeted fix → done. Failures spiraled through bash commands (avg 29 calls), never reading the actual code.

Dream synthesis caught the domain patterns but missed this meta-pattern — the records summary wasn't including `tool_calls`. This is now fixed with the efficiency analysis instruction in the dream synthesis prompt.

## What happens on run 2

Here's the key: crypt wisdom persists across runs. The bloodline files, dharma, and DNA traits live in `~/.redclaw/crypt/` as plain markdown and JSON. When you start a new session — even with a different model — the accumulated wisdom is injected into the system prompt.

A run 2 on the same SWE-bench instances with pre-loaded bloodline wisdom would start with the surgical pattern already encoded:

> *Successful Patterns: Use read_file → edit_file for targeted fixes. Avoid bash brute force.*

If the average tool calls drop on previously-failed instances, that's evidence the bloodline is working. That's a measurable, reproducible improvement from accumulated experience.

## The carry-forward argument

Crypt wisdom is decoupled from any specific LLM. You can:
- Train on a free model (GLM-5.1, local Llama)
- Switch to a frontier model (Claude, GPT-4)
- Keep the accumulated wisdom

The agent's "experience" — what works, what fails, which strategies succeed — transfers across model upgrades. This is the core value of the Crypt: not achieving high benchmark scores, but accumulating transferable experience that makes every future run better.

---

RedClaw is MIT licensed, Python 3.11+, works with any LLM provider.

GitHub: https://github.com/slothitude/RedClaw

Key Crypt files:
- `redclaw/crypt/crypt.py` — Bloodline management, entombment
- `redclaw/crypt/extractor.py` — Lesson extraction from results
- `redclaw/crypt/dna.py` — Trait evolution per bloodline
- `redclaw/crypt/dream.py` — Dream synthesis consolidation
- `redclaw/crypt/metrics.py` — Aggregate tracking
