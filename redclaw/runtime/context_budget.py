"""Context Budget — token-aware injection of AGI state into system prompt.

Allocates character budgets per section and proportionally reduces
if total exceeds the limit. Prevents AGI state from bloating context.
"""

from __future__ import annotations

from dataclasses import dataclass


# Default character budgets per section
_BUDGETS = {
    "soul": 500,
    "wisdom": 800,
    "dna": 200,
    "goals": 300,
    "dharma": 400,
    "reflection": 300,
}

_TOTAL_BUDGET = 3000  # max chars for all AGI sections combined


@dataclass
class BudgetedSection:
    """A section with its allocated budget."""
    name: str
    content: str
    budget: int


def budget_context(
    soul_text: str = "",
    wisdom: str = "",
    dna_summary: str = "",
    goals_summary: str = "",
    dharma: str = "",
    reflection: str = "",
) -> str:
    """Apply character budgets to AGI context sections.

    If total content exceeds _TOTAL_BUDGET, proportionally reduce each section.
    Returns the combined budgeted text.
    """
    sections = [
        BudgetedSection("soul", soul_text, _BUDGETS["soul"]),
        BudgetedSection("wisdom", wisdom, _BUDGETS["wisdom"]),
        BudgetedSection("dna", dna_summary, _BUDGETS["dna"]),
        BudgetedSection("goals", goals_summary, _BUDGETS["goals"]),
        BudgetedSection("dharma", dharma, _BUDGETS["dharma"]),
        BudgetedSection("reflection", reflection, _BUDGETS["reflection"]),
    ]

    # Filter empty sections
    sections = [s for s in sections if s.content.strip()]

    if not sections:
        return ""

    # Calculate total needed vs available
    total_content = sum(len(s.content) for s in sections)
    total_budget = min(sum(s.budget for s in sections), _TOTAL_BUDGET)

    if total_content <= total_budget:
        # Everything fits — just truncate to individual budgets
        parts: list[str] = []
        for s in sections:
            parts.append(s.content[:s.budget])
        return "\n".join(parts)

    # Need to reduce — proportional scaling
    scale = total_budget / total_content
    parts: list[str] = []
    for s in sections:
        scaled_budget = max(100, int(s.budget * scale))
        parts.append(s.content[:scaled_budget])
    return "\n".join(parts)
