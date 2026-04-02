"""TTS MCP Server — Coqui XTTS-v2 with voice cloning over MCP SSE.

Runs under Python 3.11 venv (.venv311) because Coqui TTS doesn't support 3.13.
Supports voice cloning: place reference WAV files in redclaw/voices/.
Falls back to edge-tts if TTS import fails.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import logging
import os
import tempfile
from pathlib import Path

# Accept Coqui CPML license non-commercial use
os.environ.setdefault("COQUI_TOS_AGREED", "1")

logging.basicConfig(level=logging.INFO, format="%(asctime)s TTS %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# Patch torch.load for PyTorch 2.6+ compat (Coqui uses pickle checkpoints)
try:
    import torch
    _orig_load = torch.load
    def _patched_load(*a, **kw):
        kw.setdefault("weights_only", False)
        return _orig_load(*a, **kw)
    torch.load = _patched_load
except ImportError:
    pass

# Voices directory for reference audio files (voice cloning)
VOICES_DIR = Path(__file__).parent.parent / "voices"

# Supported XTTS languages
XTTS_LANGUAGES = [
    "en", "es", "fr", "de", "it", "pt", "pl", "tr", "ru",
    "nl", "cs", "ar", "zh-cn", "ja", "hu", "ko",
]

# Try Coqui TTS first, fall back to edge-tts
_USE_COQUI = False
try:
    import TTS
    from TTS.api import TTS as CoquiTTS
    _USE_COQUI = True
    logger.info("Coqui TTS available")
except ImportError:
    logger.warning("Coqui TTS not available, falling back to edge-tts")

from fastmcp.server.server import FastMCP
mcp = FastMCP("redclaw-tts")

# Lazy-loaded models
_coqui_model = None


def _get_coqui_model():
    """Load XTTS-v2 model (lazy, ~1.8GB download on first use)."""
    global _coqui_model
    if _coqui_model is None:
        # Patch torch.load for PyTorch 2.6+ weights_only default
        import torch
        _orig_load = torch.load
        def _patched_load(*a, **kw):
            kw.setdefault("weights_only", False)
            return _orig_load(*a, **kw)
        torch.load = _patched_load

        logger.info("Loading Coqui XTTS-v2 model (CPU)...")
        _coqui_model = CoquiTTS("tts_models/multilingual/multi-dataset/xtts_v2")
        logger.info("XTTS-v2 model loaded.")
    return _coqui_model


def _get_voice_path(speaker_wav: str) -> str | None:
    """Resolve a speaker_wav name to a file path in voices/ dir."""
    if not speaker_wav:
        return None
    # If it's already a full path, use it directly
    if os.path.isabs(speaker_wav) and os.path.isfile(speaker_wav):
        return speaker_wav
    # Look in voices/ directory
    voices_dir = VOICES_DIR
    voices_dir.mkdir(parents=True, exist_ok=True)
    # Try exact name, then with extensions
    for candidate in [speaker_wav, f"{speaker_wav}.wav", f"{speaker_wav}.mp3"]:
        path = voices_dir / candidate
        if path.exists():
            return str(path)
    return None


@mcp.tool()
def text_to_speech(text: str, language: str = "en", speaker_wav: str = "") -> str:
    """Convert text to speech audio, returned as base64-encoded WAV.

    Uses Coqui XTTS-v2 for voice cloning if a speaker_wav reference is provided.
    Otherwise generates with default XTTS voice.

    Args:
        text: The text to speak.
        language: Language code (en, es, fr, de, it, pt, pl, tr, ru, nl, cs, ar, zh-cn, ja, hu, ko).
        speaker_wav: Reference audio filename in voices/ dir (for voice cloning), or full path.

    Returns:
        Base64-encoded WAV audio data.
    """
    if _USE_COQUI:
        return _coqui_tts(text, language, speaker_wav)
    else:
        return _edge_tts_sync(text, language)


def _coqui_tts(text: str, language: str, speaker_wav: str) -> str:
    """Generate TTS using Coqui XTTS-v2."""
    model = _get_coqui_model()
    voice_path = _get_voice_path(speaker_wav)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        if voice_path:
            # Voice cloning mode
            model.tts_to_file(
                text=text,
                language=language,
                speaker_wav=voice_path,
                file_path=tmp_path,
            )
        else:
            # Default voice mode
            model.tts_to_file(
                text=text,
                language=language,
                file_path=tmp_path,
            )

        with open(tmp_path, "rb") as f:
            audio_bytes = f.read()

        if not audio_bytes:
            return "Error: no audio generated"
        return base64.b64encode(audio_bytes).decode("ascii")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _edge_tts_sync(text: str, language: str) -> str:
    """Fallback: generate TTS using edge-tts (synchronous wrapper)."""
    import edge_tts

    LANG_VOICES = {
        "en": "en-US-AriaNeural", "es": "es-ES-ElviraNeural",
        "fr": "fr-FR-DeniseNeural", "de": "de-DE-KatjaNeural",
        "it": "it-IT-ElsaNeural", "pt": "pt-BR-FranciscaNeural",
        "ru": "ru-RU-SvetlanaNeural", "zh": "zh-CN-XiaoxiaoNeural",
        "zh-cn": "zh-CN-XiaoxiaoNeural", "ja": "ja-JP-NanamiNeural",
        "ko": "ko-KR-SunHiNeural", "ar": "ar-SA-ZariyahNeural",
        "hi": "hi-IN-SwaraNeural",
    }
    voice = LANG_VOICES.get(language.lower(), "en-US-AriaNeural")

    # edge-tts is async, run in event loop
    async def _gen():
        communicate = edge_tts.Communicate(text, voice)
        buf = io.BytesIO()
        await communicate.stream_to_buffer(buf)
        return buf.getvalue()

    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                audio_bytes = pool.submit(asyncio.run, _gen()).result()
        else:
            audio_bytes = loop.run_until_complete(_gen())
    except RuntimeError:
        audio_bytes = asyncio.run(_gen())

    if not audio_bytes:
        return "Error: no audio generated"
    return base64.b64encode(audio_bytes).decode("ascii")


@mcp.tool()
def list_voices() -> str:
    """List available reference voice files in the voices/ directory.

    Returns:
        List of voice file names available for voice cloning.
    """
    VOICES_DIR.mkdir(parents=True, exist_ok=True)
    voices = sorted(VOICES_DIR.iterdir()) if VOICES_DIR.exists() else []
    wav_files = [f.name for f in voices if f.suffix in (".wav", ".mp3", ".ogg")]
    if not wav_files:
        return "No voice files found. Place WAV files in: " + str(VOICES_DIR)
    return f"{len(wav_files)} voice(s):\n" + "\n".join(f"  - {v}" for v in wav_files)


@mcp.tool()
def get_languages() -> str:
    """List supported languages for TTS.

    Returns:
        Supported language codes.
    """
    if _USE_COQUI:
        return "XTTS-v2 languages: " + ", ".join(XTTS_LANGUAGES)
    return "edge-tts: 400+ voices in 100+ languages. Use language code (en, es, fr, etc.)"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RedClaw TTS MCP Server")
    parser.add_argument("--port", type=int, default=8006, help="Port to run on (default: 8006)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    logger.info(f"Starting TTS MCP server on port {args.port} (Coqui={'yes' if _USE_COQUI else 'no'})")
    mcp.run(transport="sse", port=args.port, host="0.0.0.0")
