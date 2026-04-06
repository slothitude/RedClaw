"""Wiki data types — pages, ingest records, stats."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class WikiPage:
    """A compiled wiki page."""
    title: str
    topic: str
    source_path: str
    compiled_path: str
    ingested_at: str = ""
    word_count: int = 0


@dataclass
class IngestRecord:
    """Record of a source ingestion."""
    source: str
    topic: str
    timestamp: str = ""
    pages_created: int = 1


@dataclass
class WikiStats:
    """Aggregate wiki statistics."""
    total_pages: int = 0
    total_words: int = 0
    last_ingest: str = ""
    last_lint: str = ""
