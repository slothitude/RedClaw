"""Wiki tool — single tool with action dispatch (mirrors tools/memory.py)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redclaw.api.client import LLMClient
    from redclaw.api.providers import ProviderConfig

from redclaw.wiki.manager import WikiManager

logger = logging.getLogger(__name__)

# ── Global singleton ───────────────────────────────────────────

_wiki_manager: WikiManager | None = None


def get_wiki_manager(
    wiki_dir: str | None = None,
    client: LLMClient | None = None,
    provider: ProviderConfig | None = None,
    model: str = "",
) -> WikiManager:
    """Get or create the global WikiManager."""
    global _wiki_manager
    if _wiki_manager is None:
        _wiki_manager = WikiManager(wiki_dir, client, provider, model)
    return _wiki_manager


async def execute_wiki(
    action: str,
    source: str = "",
    topic: str = "general",
    question: str = "",
    wiki_dir: str | None = None,
    client: LLMClient | None = None,
    provider: ProviderConfig | None = None,
    model: str = "",
    **kwargs,
) -> str:
    """Wiki tool — ingest, query, lint, stats.

    action: 'ingest', 'query', 'lint', 'stats'
    """
    mgr = get_wiki_manager(wiki_dir, client, provider, model)

    if action == "ingest":
        if not source:
            return "Error: 'source' is required for ingest (URL or file path)."
        return await mgr.ingest(source, topic)
    elif action == "query":
        if not question:
            return "Error: 'question' is required for query."
        return await mgr.query(question)
    elif action == "lint":
        return await mgr.lint()
    elif action == "stats":
        return mgr.stats()
    else:
        return f"Error: Unknown action '{action}'. Use ingest, query, lint, or stats."
