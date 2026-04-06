"""WikiManager — LLM-compiled markdown wiki knowledge base.

Mirrors the DreamSynthesizer pattern for LLM calls and the MemoryManager
pattern for atomic writes.  Phase 1 implements ingest, query, lint, stats.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from redclaw.api.client import LLMClient
    from redclaw.api.providers import ProviderConfig

from redclaw.wiki.types import WikiPage, WikiStats

logger = logging.getLogger(__name__)

# ── Compile prompt (ingest) ────────────────────────────────────

_COMPILE_PROMPT = (
    "You are compiling raw source material into a structured wiki page.\n\n"
    "Rules:\n"
    "- Write a comprehensive summary of the key concepts\n"
    "- Use markdown headers (##) for sections\n"
    "- Use [[wikilinks]] to reference related concepts\n"
    "- Include a Sources section linking to the raw file\n"
    "- Be factual — only include information from the source\n\n"
    "Raw source:\n---\n{content}\n---\n\nWrite the wiki page:"
)

# ── Query prompt ───────────────────────────────────────────────

_QUERY_PROMPT = (
    "You are answering a question using a wiki knowledge base.\n\n"
    "Wiki index:\n{index}\n\n"
    "Relevant pages:\n{pages}\n\n"
    "Question: {question}\n\n"
    "Answer with inline citations like [topic/slug]. "
    "If the wiki doesn't contain enough information, say so."
)


def _slugify(text: str) -> str:
    """Convert text to a filesystem-safe slug."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "_", text)
    return text[:80] or "untitled"


