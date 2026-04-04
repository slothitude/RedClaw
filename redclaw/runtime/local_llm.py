"""Local LLM backend using bitnet.cpp for token-free inference.

Runs BitNet model locally via subprocess. No API needed.
Used by TokenSaver for SKIP/COMPRESS routing decisions and
tool call predictions.

Two modes:
1. bitnet.cpp CLI: runs llama-cli as subprocess (~50ms on modern CPU)
2. bitnet.cpp server: OpenAI-compatible API via llama-server (faster for repeated calls)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BitNetConfig:
    """Configuration for local BitNet inference."""
    model_path: Path
    bitnet_bin: Path | None = None  # Auto-detected if None
    threads: int = 4
    ctx_size: int = 2048
    temperature: float = 0.1  # Low temp for deterministic predictions
    server_mode: bool = False
    server_url: str = "http://127.0.0.1:8080"


class LocalBitNet:
    """BitNet b1.58 inference via bitnet.cpp.

    Supports two backends:
    - CLI mode: runs llama-cli subprocess for each prediction
    - Server mode: connects to running llama-server (OpenAI-compatible API)
    """

    def __init__(self, config: BitNetConfig) -> None:
        self.config = config
        self._server_session: Any = None

        if config.bitnet_bin is None:
            # Try to find bitnet.cpp binary
            self._find_binary()

    def _find_binary(self) -> None:
        """Try to locate the bitnet.cpp binary."""
        # Common locations
        search_paths = [
            Path.home() / "BitNet" / "build" / "bin",
            Path.cwd() / "BitNet" / "build" / "bin",
            Path("/usr/local/bin"),
        ]

        binary_names = ["llama-cli", "llama-cli.exe", "main", "main.exe"]

        for search_dir in search_paths:
            if not search_dir.is_dir():
                continue
            for name in binary_names:
                candidate = search_dir / name
                if candidate.is_file():
                    self.config.bitnet_bin = candidate
                    logger.info("Found bitnet.cpp binary: %s", candidate)
                    return

        logger.warning("bitnet.cpp binary not found. Set --bitnet-bin or add to PATH.")

    async def predict_tool(self, prompt: str) -> tuple[str, float]:
        """Predict next tool from prompt. Returns (tool_name, confidence).

        Runs bitnet.cpp as subprocess, parses output.
        ~50ms on modern CPU (2B model).
        """
        if self.config.server_mode:
            return await self._predict_server(prompt)
        else:
            return await self._predict_cli(prompt)

    async def _predict_cli(self, prompt: str) -> tuple[str, float]:
        """Predict via CLI subprocess."""
        if not self.config.bitnet_bin or not self.config.bitnet_bin.is_file():
            return ("unknown", 0.0)

        cmd = [
            str(self.config.bitnet_bin),
            "-m", str(self.config.model_path),
            "-p", prompt,
            "-n", "8",  # Only need a few tokens for tool name
            "-t", str(self.config.threads),
            "-c", str(self.config.ctx_size),
            "--temp", str(self.config.temperature),
            "--no-warmup",
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)

            output = stdout.decode("utf-8", errors="replace").strip()

            # Parse the generated text to extract tool name
            tool_name, confidence = self._parse_tool_output(output)
            return (tool_name, confidence)

        except asyncio.TimeoutError:
            logger.warning("BitNet CLI timeout")
            return ("unknown", 0.0)
        except Exception as e:
            logger.warning("BitNet CLI error: %s", e)
            return ("unknown", 0.0)

    async def _predict_server(self, prompt: str) -> tuple[str, float]:
        """Predict via server API (OpenAI-compatible)."""
        try:
            import httpx
        except ImportError:
            return await self._predict_cli(prompt)

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(
                    f"{self.config.server_url}/v1/completions",
                    json={
                        "prompt": prompt,
                        "max_tokens": 8,
                        "temperature": self.config.temperature,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    text = data.get("choices", [{}])[0].get("text", "").strip()
                    return self._parse_tool_output(text)
                else:
                    logger.warning("Server error: %d", response.status_code)
                    return ("unknown", 0.0)
        except Exception as e:
            logger.warning("Server request failed: %s", e)
            return ("unknown", 0.0)

    async def generate(self, prompt: str, max_tokens: int = 128) -> str:
        """Generate text from prompt."""
        if self.config.server_mode:
            return await self._generate_server(prompt, max_tokens)
        else:
            return await self._generate_cli(prompt, max_tokens)

    async def _generate_cli(self, prompt: str, max_tokens: int) -> str:
        """Generate via CLI subprocess."""
        if not self.config.bitnet_bin or not self.config.bitnet_bin.is_file():
            return ""

        cmd = [
            str(self.config.bitnet_bin),
            "-m", str(self.config.model_path),
            "-p", prompt,
            "-n", str(max_tokens),
            "-t", str(self.config.threads),
            "-c", str(self.config.ctx_size),
            "--temp", str(self.config.temperature),
        ]

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
            return stdout.decode("utf-8", errors="replace").strip()
        except Exception as e:
            logger.warning("BitNet generate error: %s", e)
            return ""

    async def _generate_server(self, prompt: str, max_tokens: int) -> str:
        """Generate via server API."""
        try:
            import httpx
        except ImportError:
            return await self._generate_cli(prompt, max_tokens)

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.config.server_url}/v1/completions",
                    json={
                        "prompt": prompt,
                        "max_tokens": max_tokens,
                        "temperature": self.config.temperature,
                    },
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("choices", [{}])[0].get("text", "").strip()
                return ""
        except Exception:
            return ""

    def _parse_tool_output(self, output: str) -> tuple[str, float]:
        """Parse model output to extract tool name and confidence.

        Looks for known tool names in the output text.
        """
        from redclaw.training.encode import TOOL_VOCAB

        output_lower = output.lower()

        # Try exact match first
        for tool in TOOL_VOCAB:
            if tool in output_lower:
                # Simple confidence based on position (earlier = more confident)
                pos = output_lower.index(tool)
                confidence = max(0.3, 1.0 - pos * 0.1)
                return (tool, confidence)

        # Try partial match
        for tool in TOOL_VOCAB:
            tool_parts = tool.split("_")
            if all(part in output_lower for part in tool_parts):
                return (tool, 0.5)

        return ("unknown", 0.0)

    async def start_server(self) -> asyncio.subprocess.Process | None:
        """Start bitnet.cpp server in background."""
        if self.config.server_mode:
            return None  # Assume already running

        if not self.config.bitnet_bin:
            return None

        # Find llama-server binary (same directory as llama-cli)
        server_bin = self.config.bitnet_bin.parent / "llama-server"
        if not server_bin.is_file():
            server_bin = self.config.bitnet_bin.parent / "server"
        if not server_bin.is_file():
            logger.warning("llama-server binary not found")
            return None

        cmd = [
            str(server_bin),
            "-m", str(self.config.model_path),
            "-t", str(self.config.threads),
            "-c", str(self.config.ctx_size),
            "--port", self.config.server_url.split(":")[-1].rstrip("/"),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        logger.info("Started bitnet.cpp server (PID %d)", proc.pid)
        self.config.server_mode = True
        return proc
