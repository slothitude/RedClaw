"""Session management with JSONL persistence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from redclaw.api.types import InputMessage, parse_content_block, Role


@dataclass
class ConversationMessage:
    role: Role
    content: list  # list of content blocks
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role.value,
            "content": [b.to_dict() for b in self.content],
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ConversationMessage:
        return cls(
            role=Role(d["role"]),
            content=[parse_content_block(b) for b in d.get("content", [])],
            timestamp=d.get("timestamp", time.time()),
        )

    @classmethod
    def from_input_message(cls, msg: InputMessage) -> ConversationMessage:
        return cls(role=msg.role, content=list(msg.content))

    def to_input_message(self) -> InputMessage:
        return InputMessage(role=self.role, content=list(self.content))

    def text_content(self) -> str:
        from redclaw.api.types import TextBlock
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))


@dataclass
class Session:
    """A conversation session persisted as JSONL."""

    id: str
    messages: list[ConversationMessage] = field(default_factory=list)
    model: str = ""
    provider: str = ""
    working_dir: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def add_message(self, msg: InputMessage) -> None:
        self.messages.append(ConversationMessage.from_input_message(msg))
        self.updated_at = time.time()

    def to_input_messages(self) -> list[InputMessage]:
        return [m.to_input_message() for m in self.messages]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "messages": [m.to_dict() for m in self.messages],
            "model": self.model,
            "provider": self.provider,
            "working_dir": self.working_dir,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Session:
        return cls(
            id=d["id"],
            messages=[ConversationMessage.from_dict(m) for m in d.get("messages", [])],
            model=d.get("model", ""),
            provider=d.get("provider", ""),
            working_dir=d.get("working_dir", ""),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )


# ── Persistence ──────────────────────────────────────────────

SESSIONS_DIR = ".redclaw"


def sessions_dir(cwd: str | Path | None = None) -> Path:
    base = Path(cwd) if cwd else Path.cwd()
    return base / SESSIONS_DIR


def save_session(session: Session, cwd: str | Path | None = None) -> Path:
    """Save session as JSONL. Returns path to the file."""
    d = sessions_dir(cwd)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{session.id}.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for msg in session.messages:
            f.write(json.dumps(msg.to_dict(), ensure_ascii=False) + "\n")
    # Write metadata as a special first line
    meta_path = d / f"{session.id}.meta.json"
    meta = {
        "id": session.id,
        "model": session.model,
        "provider": session.provider,
        "working_dir": session.working_dir,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "message_count": len(session.messages),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return path


def load_session(session_id: str, cwd: str | Path | None = None) -> Session | None:
    """Load a session from JSONL. Returns None if not found."""
    d = sessions_dir(cwd)
    path = d / f"{session_id}.jsonl"
    if not path.exists():
        return None
    messages: list[ConversationMessage] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                messages.append(ConversationMessage.from_dict(json.loads(line)))
    session = Session(id=session_id, messages=messages)
    # Load metadata
    meta_path = d / f"{session_id}.meta.json"
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        session.model = meta.get("model", "")
        session.provider = meta.get("provider", "")
        session.working_dir = meta.get("working_dir", "")
        session.created_at = meta.get("created_at", session.created_at)
        session.updated_at = meta.get("updated_at", session.updated_at)
    return session


def list_sessions(cwd: str | Path | None = None) -> list[dict[str, Any]]:
    """List all sessions with metadata."""
    d = sessions_dir(cwd)
    if not d.exists():
        return []
    results = []
    for meta_path in sorted(d.glob("*.meta.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            results.append(meta)
        except (json.JSONDecodeError, OSError):
            continue
    return results


def delete_session(session_id: str, cwd: str | Path | None = None) -> bool:
    """Delete a session. Returns True if deleted."""
    d = sessions_dir(cwd)
    deleted = False
    for ext in (".jsonl", ".meta.json"):
        p = d / f"{session_id}{ext}"
        if p.exists():
            p.unlink()
            deleted = True
    return deleted
