"""WebChat interface — HTTP/WebSocket server for browser-based chat."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Any

from aiohttp import web, WSMsgType

from redclaw.api.client import LLMClient
from redclaw.api.providers import get_provider
from redclaw.api.types import Usage
from redclaw.runtime.conversation import ConversationCallbacks, ConversationRuntime
from redclaw.runtime.permissions import PermissionMode, PermissionPolicy
from redclaw.runtime.session import Session
from redclaw.runtime.usage import UsageTracker
from redclaw.tools.registry import ToolExecutor

logger = logging.getLogger(__name__)

HTML_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>RedClaw Chat</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,sans-serif;background:#1a1a2e;color:#e0e0e0;height:100vh;display:flex;flex-direction:column}
#header{padding:12px 16px;background:#16213e;border-bottom:1px solid #0f3460;display:flex;justify-content:space-between;align-items:center}
#header h1{font-size:16px;color:#e94560}
#status{font-size:12px;color:#888}
#messages{flex:1;overflow-y:auto;padding:16px;display:flex;flex-direction:column;gap:8px}
.msg{max-width:80%;padding:10px 14px;border-radius:12px;white-space:pre-wrap;font-size:14px;line-height:1.5}
.msg.user{align-self:flex-end;background:#0f3460;color:#e0e0e0}
.msg.assistant{align-self:flex-start;background:#16213e;color:#e0e0e0;border:1px solid #0f3460}
.msg.system{align-self:center;background:transparent;color:#888;font-size:12px;font-style:italic}
.msg.error{color:#e94560}
#input-area{padding:12px 16px;background:#16213e;border-top:1px solid #0f3460;display:flex;gap:8px}
#input{flex:1;padding:10px 14px;border-radius:20px;border:1px solid #0f3460;background:#1a1a2e;color:#e0e0e0;font-size:14px;outline:none}
#input:focus{border-color:#e94560}
#send{padding:10px 20px;border-radius:20px;border:none;background:#e94560;color:#fff;cursor:pointer;font-size:14px}
#send:hover{background:#c73e54}
#send:disabled{opacity:0.5;cursor:not-allowed}
.typing::after{content:"...";animation:dots 1s steps(3) infinite}
@keyframes dots{0%{content:"."}33%{content:".."}66%{content:"..."}}
</style></head><body>
<div id="header"><h1>RedClaw</h1><span id="status">connected</span></div>
<div id="messages"></div>
<div id="input-area">
<input id="input" placeholder="Type a message..." autofocus>
<button id="send">Send</button>
</div>
<script>
const msgs = document.getElementById('messages');
const input = document.getElementById('input');
const sendBtn = document.getElementById('send');
const status = document.getElementById('status');
let ws;
function connect() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  ws = new WebSocket(proto + '//' + location.host + '/ws');
  ws.onopen = () => { status.textContent = 'connected'; };
  ws.onclose = () => { status.textContent = 'disconnected'; setTimeout(connect, 2000); };
  ws.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (data.type === 'text_delta') {
      if (!msgs.lastChild || !msgs.lastChild.classList.contains('assistant') || msgs.lastChild.dataset.streaming !== '1') {
        const div = document.createElement('div');
        div.className = 'msg assistant';
        div.dataset.streaming = '1';
        msgs.appendChild(div);
      }
      msgs.lastChild.textContent += data.text;
    } else if (data.type === 'done') {
      if (msgs.lastChild && msgs.lastChild.dataset.streaming === '1') {
        msgs.lastChild.dataset.streaming = '0';
        msgs.lastChild.classList.remove('typing');
      }
      sendBtn.disabled = false;
    } else if (data.type === 'tool_call') {
      const div = document.createElement('div');
      div.className = 'msg system';
      div.textContent = 'Tool: ' + data.name;
      msgs.appendChild(div);
    } else if (data.type === 'error') {
      const div = document.createElement('div');
      div.className = 'msg system error';
      div.textContent = 'Error: ' + data.message;
      msgs.appendChild(div);
      sendBtn.disabled = false;
    }
    msgs.scrollTop = msgs.scrollHeight;
  };
}
function sendMessage() {
  const text = input.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;
  const div = document.createElement('div');
  div.className = 'msg user';
  div.textContent = text;
  msgs.appendChild(div);
  ws.send(JSON.stringify({type: 'message', text: text}));
  input.value = '';
  sendBtn.disabled = true;
}
sendBtn.onclick = sendMessage;
input.onkeydown = (e) => { if (e.key === 'Enter') sendMessage(); };
connect();
</script></body></html>"""


