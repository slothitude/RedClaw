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
import re
import subprocess
import tempfile
import threading
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
        import torch
        torch.set_num_threads(os.cpu_count() or 8)
        logger.info(f"Torch threads: {torch.get_num_threads()}")
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


@mcp.tool()
def speak(text: str, language: str = "en", speaker_wav: str = "") -> str:
    """Generate speech and play it immediately via streaming.

    Splits text into sentences and generates+plays each one in a pipeline
    so audio starts playing within seconds rather than waiting for full generation.
    Returns immediately — audio plays in the background.

    Args:
        text: The text to speak.
        language: Language code (en, es, fr, de, it, pt, pl, tr, ru, nl, cs, ar, zh-cn, ja, hu, ko).
        speaker_wav: Reference audio filename in voices/ dir (for voice cloning), or full path.

    Returns:
        Status message. Audio plays in background.
    """
    if not text.strip():
        return "Error: empty text"
    voice_path = _get_voice_path(speaker_wav) or ""
    # Start streaming in background thread
    t = threading.Thread(
        target=_stream_and_play,
        args=(text, language, voice_path),
        daemon=True,
    )
    t.start()
    preview = text[:60] + ("..." if len(text) > 60 else "")
    return f"Streaming: {preview}"


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences for streaming generation."""
    # Split on sentence-ending punctuation followed by space or end
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    # Merge very short fragments with the previous one
    sentences = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if sentences and len(part) < 20:
            sentences[-1] += " " + part
        else:
            sentences.append(part)
    return sentences or [text]


def _play_wav(path: str):
    """Play a WAV file using ffplay (non-blocking, waits for completion)."""
    try:
        subprocess.run(
            ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", str(path)],
            timeout=30,
        )
    except FileNotFoundError:
        # Fallback: Windows powershell
        ps_cmd = (
            f"(New-Object Media.SoundPlayer '{path}').PlaySync()"
        )
        subprocess.run(
            ["powershell.exe", "-Command", ps_cmd],
            timeout=30,
        )
    except Exception as e:
        logger.error(f"Playback error: {e}")


def _generate_sentence(model, sentence: str, language: str, voice_path: str) -> str:
    """Generate a single sentence to a temp WAV file, returns path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=tempfile.gettempdir())
    tmp_path = tmp.name
    tmp.close()
    try:
        if voice_path:
            model.tts_to_file(
                text=sentence, language=language,
                speaker_wav=voice_path, file_path=tmp_path,
            )
        else:
            model.tts_to_file(
                text=sentence, language=language, file_path=tmp_path,
            )
        return tmp_path
    except Exception as e:
        logger.error(f"TTS generation error: {e}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return ""


def _stream_and_play(text: str, language: str, voice_path: str):
    """Pipeline: generate sentence N+1 while sentence N plays."""
    if not _USE_COQUI:
        # edge-tts fallback — generate full audio then play
        _stream_edge_fallback(text, language)
        return

    model = _get_coqui_model()
    sentences = _split_sentences(text)
    if not sentences:
        return

    logger.info(f"Streaming {len(sentences)} sentence(s)...")

    # Generate first sentence
    current_wav = _generate_sentence(model, sentences[0], language, voice_path)
    temp_files = [current_wav]

    for i in range(len(sentences)):
        if not current_wav or not os.path.exists(current_wav):
            break

        # Start generating next sentence in background (if there is one)
        next_wav = ""
        if i + 1 < len(sentences):
            next_wav = _generate_sentence(
                model, sentences[i + 1], language, voice_path
            )
            temp_files.append(next_wav)

        # Play current sentence (blocks until playback finishes)
        logger.info(f"Playing sentence {i + 1}/{len(sentences)}")
        _play_wav(current_wav)

        current_wav = next_wav

    # Cleanup temp files
    for f in temp_files:
        try:
            os.unlink(f)
        except OSError:
            pass
    logger.info("Streaming playback complete.")


def _stream_edge_fallback(text: str, language: str):
    """Fallback streaming using edge-tts."""
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

    tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    tmp_path = tmp.name
    tmp.close()

    try:
        async def _gen():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(tmp_path)

        asyncio.run(_gen())
        _play_wav(tmp_path)
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RedClaw TTS MCP Server")
    parser.add_argument("--port", type=int, default=8006, help="Port to run on (default: 8006)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    logger.info(f"Starting TTS MCP server on port {args.port} (Coqui={'yes' if _USE_COQUI else 'no'})")
    mcp.run(transport="sse", port=args.port, host="0.0.0.0")
