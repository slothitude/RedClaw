"""Dream Synthesis (Brahman Dream) — periodic LLM-powered consolidation.

After enough subagent runs accumulate, dream() runs an LLM synthesis pass
that consolidates entombed records into refined dharma and bloodline wisdom.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from redclaw.api.client import LLMClient
    from redclaw.api.providers import ProviderConfig
    from redclaw.crypt.crypt import Crypt

logger = logging.getLogger(__name__)

# Dream triggers
_MIN_ENTOMBMENTS = 10
_COOLDOWN_SECONDS = 1800  # 30 minutes


@dataclass
class DreamResult:
    """Result of a dream synthesis pass."""
    records_processed: int = 0
    insights_generated: int = 0
    dharma_updated: bool = False
    bloodlines_updated: list[str] = None  # type: ignore[assignment]
    timestamp: str = ""

    def __post_init__(self) -> None:
        if self.bloodlines_updated is None:
            self.bloodlines_updated = []


@dataclass
class _DreamMeta:
    """Persistent state for dream scheduling."""
    last_dream_time: str = ""
    last_entomb_count: int = 0
    dream_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> _DreamMeta:
        return cls(
            last_dream_time=data.get("last_dream_time", ""),
            last_entomb_count=data.get("last_entomb_count", 0),
            dream_count=data.get("dream_count", 0),
        )


class DreamSynthesizer:
    """Periodic LLM-powered dream synthesis of accumulated experience."""

    def __init__(
        self,
        client: LLMClient,
        provider: ProviderConfig,
        model: str,
        crypt_dir: Path | None = None,
    ) -> None:
        self.client = client
        self.provider = provider
        self.model = model
        self._crypt_dir = crypt_dir or Path.home() / ".redclaw" / "crypt"
        self._meta_path = self._crypt_dir / "dream_meta.json"
        self._meta = self._load_meta()

    def _load_meta(self) -> _DreamMeta:
        if self._meta_path.is_file():
            try:
                data = json.loads(self._meta_path.read_text(encoding="utf-8"))
                return _DreamMeta.from_dict(data)
            except (json.JSONDecodeError, KeyError):
                pass
        return _DreamMeta()

    def _save_meta(self) -> None:
        self._meta_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self._meta_path.parent), prefix=".dream_", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._meta.to_dict(), f, indent=2)
            os.replace(tmp, self._meta_path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def should_dream(self, current_entomb_count: int) -> bool:
        """Check if dream synthesis should run.

        Triggers when:
        - 10+ new entombments since last dream
        - 30min cooldown since last dream
        """
        new_records = current_entomb_count - self._meta.last_entomb_count
        if new_records < _MIN_ENTOMBMENTS:
            return False

        # Check cooldown
        if self._meta.last_dream_time:
            try:
                last = datetime.fromisoformat(self._meta.last_dream_time)
                elapsed = (datetime.now(timezone.utc) - last).total_seconds()
                if elapsed < _COOLDOWN_SECONDS:
                    return False
            except (ValueError, TypeError):
                pass

        return True

    async def dream(self, crypt: Crypt) -> DreamResult:
        """Run dream synthesis over accumulated entombed records.

        1. Load new records since last dream
        2. Load current dharma + bloodlines
        3. LLM synthesis
        4. Replace dharma, merge bloodlines
        """
        result = DreamResult(timestamp=datetime.now(timezone.utc).isoformat())

        # Load new entombed records
        entombed_dir = crypt.entombed_dir
        records: list[dict[str, Any]] = []
        for path in sorted(entombed_dir.glob("sub-*.json")):
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
                ts = rec.get("timestamp", "")
                # Only include records newer than last dream
                if self._meta.last_dream_time and ts <= self._meta.last_dream_time:
                    continue
                records.append(rec)
            except (json.JSONDecodeError, OSError):
                continue

        if not records:
            logger.info("Dream: no new records to process")
            return result

        result.records_processed = len(records)

        # Load current wisdom
        dharma = crypt.load_dharma()
        bloodlines: dict[str, str] = {}
        for bl_type in ("coder", "searcher", "general"):
            from redclaw.runtime.subagent_types import SubagentType
            sa_type = SubagentType(bl_type)
            wisdom = crypt.load_bloodline_wisdom(sa_type)
            if wisdom:
                bloodlines[bl_type] = wisdom

        # Build synthesis prompt
        records_summary = "\n".join(
            f"- [{r.get('type', '?')}] {'OK' if r.get('success') else 'FAIL'}: "
            f"{r.get('task', '')[:100]}"
            for r in records[:50]
        )

        prompt = (
            "Analyze these subagent records and current wisdom. Produce:\n"
            "1. REFINED DHARMA: A concise synthesis of cross-cutting patterns (max 400 words). "
            "Replace the existing dharma entirely.\n"
            "2. BLOODLINE UPDATES: For each bloodline, list 3-5 key insights as bullet points.\n\n"
            f"Records ({len(records)}):\n{records_summary}\n\n"
            f"Current dharma:\n{dharma[:1000]}\n\n"
        )
        for bl_name, bl_wisdom in bloodlines.items():
            prompt += f"Current {bl_name} bloodline:\n{bl_wisdom[:500]}\n\n"

        prompt += (
            "Output format:\n"
            "=== DHARMA ===\n<refined dharma>\n"
            "=== CODER ===\n<bullets>\n"
            "=== SEARCHER ===\n<bullets>\n"
            "=== GENERAL ===\n<bullets>\n"
        )

        # LLM call — collect streamed text
        try:
            from redclaw.api.types import InputMessage, MessageRequest, Role, TextBlock
            request = MessageRequest(
                model=self.model,
                messages=[InputMessage(role=Role.USER, content=[TextBlock(text=prompt)])],
                system="You are a wisdom synthesis engine. Be concise and insightful.",
                max_tokens=2048,
            )
            parts: list[str] = []
            async for event in self.client.stream_message(request):
                if event.text_delta:
                    parts.append(event.text_delta)
            synthesis = "".join(parts)
            if not synthesis:
                logger.warning("Dream: LLM returned empty synthesis")
                return result
        except Exception as e:
            logger.error("Dream LLM call failed: %s", e)
            return result

        # Parse and apply synthesis
        self._apply_synthesis(crypt, synthesis, result)

        # Update meta
        total_entombed = len(list(entombed_dir.glob("sub-*.json")))
        self._meta.last_dream_time = datetime.now(timezone.utc).isoformat()
        self._meta.last_entomb_count = total_entombed
        self._meta.dream_count += 1
        self._save_meta()

        logger.info(
            "Dream #%d complete: %d records processed, %d insights",
            self._meta.dream_count, result.records_processed, result.insights_generated,
        )
        return result

    def _apply_synthesis(self, crypt: Crypt, synthesis: str, result: DreamResult) -> None:
        """Parse the LLM synthesis and apply to dharma + bloodlines."""
        # Parse sections
        sections: dict[str, str] = {}
        current_section = ""
        current_lines: list[str] = []

        for line in synthesis.split("\n"):
            if line.startswith("=== ") and line.endswith(" ==="):
                if current_section:
                    sections[current_section] = "\n".join(current_lines).strip()
                current_section = line[4:-4].strip().lower()
                current_lines = []
            else:
                current_lines.append(line)

        if current_section:
            sections[current_section] = "\n".join(current_lines).strip()

        # Update dharma
        if "dharma" in sections and sections["dharma"]:
            dharma_path = crypt.crypt_dir / "dharma.md"
            new_dharma = f"# Dharma — Accumulated Patterns\n\n{sections['dharma']}\n"
            crypt._atomic_write(dharma_path, new_dharma)
            result.dharma_updated = True
            result.insights_generated += sections["dharma"].count("-") + sections["dharma"].count("*")

        # Update bloodlines — REPLACE entirely with dream-synthesized content
        from redclaw.runtime.subagent_types import SubagentType
        for bl_name in ("coder", "searcher", "general"):
            key = bl_name
            if key in sections and sections[key]:
                try:
                    sa_type = SubagentType(bl_name)
                    # Build a clean bloodline from dream synthesis
                    new_lines = [f"# {bl_name.title()} Bloodline Wisdom\n"]
                    for line in sections[key].split("\n"):
                        line = line.strip()
                        if line.startswith("- ") or line.startswith("* "):
                            new_lines.append(f"- {line[2:].strip()}")
                    if len(new_lines) > 1:
                        crypt._atomic_write(
                            crypt.bloodlines_dir / f"{bl_name}.md",
                            "\n".join(new_lines) + "\n",
                        )
                        if bl_name not in result.bloodlines_updated:
                            result.bloodlines_updated.append(bl_name)
                        result.insights_generated += len(new_lines) - 1
                except (ValueError, KeyError):
                    pass
