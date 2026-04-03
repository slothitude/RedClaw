"""Agent-facing tools for knowledge graph memory.

Tools:
- ``knowledge`` — add, cognify, search, memify, stats, prune, list, delete, visualize

All actions are async and use Cognee under the hood. Falls back gracefully
when cognee is not installed.
"""

from __future__ import annotations

import json
from typing import Any

from redclaw.memory_graph.manager import KnowledgeGraph

# ── Shared instance ──────────────────────────────────────────

_kg: KnowledgeGraph | None = None


def get_knowledge_graph(
    data_dir: str | None = None,
    llm_api_key: str | None = None,
) -> KnowledgeGraph:
    """Get or create the global KnowledgeGraph instance."""
    global _kg
    if _kg is None:
        _kg = KnowledgeGraph(data_dir=data_dir, llm_api_key=llm_api_key)
    return _kg


def set_knowledge_graph(kg: KnowledgeGraph) -> None:
    """Override the global KnowledgeGraph instance."""
    global _kg
    _kg = kg


# ── Tool execute function ────────────────────────────────────


async def execute_knowledge(
    action: str = "stats",
    data: str = "",
    dataset_name: str = "redclaw_memory",
    query: str = "",
    search_type: str = "GRAPH_COMPLETION",
    top_k: int = 5,
    run_in_background: bool = False,
    **kwargs: Any,
) -> str:
    """Knowledge graph memory tool powered by Cognee.

    Actions:
        add       — Ingest text/file/URL data into a dataset
        cognify   — Build knowledge graph (extract entities + relationships)
        search    — Query the knowledge graph (graph traversal, RAG, etc.)
        memify    — Enrich graph with inferred connections
        stats     — Show datasets and graph status
        list      — List all datasets
        delete    — Delete a dataset
        prune     — Clear all data and reset
        visualize — Export graph to HTML
    """
    kg = get_knowledge_graph()

    if action == "add":
        if not data:
            return "Error: data is required for add. Provide text, a file path, or URL."
        return await kg.add(data=data, dataset_name=dataset_name)

    elif action == "cognify":
        datasets = [dataset_name] if dataset_name != "redclaw_memory" else None
        return await kg.cognify(datasets=datasets, run_in_background=run_in_background)

    elif action == "search":
        if not query:
            return "Error: query is required for search."
        results = await kg.search(
            query=query,
            search_type=search_type,
            datasets=[dataset_name] if dataset_name != "redclaw_memory" else None,
            top_k=top_k,
        )
        if not results:
            return f"No results for '{query}'. Make sure to run 'cognify' after adding data."
        lines = []
        for i, r in enumerate(results, 1):
            score_str = f" (score: {r.score:.2f})" if r.score else ""
            lines.append(f"{i}. {r.text}{score_str}")
        return "\n\n".join(lines)

    elif action == "memify":
        return await kg.memify(dataset=dataset_name)

    elif action == "stats":
        stats = await kg.stats()
        if stats.status.value == "unavailable":
            return "Knowledge graph unavailable. Install with: pip install redclaw[cognee]"
        lines = [
            f"Status: {stats.status.value}",
            f"Datasets: {stats.total_datasets}",
        ]
        if stats.datasets:
            lines.append(f"  - " + "\n  - ".join(stats.datasets))
        return "\n".join(lines)

    elif action == "list":
        datasets = await kg.list_datasets()
        if not datasets:
            return "No datasets. Use 'add' to ingest data."
        return "Datasets:\n" + "\n".join(f"  - {d}" for d in datasets)

    elif action == "delete":
        if not dataset_name:
            return "Error: dataset_name is required for delete."
        return await kg.delete_dataset(dataset_name)

    elif action == "prune":
        return await kg.prune()

    elif action == "visualize":
        output = kwargs.get("output_path", "knowledge_graph.html")
        return await kg.visualize(output)

    else:
        return (
            f"Error: Unknown action '{action}'. "
            "Use: add, cognify, search, memify, stats, list, delete, prune, visualize"
        )