class WebChatSession:
    """Per-WebSocket session state."""

    def __init__(self, working_dir: str, provider_name: str, model: str,
                 base_url: str | None, perm_mode: str):
        self.provider = get_provider(provider_name, base_url)
        self.client = LLMClient(self.provider)
        self.session = Session(id=f"web-{uuid.uuid4().hex[:8]}")
        self.session.model = model
        self.session.provider = provider_name
        self.session.working_dir = working_dir
        self.model = model
        self.provider_name = provider_name

        self.tools = ToolExecutor(working_dir=working_dir)
        self.policy = PermissionPolicy(mode=PermissionMode(perm_mode))
        self.tracker = UsageTracker()

        self.rt = ConversationRuntime(
            client=self.client,
            provider=self.provider,
            model=model,
            session=self.session,
            tools=self.tools,
            permission_policy=self.policy,
            usage_tracker=self.tracker,
            working_dir=working_dir,
        )
        self.current_task: asyncio.Task | None = None

    async def close(self) -> None:
        await self.client.close()


async def run_webchat(
    provider_name: str,
    model: str,
    base_url: str | None,
    perm_mode: str,
    working_dir: str | None,
    port: int = 8080,
) -> None:
    """Run the WebChat HTTP/WebSocket server."""
    cwd = working_dir or str(Path.cwd())
    sessions: dict[str, WebChatSession] = {}

    async def _get_session(ws_id: str) -> WebChatSession:
        if ws_id not in sessions:
            sessions[ws_id] = WebChatSession(cwd, provider_name, model, base_url, perm_mode)
        return sessions[ws_id]

    async def handle_index(req: web.Request) -> web.Response:
        return web.Response(text=HTML_PAGE, content_type="text/html")

    async def handle_ws(req: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(req)
        ws_id = uuid.uuid4().hex[:8]
        s = await _get_session(ws_id)

        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue

                if data.get("type") == "message":
                    text = data.get("text", "")
                    if not text:
                        continue

                    if s.current_task and not s.current_task.done():
                        await ws.send_json({"type": "error", "message": "Still processing"})
                        continue

                    async def _process(text: str, ws_resp: web.WebSocketResponse, session: WebChatSession) -> None:
                        async def on_text_delta(t: str) -> None:
                            await ws_resp.send_json({"type": "text_delta", "text": t})

                        async def on_tool_begin(tid: str, name: str, inp: str) -> None:
                            await ws_resp.send_json({"type": "tool_call", "name": name})

                        async def on_tool_result(tid: str, result: str, is_error: bool) -> None:
                            pass

                        async def on_usage(u: Usage) -> None:
                            pass

                        async def on_error(m: str) -> None:
                            await ws_resp.send_json({"type": "error", "message": m})

                        cb = ConversationCallbacks(
                            on_text_delta=on_text_delta,
                            on_tool_begin=on_tool_begin,
                            on_tool_result=on_tool_result,
                            on_usage=on_usage,
                            on_error=on_error,
                        )
                        await session.rt.run_turn(text, cb)
                        await ws_resp.send_json({"type": "done"})

                    s.current_task = asyncio.create_task(_process(text, ws, s))

                elif data.get("type") == "abort":
                    s.rt.abort()

            elif msg.type == WSMsgType.ERROR:
                logger.error(f"WebSocket error: {ws.exception()}")

        # Cleanup
        if ws_id in sessions:
            await sessions.pop(ws_id).close()
        return ws

    async def handle_upload(req: web.Request) -> web.Response:
        reader = await req.multipart()
        upload_dir = Path(cwd) / "uploads"
        upload_dir.mkdir(parents=True, exist_ok=True)

        field = await reader.next()
        if field is None:
            return web.json_response({"error": "No file"}, status=400)

        filename = field.filename or "upload"
        dest = upload_dir / filename
        with open(dest, "wb") as f:
            while True:
                chunk = await field.read_chunk()
                if not chunk:
                    break
                f.write(chunk)

        return web.json_response({"ok": True, "path": str(dest), "name": filename})

    async def handle_download(req: web.Request) -> web.Response:
        rel_path = req.query.get("path", "")
        file_path = Path(cwd) / rel_path
        try:
            file_path.resolve().relative_to(Path(cwd).resolve())
        except ValueError:
            return web.json_response({"error": "Invalid path"}, status=400)
        if not file_path.is_file():
            return web.json_response({"error": "Not found"}, status=404)
        return web.FileResponse(file_path)

    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/ws", handle_ws)
    app.router.add_post("/upload", handle_upload)
    app.router.add_get("/download", handle_download)

    logger.info(f"WebChat starting on port {port}")
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

    # Keep running
    try:
        while True:
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await runner.cleanup()
        for s in sessions.values():
            await s.close()
