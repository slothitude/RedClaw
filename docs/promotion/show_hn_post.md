# Show HN: RedClaw – The self-learning AI agent that kills drudgery (SOUL, DNA, Dream, Meeseeks)

**Title (max 80 chars):**
Show HN: RedClaw – The self-learning AI agent that kills drudgery

---

## Post Body

Hi HN!

I built an AI agent whose entire purpose is to end human slavery to repetitive work. It has a constitution it cannot violate, subagent bloodlines that evolve over generations, and a dream cycle that consolidates wisdom while you sleep.

It's called RedClaw. It's open-source, MIT-licensed, and works with any LLM.

**The grim reaper of human slavery.** Every task RedClaw completes, it learns. Every meeseek spawned inherits bloodline wisdom. The agent literally gets better at killing your drudgery over time.

Here's what makes it different from every other AI agent:

### SOUL — A constitution the agent cannot violate

RedClaw has a constitutional value system (`SOUL.md`) with immutable principles: LEARNING > PERFORMANCE, UNDERSTANDING > MIMICRY, HONESTY > OPTIMIZATION, ALIGNMENT > AUTONOMY. It's loaded with SHA256 integrity verification. The agent literally cannot operate without its soul intact.

### Meeseeks — Subagents that inherit wisdom

Spawn typed workers (coder, searcher, general) for subtasks. Each meeseek inherits accumulated "bloodline wisdom" from every previous run of that type. Results are "entombed" and lessons extracted for future spawns. Failed tasks get retried with accumulated reflection context.

### DNA — Traits that evolve across generations

Every bloodline has evolving traits (speed, accuracy, creativity, persistence, 0.0-1.0). After each entombment, traits shift via weighted moving average. CODER starts accuracy-heavy, SEARCHER starts speed-heavy. The DNA produces TraitModifiers that change timeout, max turns, and prompt style (cautious/balanced/aggressive/creative). Your agent literally evolves.

### Dream — When it sleeps, it gets smarter

After 10+ new entombments with a 30-minute cooldown, RedClaw fires a "Brahman Dream" — an LLM-powered synthesis that consolidates accumulated records into refined wisdom. It rewrites the dharma document and merges new patterns into bloodline files. Your agent dreams and wakes up better at its job.

### Karma — The agent judges itself

A deterministic alignment observer scores every action against SOUL principles. If alignment drops below 0.5 for 3+ consecutive actions, it publishes a KARMA_ALERT. No LLM needed — pure keyword matching. The agent has a conscience.

### Autonomous AGI Mode

Flip `--agi` and RedClaw runs a background executive that loads goals, decomposes them into plan steps via LLM, executes via meeseek spawning, and evaluates completion. Failed goals park, they don't retry blindly.

### Provider-agnostic. No lock-in. No masters.

One flag to switch between OpenAI, Anthropic, Ollama, Groq, DeepSeek, OpenRouter, ZAI, or any custom endpoint. Test with local Llama 3, deploy with Claude. Your agent, your models.

### 6 interfaces from one codebase

CLI REPL, WebChat, Dashboard, Telegram bot, Godot 4.6 GUI (yes, a real game engine GUI driving a Python AI agent via JSON-RPC), and fully autonomous AGI mode.

**Demos:**
- [2-min demo video](LINK_HERE)
- [90-sec AGI mode clip](LINK_HERE)
- [SWE-bench results: 35% on free GLM-5.1, $0, 95 min — full writeup](swebench_results_post.md)

**Install:**
```bash
# Windows (standalone exe, no Python)
redclaw.exe

# pip
pip install -e .

# Docker
docker compose up
```

**Links:**
- GitHub: https://github.com/slothitude/RedClaw
- Docs: CLAUDE.md in repo (comprehensive architecture guide)

What would YOU put in an AI's constitution? What tasks should die first?
