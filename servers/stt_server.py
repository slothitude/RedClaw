"""STT MCP Server — Whisper-based speech-to-text over MCP SSE."""

from __future__ import annotations

import argparse
import base64
import io
import logging
import os
import tempfile
from pathlib import Path

import torch
import whisper
from fastmcp.server.server import FastMCP

logging.basicConfig(level=logging.INFO, format="%(asctime)s STT %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

mcp = FastMCP("redclaw-stt")

# Lazy-loaded model
_model = None
_device = "cpu"


def _get_model():
    global _model
    if _model is None:
        logger.info("Loading Whisper base model on CPU...")
        _model = whisper.load_model("base", device=_device)
        logger.info("Whisper model loaded.")
    return _model


@mcp.tool()
def transcribe_file(file_path: str, language: str = "") -> str:
    """Transcribe an audio file to text.

    Args:
        file_path: Path to the audio file (wav, mp3, ogg, etc.)
        language: Language code (e.g. 'en', 'es'). Auto-detect if empty.

    Returns:
        Transcribed text.
    """
    model = _get_model()
    path = Path(file_path)
    if not path.exists():
        return f"Error: file not found: {file_path}"
    opts = {}
    if language:
        opts["language"] = language
    result = model.transcribe(str(path), **opts)
    return result.get("text", "")


@mcp.tool()
def transcribe_base64(audio_data: str, format: str = "wav", language: str = "") -> str:
    """Transcribe base64-encoded audio data to text.

    Args:
        audio_data: Base64-encoded audio bytes.
        format: Audio format (wav, mp3, ogg, etc.).
        language: Language code (e.g. 'en'). Auto-detect if empty.

    Returns:
        Transcribed text.
    """
    model = _get_model()
    try:
        raw = base64.b64decode(audio_data)
    except Exception as e:
        return f"Error decoding base64: {e}"

    suffix = f".{format}" if format else ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        tmp_path = tmp.name

    try:
        opts = {}
        if language:
            opts["language"] = language
        result = model.transcribe(tmp_path, **opts)
        return result.get("text", "")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


@mcp.tool()
def get_model_info() -> str:
    """Return info about the loaded Whisper model and device."""
    model = _get_model()
    return f"Whisper base model, device=cpu, torch={torch.__version__}"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RedClaw STT MCP Server")
    parser.add_argument("--port", type=int, default=8007, help="Port to run on (default: 8007)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    logger.info(f"Starting STT MCP server on port {args.port}")
    mcp.run(transport="sse", port=args.port, host="0.0.0.0")
