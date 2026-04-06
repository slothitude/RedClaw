# RedClaw × Karpathy LLM Wiki — Integration Spec

**Target repo:** `slothitude/RedClaw` (v0.3.0)  
**Source idea:** Karpathy `llm-wiki.md` gist (April 2026)  
**Date:** 2026-04-06

---

## 1. What Karpathy's Pattern Is

The pattern replaces query-time RAG with an LLM that **compiles** raw source materials into a structured, interlinked markdown wiki — then **answers questions from the wiki**, not the raw files. Key properties:

| Layer | Role |
|---|---|
| `raw/` | Immutable source material (articles, PDFs, repos, images). Human drops things in, never edits them. |
| `wiki/` | Compiled knowledge — LLM maintains this. Summaries, concept articles, entity pages, comparisons, backlinks. |
| Schema (`CLAUDE.md` / `AGENTS.md`) | Rules for how the LLM operates the wiki — folder structure, page types, citation rules, linting conventions. |
| `index.md` | Content-oriented catalog — every page with a one-line summary. LLM reads this first at query time. |
| `log.md` | Append-only chronological record of ingests, queries, lint passes. |

The magic: the LLM **accumulates** knowledge instead of rediscovering it on every query. At ~100 articles / ~400K words, it can answer complex multi-step questions that previously required full RAG pipelines.

---

## 2. Why This Fits RedClaw Perfectly

RedClaw already has nearly every infrastructure piece needed:

| Wiki requirement | RedClaw equivalent |
|---|---|
| File read/write | `read_file`, `write_file`, `edit_file` tools |
| Directory traversal | `glob_search`, `grep_search` tools |
| Web ingestion | `web_search`, `web_reader` tools |
| Persistent memory | `memory.py` (MEMORY.md / USER.md) |
| Knowledge graph | `memory_graph/` (Cognee) |
| LLM-powered synthesis | `dream.py` (Dream synthesis engine) |
| Skills system | SKILL.md plugins — wiki ops become skills |
| Subagents | Parallel ingest / lint workers |
| Session persistence | JSONL conversation history |
| CLAW.md schema discovery | Already walks up dirs for CLAW.md instructions |

The wiki **is not a new system** — it's a new operational mode that runs on top of what's already there.

---

## 3. Proposed Architecture

```
redclaw/
  wiki/                        ← NEW module
    manager.py                 WikiManager: ingest, compile, query, lint
    schema.py                  SchemaConfig: loaded from WIKI.md (or CLAW.md wiki: section)
    compiler.py                LLM-powered raw → wiki compilation
    linter.py                  Health checks: broken links, stale entries, missing backlinks
    query.py                   Index-first query engine
    types.py                   WikiPage, WikiEntry, IngestRecord dataclasses

~/.redclaw/wiki/               ← Default wiki storage root
  raw/                         Source materials (immutable)
  wiki/                        Compiled pages (LLM-maintained)
    index.md                   Table of contents
    log.md                     Append-only operation log
  WIKI.md                      Schema config (page types, citation rules, linting)

skills/wiki/                   ← Pre-built skill: wiki_ingest, wiki_query, wiki_lint
  SKILL.md
```

### New CLI flags

```
--wiki                         Enable wiki mode
--wiki-dir PATH                Wiki root (default: ~/.redclaw/wiki)
--wiki-schema PATH             Schema file (default: ~/.redclaw/wiki/WIKI.md)
--wiki-auto-ingest             Auto-ingest new files dropped into raw/ on startup
```

### New slash commands

```
/wiki ingest <url|path>        Ingest a source into raw/ and compile to wiki
/wiki query <question>         Query the wiki (index-first)
/wiki lint                     Run health check pass
/wiki status                   Show wiki stats (pages, words, last ingest, last lint)
/wiki sync                     Re-compile all stale raw sources
```

### New agent tools

| Tool | Description |
|---|---|
| `wiki_ingest` | Fetch URL or read file → write to raw/ → compile to wiki/ |
| `wiki_query` | Read index.md → identify relevant pages → synthesize answer with citations |
| `wiki_compile` | Compile one raw file into wiki/ (or all if no arg) |
| `wiki_lint` | Scan wiki/ for broken links, missing index entries, stale cross-refs |
| `wiki_log` | Append an entry to log.md |

---

