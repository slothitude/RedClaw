"""Knowledge graph memory backed by Cognee.

Provides structured, graph-based memory with entity/relationship extraction,
vector search, and graph traversal — a major upgrade over flat-file MEMORY.md.

Requires: ``pip install redclaw[cognee]``
"""

from redclaw.memory_graph.manager import KnowledgeGraph, KnowledgeStatus

__all__ = ["KnowledgeGraph", "KnowledgeStatus"]
