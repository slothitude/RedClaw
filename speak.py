#!/usr/bin/env python3
"""Quick script to use TTS via MCP server."""
import asyncio
from redclaw.mcp_client import MCPClient, MCPServerConfig

async def main():
    # Connect to TTS server
    client = MCPClient(servers=[
        MCPServerConfig(name="tts", url="http://localhost:8006/sse")
    ])
    
    # Discover tools
    await client.discover()
    print(f"Available tools: {[t.name for t in client.tools]}")
    
    # Call text_to_speech
    result = await client.call_tool("text_to_speech", {
        "text": "Hello! I am RedClaw, your AI coding agent. I can help you with software engineering tasks.",
        "language": "en"
    })
    
    print(f"Result: {result[:100]}...")
    
    # Decode and play the audio
    import base64
    import tempfile
    import os
    
    # Extract base64 audio from result
    audio_data = base64.b64decode(result)
    
    # Save to temp file and play
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio_data)
        tmp_path = tmp.name
    
    print(f"Playing audio from {tmp_path}")
    os.system(f'powershell -c "(New-Object Media.SoundPlayer \"{tmp_path}\").PlaySync()"')
    os.unlink(tmp_path)
    
    await client.close()

if __name__ == "__main__":
    asyncio.run(main())
