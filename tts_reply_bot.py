#!/usr/bin/env python3
"""Telegram bot that replies with TTS audio."""
import asyncio
import edge_tts
import tempfile
import os
import logging
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ.get("REDCLAW_TELEGRAM_TOKEN")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def text_to_speech(text: str, voice: str = "en-US-AriaNeural") -> str:
    """Generate TTS audio file and return path."""
    communicate = edge_tts.Communicate(text, voice)
    
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    
    await communicate.save(tmp_path)
    return tmp_path

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle incoming message and reply with TTS."""
    text = update.message.text or ""
    user_id = update.effective_user.id
    
    logger.info(f"Message from {user_id}: {text}")
    
    if not text.strip():
        return
    
    # React with processing indicator
    try:
        await update.message.set_reaction("⚡")
    except Exception:
        pass
    
    # Generate TTS
    try:
        audio_path = await text_to_speech(text)
        
        # Send as voice message
        with open(audio_path, "rb") as audio_file:
            await update.message.reply_voice(
                voice=audio_file,
                caption=f"🔊 {text[:100]}{'...' if len(text) > 100 else ''}"
            )
        
        # Update reaction
        try:
            await update.message.set_reaction("✅")
        except Exception:
            pass
        
        # Clean up
        try:
            os.unlink(audio_path)
        except Exception:
            pass
            
    except Exception as e:
        logger.error(f"Error: {e}")
        await update.message.reply_text(f"Error: {e}")

async def main():
    if not TELEGRAM_TOKEN:
        print("Error: REDCLAW_TELEGRAM_TOKEN not set")
        return
    
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("TTS Reply Bot starting... Send a message to get TTS audio back!")
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    
    try:
        while True:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()

if __name__ == "__main__":
    asyncio.run(main())
