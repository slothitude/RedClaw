"""Knowledge graph manager — wraps Cognee for ECL (Extract, Cognify, Load) pipelines.

Lifecycle:
1. ``add()``     — ingest text/files/URLs into a named dataset
2. ``cognify()`` — run ECL pipeline: extract entities, build knowledge graph, store vectors
3. ``search()``  — query the graph with multiple strategies (graph traversal, RAG, summaries)
4. ``memify()``  — enrich graph with inferred connections (optional)

All operations are async. Cognee uses embedded Kuzu (graph) + LanceDB (vectors) + SQLite
by default, so no external servers are needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Status & Config ──────────────────────────────────────────


class KnowledgeStatus(str, Enum):
    """Status of the knowledge graph subsystem."""
    UNAVAILABLE = "unavailable"   # cognee not installed
    IDLE = "idle"                 # ready, no data ingested
    INGESTING = "ingesting"       # data being added
    COGNIFYING = "cognifying"     # ECL pipeline running
    READY = "ready"               # graph built, searchable
    ERROR = "error"


@dataclass
class SearchResult:
    """A single search result from the knowledge graph."""
    text: str = ""
    score: float = 0.0
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class KnowledgeStats:
    """Stats about the knowledge graph."""
    datasets: list[str] = field(default_factory=list)
    total_datasets: int = 0
    status: KnowledgeStatus = KnowledgeStatus.UNAVAILABLE


# ── Knowledge Graph Manager ──────────────────────────────────


class KnowledgeGraph:
    """Manages Cognee-backed knowledge graph memory.

    If cognee is not installed, all operations return graceful fallback
    messages instead of crashing.
    """

    def __init__(self, data_dir: str | None = None, llm_api_key: str | None = None) -> None:
        self._cognee = None
        self._status = KnowledgeStatus.UNAVAILABLE
        self._data_dir = data_dir
        self._llm_api_key = llm_api_key

        try:
            import cognee
            self._cognee = cognee
            self._status = KnowledgeStatus.IDLE

            # Configure data directory if provided
            if data_dir:
                cognee.config.data_root_directory(data_dir)

            # Set LLM API key if provided
            if llm_api_key:
                cognee.config.set_llm_api_key(llm_api_key)

            logger.info("Cognee knowledge graph initialized")
        except ImportError:
            logger.info("Cognee not installed — knowledge graph unavailable. pip install redclaw[cognee]")

    @property
    def status(self) -> KnowledgeStatus:
        return self._status

    @property
    def available(self) -> bool:
        return self._cognee is not None

    # ── Core Operations ──────────────────────────────────────

    async def add(
        self,
        data: str | list[str],
        dataset_name: str = "redclaw_memory",
    ) -> str:
        """Ingest data into a named dataset.

        Args:
            data: Text string, list of texts, file path, or URL.
            dataset_name: Named dataset to group related data.

        Returns:
            Status message.
        """
        if not self.available:
            return "Knowledge graph unavailable. Install with: pip install redclaw[cognee]"

        try:
            self._status = KnowledgeStatus.INGESTING
            await self._cognee.add(data, dataset_name=dataset_name)
            self._status = KnowledgeStatus.IDLE
            count = len(data) if isinstance(data, list) else 1
            return f"Ingested {count} item(s) into dataset '{dataset_name}'. Run cognify to build the graph."
        except Exception as e:
            self._status = KnowledgeStatus.ERROR
            logger.error(f"Knowledge graph add failed: {e}")
            return f"Error ingesting data: {e}"

    async def cognify(
        self,
        datasets: list[str] | None = None,
        run_in_background: bool = False,
    ) -> str:
        """Run the ECL pipeline: extract entities, build knowledge graph, store vectors.

        Args:
            datasets: Specific datasets to process. None = all.
            run_in_background: Run pipeline async (returns immediately).

        Returns:
            Status message.
        """
        if not self.available:
            return "Knowledge graph unavailable."

        try:
            self._status = KnowledgeStatus.COGNIFYING
            kwargs: dict[str, Any] = {}
            if datasets:
                kwargs["datasets"] = datasets
            if run_in_background:
                kwargs["run_in_background"] = True

            await self._cognee.cognify(**kwargs)

            self._status = KnowledgeStatus.READY
            ds_str = ", ".join(datasets) if datasets else "all datasets"
            return f"Knowledge graph built for {ds_str}."
        except Exception as e:
            self._status = KnowledgeStatus.ERROR
            logger.error(f"Cognify failed: {e}")
            return f"Error building knowledge graph: {e}"

    async def search(
        self,
        query: str,
        search_type: str = "GRAPH_COMPLETION",
        datasets: list[str] | None = None,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Search the knowledge graph.

        Args:
            query: Natural language query.
            search_type: One of: GRAPH_COMPLETION, RAG_COMPLETION, CHUNKS,
                         SUMMARIES, GRAPH_COMPLETION_COT, RELATIONSHIPS,
                         FEELING_LUCKY.
            datasets: Limit search to specific datasets.
            top_k: Max results to return.

        Returns:
            List of SearchResult objects.
        """
        if not self.available:
            return []

        try:
            from cognee.api.v1.search import SearchType

            # Map string to SearchType enum (resilient to version differences)
            type_map = {}
            for name in [
                "GRAPH_COMPLETION", "RAG_COMPLETION", "CHUNKS", "SUMMARIES",
                "GRAPH_COMPLETION_COT", "GRAPH_SUMMARY_COMPLETION",
                "GRAPH_COMPLETION_CONTEXT_EXTENSION", "RELATIONSHIPS",
                "CHUNKS_LEXICAL", "FEELING_LUCKY", "CODE",
                "NATURAL_LANGUAGE", "TEMPORAL",
            ]:
                if hasattr(SearchType, name):
                    type_map[name] = getattr(SearchType, name)

            st = type_map.get(search_type, SearchType.GRAPH_COMPLETION)

            kwargs: dict[str, Any] = {
                "query_text": query,
                "query_type": st,
            }
            if datasets:
                kwargs["datasets"] = datasets

            raw_results = await self._cognee.search(**kwargs)

            results: list[SearchResult] = []
            for r in raw_results[:top_k]:
                if isinstance(r, str):
                    results.append(SearchResult(text=r))
                elif isinstance(r, dict):
                    results.append(SearchResult(
                        text=r.get("text", r.get("content", str(r))),
                        score=r.get("score", 0.0),
                        source=r.get("source", ""),
                        metadata=r.get("metadata", {}),
                    ))
                else:
                    results.append(SearchResult(text=str(r)))

            return results
        except Exception as e:
            logger.error(f"Knowledge search failed: {e}")
            return []

    async def memify(self, dataset: str = "redclaw_memory") -> str:
        """Enrich graph with inferred connections and rules.

        Memify discovers implicit relationships that weren't caught by
        the initial entity extraction.
        """
        if not self.available:
            return "Knowledge graph unavailable."

        try:
            await self._cognee.memify(dataset=dataset)
            return f"Graph enriched for dataset '{dataset}'."
        except Exception as e:
            logger.error(f"Memify failed: {e}")
            return f"Error enriching graph: {e}"

    async def delete_dataset(self, dataset_name: str) -> str:
        """Delete a dataset and all its data."""
        if not self.available:
            return "Knowledge graph unavailable."

        try:
            datasets = await self._cognee.datasets.list_datasets()
            target_id = None
            for ds in datasets:
                ds_name = ds.name if hasattr(ds, "name") else str(ds)
                if ds_name == dataset_name:
                    target_id = ds.id if hasattr(ds, "id") else ds
                    break

            if target_id is None:
                return f"Dataset '{dataset_name}' not found."

            # Delete all data items within the dataset
            data_items = await self._cognee.datasets.list_data(target_id)
            for item in data_items:
                item_id = item.id if hasattr(item, "id") else item
                try:
                    await self._cognee.datasets.delete_data(dataset_id=target_id, data_id=item_id)
                except Exception:
                    pass

            return f"Dataset '{dataset_name}' deleted ({len(data_items)} items)."
        except Exception as e:
            return f"Error deleting dataset: {e}"

    async def list_datasets(self) -> list[str]:
        """List all dataset names."""
        if not self.available:
            return []

        try:
            datasets = await self._cognee.datasets.list_datasets()
            return [ds.name if hasattr(ds, "name") else str(ds) for ds in datasets]
        except Exception:
            return []

    async def stats(self) -> KnowledgeStats:
        """Get knowledge graph stats."""
        if not self.available:
            return KnowledgeStats(status=KnowledgeStatus.UNAVAILABLE)

        try:
            datasets = await self.list_datasets()
            return KnowledgeStats(
                datasets=datasets,
                total_datasets=len(datasets),
                status=self._status,
            )
        except Exception:
            return KnowledgeStats(status=KnowledgeStatus.ERROR)

    async def prune(self) -> str:
        """Clear all data and reset the knowledge graph."""
        if not self.available:
            return "Knowledge graph unavailable."

        try:
            await self._cognee.prune.prune_data()
            await self._cognee.prune.prune_system(metadata=True)
            self._status = KnowledgeStatus.IDLE
            return "Knowledge graph cleared."
        except Exception as e:
            return f"Error pruning: {e}"

    async def visualize(self, output_path: str = "knowledge_graph.html") -> str:
        """Export graph visualization to HTML."""
        if not self.available:
            return "Knowledge graph unavailable."

        try:
            await self._cognee.visualize_graph(output_path)
            return f"Graph visualization saved to {output_path}"
        except Exception as e:
            return f"Error visualizing: {e}"
