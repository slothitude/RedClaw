"""Notes management with JSON persistence."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_NOTES_PATH = Path.home() / ".redclaw" / "assistant" / "notes.json"


@dataclass
class Note:
    """A single note."""
    id: str = ""
    title: str = ""
    content: str = ""  # markdown
    tags: list[str] = field(default_factory=list)
    source: str = ""
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid.uuid4().hex[:8]
        now = datetime.now(timezone.utc).isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


class NoteStore:
    """Persistent note store backed by JSON file."""

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path) if path else DEFAULT_NOTES_PATH
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._notes: dict[str, Note] = {}
        self._load()

    def _load(self) -> None:
        if self._path.is_file():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                for item in data:
                    n = Note(**{k: v for k, v in item.items() if k in Note.__dataclass_fields__})
                    self._notes[n.id] = n
            except Exception as e:
                logger.warning(f"Failed to load notes: {e}")

    def _save(self) -> None:
        data = [asdict(n) for n in self._notes.values()]
        _atomic_write(self._path, json.dumps(data, indent=2, ensure_ascii=False))

    def add(
        self,
        title: str,
        content: str = "",
        tags: list[str] | None = None,
        source: str = "",
    ) -> Note:
        """Add a new note and persist."""
        note = Note(
            title=title,
            content=content,
            tags=tags or [],
            source=source,
        )
        self._notes[note.id] = note
        self._save()
        return note

    def update(self, note_id: str, **kwargs: object) -> Note | None:
        """Update a note's fields."""
        note = self._notes.get(note_id)
        if note is None:
            return None
        for k, v in kwargs.items():
            if hasattr(note, k) and k not in ("id", "created_at"):
                setattr(note, k, v)
        note.updated_at = datetime.now(timezone.utc).isoformat()
        self._save()
        return note

    def delete(self, note_id: str) -> bool:
        """Delete a note by ID."""
        if note_id in self._notes:
            del self._notes[note_id]
            self._save()
            return True
        return False

    def get(self, note_id: str) -> Note | None:
        return self._notes.get(note_id)

    def search(self, query: str) -> list[Note]:
        """Search notes by title or content."""
        q = query.lower()
        return [
            n for n in self._notes.values()
            if q in n.title.lower() or q in n.content.lower()
        ]

    def list_notes(self, tag: str | None = None, limit: int = 20) -> list[Note]:
        """List notes, optionally filtered by tag."""
        results = list(self._notes.values())
        if tag:
            results = [n for n in results if tag in n.tags]
        results.sort(key=lambda n: n.updated_at, reverse=True)
        return results[:limit]


def _atomic_write(path: Path, content: str) -> None:
    """Write file atomically."""
    fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), prefix=".redclaw_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
