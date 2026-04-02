"""Web Reader MCP Server — Playwright-based page fetcher over MCP SSE.

Renders web pages with a headless browser and returns clean markdown/text.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import logging
from typing import Any

from fastmcp.server.server import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s READER %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("redclaw-web-reader")

_browser = None


async def _get_browser():
    """Lazy-init a persistent browser instance."""
    global _browser
    if _browser is None or not _browser.is_connected():
        from playwright.async_api import async_playwright
        pw = await async_playwright().start()
        _browser = await pw.chromium.launch(headless=True)
    return _browser


async def _fetch_page(url: str) -> tuple[str, str]:
    """Fetch a page, return (html, text_content)."""
    browser = await _get_browser()
    page = await browser.new_page()
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        html = await page.content()
        text = await page.evaluate("() => document.body.innerText")
        return html, text or ""
    finally:
        await page.close()


def _html_to_markdown(html: str) -> str:
    """Convert HTML to markdown using html2text."""
    import html2text
    h = html2text.HTML2Text()
    h.ignore_links = False
    h.ignore_images = True
    h.body_width = 0  # no wrapping
    return h.handle(html)


def _extract_links(html: str) -> list[dict[str, str]]:
    """Extract links from HTML using a simple regex-free approach."""
    from html.parser import HTMLParser

    class LinkExtractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.links = []

        def handle_starttag(self, tag, attrs):
            if tag == "a":
                href = text = None
                for attr, val in attrs:
                    if attr == "href":
                        href = val
                if href:
                    self.links.append({"url": href})

    parser = LinkExtractor()
    parser.feed(html)
    return parser.links[:100]  # cap at 100


@mcp.tool()
async def read_webpage(url: str, output_format: str = "markdown") -> str:
    """Fetch and render a web page, returning its content.

    Args:
        url: The URL to fetch.
        output_format: 'markdown' or 'text'. Default is 'markdown'.

    Returns:
        Page content in the requested format.
    """
    html, text = await _fetch_page(url)
    if output_format == "text":
        return text[:50000]  # cap output
    md = _html_to_markdown(html)
    return md[:50000]


@mcp.tool()
async def read_webpage_with_links(url: str) -> str:
    """Fetch a web page and return content with extracted links.

    Args:
        url: The URL to fetch.

    Returns:
        Page content as markdown followed by a links section.
    """
    html, text = await _fetch_page(url)
    md = _html_to_markdown(html)
    links = _extract_links(html)
    links_md = "\n\n---\n## Links\n"
    for i, link in enumerate(links, 1):
        links_md += f"\n{i}. {link['url']}"
    return (md + links_md)[:60000]


@mcp.tool()
async def screenshot(url: str) -> str:
    """Take a screenshot of a web page.

    Args:
        url: The URL to screenshot.

    Returns:
        Base64-encoded PNG screenshot.
    """
    browser = await _get_browser()
    page = await browser.new_page(viewport={"width": 1280, "height": 720})
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        screenshot_bytes = await page.screenshot(full_page=False, type="png")
        return base64.b64encode(screenshot_bytes).decode("ascii")
    finally:
        await page.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RedClaw Web Reader MCP Server")
    parser.add_argument("--port", type=int, default=8003, help="Port to run on (default: 8003)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    logger.info(f"Starting Web Reader MCP server on port {args.port}")
    mcp.run(transport="sse", port=args.port, host="0.0.0.0")
