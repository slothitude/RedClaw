---
name: swebench
description: SWE-bench issue resolution — surgical patch strategy
---

# SWE-bench Resolution Strategy

## Core Pattern

Successful SWE-bench patches use **3 tool calls**:

1. **read_file** — Read the failing test first to understand expected behavior
2. **grep_search** — Locate the relevant source code
3. **edit_file** — Make the minimal targeted fix

## Warnings

- **Avoid bash loops.** Failed attempts average 29 tool calls using bash brute force (compile→test→debug loops). This strategy has a 0% success rate.
- **Do not refactor.** Make the smallest change that fixes the test. No style improvements, no related fixes.
- **Read before editing.** Always read the failing test AND the source file before making changes. Understand what the test expects.

## Rules

1. Read the failing test first — it tells you exactly what behavior to fix
2. Use grep_search to find the exact location in source
3. Use edit_file for targeted changes — never write_file for full rewrites
4. One fix per instance. Don't chain unrelated changes
5. If the fix isn't obvious after reading, the model ceiling has been reached — move on rather than looping
