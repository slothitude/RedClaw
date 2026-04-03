#!/usr/bin/env python3
"""Send TTS audio via Telegram."""
import asyncio
import edge_tts
import tempfile
import os
from telegram import Bot

TELEGRAM_TOKEN = os.environ.get("REDCLAW_TELEGRAM_TOKEN")
TELEGRAM_USER_ID = int(os.environ.get("REDCLAW_TELEGRAM_USER_ID", "0"))

async def send_tts_message(text: str, voice: str = "en-US-AriaNeural"):
    """Generate TTS and send as voice message via Telegram."""
    if not TELEGRAM_TOKEN:
        print("Error: REDCLAW_TELEGRAM_TOKEN not set")
        return
    
    if not TELEGRAM_USER_ID:
        print("Error: REDCLAW_TELEGRAM_USER_ID not set")
        return
    
    print(f"Generating TTS: {text}")
    
    # Generate audio
    communicate = edge_tts.Communicate(text, voice)
    
    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    
    await communicate.save(tmp_path)
    print(f"Audio saved to: {tmp_path}")
    
    # Send via Telegram
    bot = Bot(token=TELEGRAM_TOKEN)
    
    with open(tmp_path, "rb") as audio_file:
        await bot.send_voice(
            chat_id=TELEGRAM_USER_ID,
            voice=audio_file,
            caption=text
        )
    
    print("Audio sent via Telegram!")
    
    # Clean up
    try:
        os.unlink(tmp_path)
    except:
        pass

if __name__ == "__main__":
    text = "Hello! This is a test message from RedClaw using text to speech."
    asyncio.run(send_tts_message(text))