## 4. How It Hooks Into Existing Systems

### 4.1 Dream Synthesis → Wiki Synthesis

`dream.py` already does LLM-powered periodic consolidation of subagent records into dharma/bloodlines. The wiki compiler uses the **same pattern**:

```python
# dream.py pattern (already exists)
async def synthesize(self, records: list[EntombedRecord]) -> str:
    prompt = build_dream_prompt(records)
    return await self.llm.complete(prompt, max_tokens=2048)

# wiki/compiler.py (new — mirrors dream.py)
async def compile_raw(self, raw_path: Path, wiki_dir: Path, schema: SchemaConfig) -> WikiPage:
    content = raw_path.read_text()
    prompt = build_compile_prompt(content, schema)
    compiled = await self.llm.complete(prompt, max_tokens=2048)
    page = parse_wiki_page(compiled)
    update_index(wiki_dir / "index.md", page)
    append_log(wiki_dir / "log.md", "INGEST", raw_path.name)
    return page
```

### 4.2 Memory System → Wiki as Extended Memory

Current memory: flat MEMORY.md snapshot injected into system prompt.  
Wiki extension: at query time, `wiki_query` reads index.md, pulls relevant pages, and **injects them into the prompt as contextual memory** — same frozen snapshot pattern, but from the wiki instead of flat MEMORY.md.

This means the wiki effectively gives RedClaw **topic-specific deep memory** without blowing the context window on every session.

### 4.3 Subagents → Parallel Wiki Workers

The subagent system (with bloodlines) can be leveraged for parallel operations:

```
/wiki sync  →  spawns N SEARCHER subagents (one per raw/ file)
              each compiles its file independently
              WikiManager merges results, deduplicates backlinks
              runs lint pass
              Dream-style synthesis consolidates cross-cutting concepts
```

This is a natural fit — SEARCHER bloodline (web tools, no bash) is exactly right for ingest work.

### 4.4 CLAW.md Schema Discovery → WIKI.md

RedClaw already walks up directories looking for `CLAW.md` to inject project-specific instructions. The wiki schema (`WIKI.md`) uses the **same discovery mechanism** — place a `WIKI.md` in any project directory and RedClaw will use it to configure the wiki for that project's domain.

Different projects get domain-specific wikis automatically.

### 4.5 Skills System → Wiki as a Skill

The entire wiki feature can ship as a **built-in skill** (`skills/wiki/SKILL.md`), making it:
- Optionally loaded (not always present in the toolset)
- Agent-manageable (the agent can update its own wiki schema)
- Discoverable via `skills_list`
- Zero impact on existing modes until loaded

---

## 5. WIKI.md Schema Format (new file type)

```yaml
---
name: wiki
description: "Personal knowledge base schema"
page_types:
  - concept: "Encyclopedia-style article. Frontmatter: title, aliases, tags, source_count."
  - summary: "One-page distillation of a single source. Frontmatter: title, source, date."
  - entity: "Named thing (person, project, company). Frontmatter: name, type, related."
  - comparison: "Side-by-side analysis. Frontmatter: title, subjects."
citation_rules: "Every factual claim links to its source page in raw/ using [text](../raw/file.md)."
ingest_workflow:
  - "Fetch content → write to raw/{topic}/{date}-{slug}.md"
  - "Compile: read raw file, write wiki/{topic}/{slug}.md with frontmatter"
  - "Update index.md: add entry under correct category"
  - "Append log.md: INGEST {timestamp} {slug}"
lint_rules:
  - "Every wiki/ page must appear in index.md"
  - "All [[wikilinks]] must resolve to existing pages"
  - "No wiki/ page older than 30 days without a health check entry in log.md"
query_behavior: "Read index.md first. Identify 3-5 most relevant pages. Read those pages. Synthesize answer with inline citations."
---

This wiki covers [your domain here]. Pages are organized under wiki/{topic}/.
```

---

## 6. Implementation Plan

### Phase 1 — Core (minimal viable wiki)
1. `redclaw/wiki/types.py` — WikiPage, SchemaConfig dataclasses
2. `redclaw/wiki/manager.py` — WikiManager with ingest/compile/query/lint/log
3. `redclaw/wiki/compiler.py` — LLM compilation (mirrors dream.py)
4. `redclaw/wiki/linter.py` — broken link + index consistency checks
5. `redclaw/wiki/query.py` — index-first query with citation injection
6. Wire `wiki_ingest`, `wiki_query`, `wiki_lint` into `redclaw/tools/registry.py`
7. Add `wiki` toolset to `toolsets.py`
8. Add `--wiki`, `--wiki-dir` CLI flags
9. Add `/wiki` slash commands to `cli.py`

