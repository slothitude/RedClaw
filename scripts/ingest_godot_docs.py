"""Ingest Godot documentation into the RedClaw wiki.

Reads key Godot doc pages via the web reader MCP server (Playwright + html2text)
and ingests each into the wiki as LLM-compiled pages.

Usage:
    # Start web reader first (if not already running):
    python servers/web_reader_server.py --port 8003

    # Run ingestion:
    python scripts/ingest_godot_docs.py --provider zai --model glm-4-flash
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DOCS: list[dict[str, str]] = [
    # Priority 1: Core UI System
    {"slug": "control", "url": "https://docs.godotengine.org/en/stable/classes/class_control.html"},
    {"slug": "container", "url": "https://docs.godotengine.org/en/stable/classes/class_container.html"},
    {"slug": "hsplitcontainer", "url": "https://docs.godotengine.org/en/stable/classes/class_hsplitcontainer.html"},
    {"slug": "richtextlabel", "url": "https://docs.godotengine.org/en/stable/classes/class_richtextlabel.html"},
    {"slug": "bbcode_in_richtextlabel", "url": "https://docs.godotengine.org/en/stable/tutorials/ui/bbcode_in_richtextlabel.html"},
    {"slug": "panelcontainer", "url": "https://docs.godotengine.org/en/stable/classes/class_panelcontainer.html"},
    {"slug": "stylebox", "url": "https://docs.godotengine.org/en/stable/classes/class_styleboxflat.html"},
    {"slug": "scrollcontainer", "url": "https://docs.godotengine.org/en/stable/classes/class_scrollcontainer.html"},
    # Priority 2: Input Controls
    {"slug": "textedit", "url": "https://docs.godotengine.org/en/stable/classes/class_textedit.html"},
    {"slug": "lineedit", "url": "https://docs.godotengine.org/en/stable/classes/class_lineedit.html"},
    {"slug": "optionbutton", "url": "https://docs.godotengine.org/en/stable/classes/class_optionbutton.html"},
    {"slug": "itemlist", "url": "https://docs.godotengine.org/en/stable/classes/class_itemlist.html"},
    {"slug": "tabcontainer", "url": "https://docs.godotengine.org/en/stable/classes/class_tabcontainer.html"},
    # Priority 3: GDScript & Scenes
    {"slug": "gdscript_basics", "url": "https://docs.godotengine.org/en/stable/tutorials/scripting/gdscript/gdscript_basics.html"},
    {"slug": "json_class", "url": "https://docs.godotengine.org/en/stable/classes/class_json.html"},
    {"slug": "nodes_and_scenes", "url": "https://docs.godotengine.org/en/stable/tutorials/scripting/scenes_and_nodes.html"},
    {"slug": "theme_class", "url": "https://docs.godotengine.org/en/stable/classes/class_theme.html"},
    # Priority 4: Advanced Features
    {"slug": "codeedit", "url": "https://docs.godotengine.org/en/stable/classes/class_codeedit.html"},
    {"slug": "file_dialog", "url": "https://docs.godotengine.org/en/stable/classes/class_filedialog.html"},
    {"slug": "dir_access", "url": "https://docs.godotengine.org/en/stable/classes/class_diraccess.html"},
    {"slug": "os_class", "url": "https://docs.godotengine.org/en/stable/classes/class_os.html"},
]

WEB_READER_PORT = 8003


async def fetch_via_web_reader(url: str) -> str:
    """Fetch clean markdown from URL via the web reader MCP server."""
    import httpx

    async with httpx.AsyncClient(timeout=60) as client:
        # The web reader MCP server exposes tools via SSE, but we can use
        # a simpler HTTP approach — call the FastMCP SSE endpoint or just
        # use httpx directly. Since the MCP server uses SSE transport,
        # we'll fall back to direct httpx + html2text for simplicity.
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

    import html2text
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0
    return h.handle(html)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Godot docs into RedClaw wiki")
    parser.add_argument("--provider", default="zai", help="LLM provider")
    parser.add_argument("--model", default="glm-4-flash", help="LLM model")
    parser.add_argument("--base-url", default=None, help="LLM base URL")
    parser.add_argument("--wiki-dir", default=None, help="Wiki directory")
    args = parser.parse_args()

    from redclaw.api.client import LLMClient
    from redclaw.api.providers import get_provider
    from redclaw.wiki.manager import WikiManager

    provider = get_provider(args.provider, args.base_url)
    client = LLMClient(provider)
    mgr = WikiManager(
        wiki_dir=args.wiki_dir,
        client=client,
        provider=provider,
        model=args.model,
    )

    total = len(DOCS)
    for i, doc in enumerate(DOCS, 1):
        slug = doc["slug"]
        url = doc["url"]
        print(f"[{i}/{total}] Fetching {slug}...")

        try:
            markdown = await fetch_via_web_reader(url)
        except Exception as e:
            print(f"  ERROR fetching: {e}")
            continue

        if not markdown.strip():
            print(f"  SKIP: empty content")
            continue

        # Save to temp file and ingest as local source
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8", prefix=f"godot_{slug}_"
        ) as f:
            f.write(markdown)
            temp_path = f.name

        try:
            result = await mgr.ingest(temp_path, topic="godot")
            print(f"  {result}")
        except Exception as e:
            print(f"  ERROR ingesting: {e}")
        finally:
            Path(temp_path).unlink(missing_ok=True)

    print(f"\nDone! Stats: {mgr.stats()}")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
