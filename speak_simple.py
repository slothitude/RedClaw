#!/usr/bin/env python3
"""Simple TTS using edge-tts directly."""
import asyncio
import edge_tts
import tempfile
import os
import time

async def main():
    text = "Greetings! I am speaking to you using text to speech. This is a demonstration of the edge TTS system. How can I help you code today?"
    voice = "en-US-AriaNeural"
    
    print(f"Speaking: {text}")
    
    # Generate audio
    communicate = edge_tts.Communicate(text, voice)
    
    # Save to temp file
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
        tmp_path = tmp.name
    
    await communicate.save(tmp_path)
    
    # Play audio using PowerShell
    print(f"Playing audio...")
    ps_script = f'''
    Add-Type -AssemblyName presentationCore
    $player = New-Object System.Windows.Media.MediaPlayer
    $player.Open("{tmp_path}")
    Start-Sleep -Milliseconds 500
    $player.Play()
    Start-Sleep -Seconds 8
    $player.Close()
    '''
    result = os.system(f'powershell -c "{ps_script}"')
    
    # Clean up
    try:
        time.sleep(1)
        os.unlink(tmp_path)
    except:
        pass

if __name__ == "__main__":
    asyncio.run(main())