### Phase 2 — Integration
10. WIKI.md schema discovery (same pattern as CLAW.md in `prompt.py`)
11. Parallel ingest via subagent workers (SEARCHER bloodline)
12. Wiki memory injection at query time (extend `prompt.py`)
13. Auto-lint trigger: after N ingests (mirrors dream synthesis trigger)

### Phase 3 — Advanced
14. `skills/wiki/SKILL.md` — package as loadable skill
15. `--wiki-auto-ingest` flag (watch raw/ directory for new files)
16. Cross-wiki backlink resolution across multiple wiki roots
17. Ephemeral mini-wiki: `/wiki focus <topic>` generates a temporary focused wiki for a session (Karpathy's "voice mode run" use case)
18. Future: fine-tune path — wiki → synthetic training data export

---

## 7. Key Design Decisions

**Why not just use the existing Cognee knowledge graph?**  
Cognee (`--knowledge`) requires a separate LLM API key, adds a heavy dependency, and isn't human-readable. The wiki is plain markdown — auditable, portable, vendor-agnostic, works with any text editor. Both can coexist: Cognee for graph traversal, wiki for human-readable compilation.

**Why WIKI.md instead of extending CLAW.md?**  
CLAW.md is agent instructions for *how to work on the project*. WIKI.md is schema for *how to organize knowledge about the domain*. They serve different masters — developer workflow vs. knowledge structure. Keeping them separate allows both to evolve independently.

**Why index-first query instead of full-text search?**  
At <400K words, the LLM can read the index (~100 entries × ~50 chars = ~5K tokens), pick the 3-5 most relevant pages, and synthesize. No embeddings, no vector database, no chunking artifacts. At larger scale, `grep_search` provides the fallback filter before index lookup. This matches Karpathy's own framing: "the LLM navigates its own wiki the way a librarian navigates a library they personally built."

**DNA / Karma alignment for wiki operations?**  
Wiki compile and lint operations get entombed as GENERAL bloodline subagent records. Over time, the dream synthesis will surface patterns about which compilation strategies work best for which domains — feeding back into improved WIKI.md schema rules. Karma tracks whether wiki operations align with SOUL principles (UNDERSTANDING > MIMICRY is directly applicable: does the compiled page demonstrate understanding or just regurgitate the source?).

---

## 8. Minimal Viable Diff (where to start)

The smallest possible useful change: **add `wiki_ingest` and `wiki_query` as two new tools** in `redclaw/tools/search.py`, backed by a dead-simple WikiManager that reads/writes from `~/.redclaw/wiki/`. No schema, no linter, no subagents. Just:

```python
# tools/search.py additions
async def wiki_ingest(url_or_path: str, topic: str = "general") -> str:
    """Fetch content, write to raw/, compile summary to wiki/, update index."""
    ...

async def wiki_query(question: str) -> str:
    """Read wiki/index.md, find relevant pages, synthesize answer."""
    ...
```

Ship that. Get it working. Grow from there.

---

## 9. Comparison Table (updated README section)

```markdown
| Feature                    | RedClaw | Claude Code | Aider/Cline |
|----------------------------|---------|-------------|-------------|
| Self-learning (DNA)        | Yes     | No          | No          |
| Subagent bloodlines        | Yes     | Limited     | No          |
| Dream synthesis            | Yes     | No          | No          |
| Constitutional SOUL        | Yes     | No          | No          |
| Karma self-evaluation      | Yes     | No          | No          |
| **LLM Wiki (compiling KB)**| **Yes** | **No**      | **No**      |
| **Persistent knowledge**   | **Yes** | **No**      | **No**      |
| Provider-agnostic          | Yes     | Anthropic   | Limited     |
| Interfaces                 | 6       | CLI only    | IDE plugin  |
| Autonomous goals           | Yes     | No          | No          |
```

---

*Generated from Karpathy llm-wiki.md gist + RedClaw v0.3.0 CLAUDE.md architecture analysis.*