class WikiManager:
    """Manages the LLM-compiled wiki knowledge base."""

    def __init__(
        self,
        wiki_dir: str | Path | None = None,
        client: LLMClient | None = None,
        provider: ProviderConfig | None = None,
        model: str = "",
    ) -> None:
        self._wiki_dir = Path(wiki_dir) if wiki_dir else Path.home() / ".redclaw" / "wiki"
        self._raw_dir = self._wiki_dir / "raw"
        self._pages_dir = self._wiki_dir / "wiki"
        self._index_path = self._pages_dir / "index.md"
        self._log_path = self._wiki_dir / "log.md"
        self.client = client
        self.provider = provider
        self.model = model
        self._ensure_dirs()

    # ── Directory setup ─────────────────────────────────────

    def _ensure_dirs(self) -> None:
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._pages_dir.mkdir(parents=True, exist_ok=True)

    # ── Atomic write (same pattern as crypt.py / memory.py) ─

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".wiki_", suffix=".md")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    # ── Index management ────────────────────────────────────

    def _load_index(self) -> str:
        if self._index_path.is_file():
            return self._index_path.read_text(encoding="utf-8")
        return ""

    def _update_index(self, page: WikiPage) -> None:
        """Append a page entry to wiki/index.md."""
        current = self._load_index()
        entry = f"- [{page.title}]({page.topic}/{Path(page.compiled_path).stem}) — {page.word_count} words ({page.ingested_at})\n"
        if not current.strip():
            current = "# Wiki Index\n\n"
        self._atomic_write(self._index_path, current + entry)

    def _append_log(self, action: str, detail: str) -> None:
        """Append to wiki/log.md."""
        ts = datetime.now(timezone.utc).isoformat()
        entry = f"- [{ts}] {action}: {detail}\n"
        current = ""
        if self._log_path.is_file():
            current = self._log_path.read_text(encoding="utf-8")
        if not current.strip():
            current = "# Wiki Operation Log\n\n"
        self._atomic_write(self._log_path, current + entry)

    # ── LLM helpers ─────────────────────────────────────────

    async def _llm_call(self, system: str, prompt: str) -> str:
        """Call the LLM and return the full text response."""
        from redclaw.api.types import InputMessage, MessageRequest, Role, TextBlock

        request = MessageRequest(
            model=self.model,
            messages=[InputMessage(role=Role.USER, content=[TextBlock(text=prompt)])],
            system=system,
            max_tokens=4096,
        )
        parts: list[str] = []
        async for event in self.client.stream_message(request):
            if event.text_delta:
                parts.append(event.text_delta)
        return "".join(parts)

    # ── Source fetching ─────────────────────────────────────

    async def _fetch_source(self, source: str) -> str:
        """Read a source — URL (httpx) or local file."""
        if source.startswith(("http://", "https://")):
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(source, follow_redirects=True)
                resp.raise_for_status()
                return resp.text
        # Local file
        path = Path(source)
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
        raise FileNotFoundError(f"Source not found: {source}")

    # ── Core operations ─────────────────────────────────────

    async def ingest(self, source: str, topic: str) -> str:
        """Ingest a source into the wiki via LLM compilation.

        1. Fetch source content
        2. Save raw (immutable)
        3. LLM compile into structured wiki page
        4. Save compiled page
        5. Update index + log
        """
        if not self.client:
            return "Error: Wiki LLM client not configured. Start with --wiki and a provider."

        # 1. Fetch
        try:
            content = await self._fetch_source(source)
        except Exception as e:
            return f"Error fetching source: {e}"

        if not content.strip():
            return "Error: Source is empty."

        ts = datetime.now(timezone.utc).isoformat()
        slug = _slugify(Path(source).stem or topic)

        # 2. Save raw (immutable)
        raw_dir = self._raw_dir / topic
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{slug}.md"
        if not raw_path.exists():
            self._atomic_write(raw_path, content)

        # 3. LLM compile
        compile_prompt = _COMPILE_PROMPT.format(content=content[:12000])
        try:
            compiled = await self._llm_call(
                "You are a wiki compiler. Produce well-structured markdown.",
                compile_prompt,
            )
        except Exception as e:
            logger.error("Wiki compile LLM call failed: %s", e)
            return f"Error compiling wiki page: {e}"

        if not compiled.strip():
            return "Error: LLM returned empty compilation."

        # 4. Save compiled page
        page_dir = self._pages_dir / topic
        page_dir.mkdir(parents=True, exist_ok=True)
        page_path = page_dir / f"{slug}.md"
        self._atomic_write(page_path, compiled)

        word_count = len(compiled.split())

        # 5. Update index + log
        page = WikiPage(
            title=f"{topic}/{slug}",
            topic=topic,
            source_path=str(raw_path),
            compiled_path=str(page_path),
            ingested_at=ts,
            word_count=word_count,
        )
        self._update_index(page)
        self._append_log("ingest", f"{source} → {topic}/{slug} ({word_count} words)")

        return f"Ingested '{source}' → wiki/{topic}/{slug}.md ({word_count} words)"

    async def query(self, question: str) -> str:
        """Query the wiki — index-first, then read relevant pages and synthesize."""
        if not self.client:
            return "Error: Wiki LLM client not configured."

        index = self._load_index()
        if not index.strip():
            return "Wiki is empty. Use 'ingest' to add sources first."

        # Step 1: LLM picks relevant pages from index
        page_selection_prompt = (
            f"Wiki index:\n{index}\n\n"
            f"Question: {question}\n\n"
            "List the wiki page paths (topic/slug format) that are relevant, "
            "one per line. If none are relevant, respond 'NONE'."
        )
        selection = await self._llm_call(
            "You are a wiki index navigator. Be precise.",
            page_selection_prompt,
        )

        # Step 2: Read those pages
        pages_text = ""
        for line in selection.strip().split("\n"):
            line = line.strip().strip("- *[]")
            if not line or line.upper() == "NONE":
                continue
            page_path = self._pages_dir / f"{line}.md"
            if not page_path.is_file():
                # Try as topic/slug
                parts = line.split("/")
                if len(parts) == 2:
                    page_path = self._pages_dir / parts[0] / f"{parts[1]}.md"
            if page_path.is_file():
                page_content = page_path.read_text(encoding="utf-8")
                pages_text += f"\n--- {line} ---\n{page_content}\n"

        if not pages_text:
            return "No relevant wiki pages found for your question."

        # Step 3: LLM synthesizes answer
        query_prompt = _QUERY_PROMPT.format(
            index=index[:3000],
            pages=pages_text[:8000],
            question=question,
        )
        answer = await self._llm_call(
            "You are a wiki knowledge assistant. Answer with citations.",
            query_prompt,
        )
        return answer or "Could not generate an answer."

    async def lint(self) -> str:
        """Health-check the wiki: index consistency, wikilink resolution."""
        issues: list[str] = []

        # Check index exists
        if not self._index_path.is_file():
            return "Wiki has no index yet. Ingest some sources first."

        index = self._load_index()

        # Check all wiki pages appear in index
        all_pages: list[Path] = []
        for topic_dir in sorted(self._pages_dir.iterdir()):
            if topic_dir.is_dir() and topic_dir.name != "":
                for page_file in sorted(topic_dir.glob("*.md")):
                    rel = f"{topic_dir.name}/{page_file.stem}"
                    all_pages.append(page_file)
                    if rel not in index:
                        issues.append(f"Page {rel} not in index")

        # Check wikilinks resolve
        link_pattern = re.compile(r"\[\[(.+?)\]\]")
        all_slugs = {p.stem for p in all_pages}
        for page_file in all_pages:
            content = page_file.read_text(encoding="utf-8", errors="replace")
            for match in link_pattern.finditer(content):
                target = match.group(1).strip()
                if target not in all_slugs and not (self._pages_dir / f"{target}.md").exists():
                    issues.append(f"Broken wikilink [[{target}]] in {page_file.name}")

        if not issues:
            return f"Wiki is healthy — {len(all_pages)} pages, no issues found."
        return f"Wiki lint found {len(issues)} issue(s):\n" + "\n".join(f"  - {i}" for i in issues)

    def stats(self) -> str:
        """Return wiki statistics."""
        total_pages = 0
        total_words = 0
        for topic_dir in self._pages_dir.iterdir():
            if topic_dir.is_dir():
                for page_file in topic_dir.glob("*.md"):
                    total_pages += 1
                    total_words += len(page_file.read_text(encoding="utf-8", errors="replace").split())

        last_ingest = ""
        if self._log_path.is_file():
            log = self._log_path.read_text(encoding="utf-8")
            for line in reversed(log.split("\n")):
                if "ingest" in line:
                    last_ingest = line.strip()
                    break

        return (
            f"Wiki stats:\n"
            f"  Pages: {total_pages}\n"
            f"  Words: {total_words}\n"
            f"  Last ingest: {last_ingest or 'never'}\n"
            f"  Directory: {self._wiki_dir}"
        )

    def get_index_text(self) -> str:
        """Return the wiki index text for system prompt injection."""
        return self._load_index()
