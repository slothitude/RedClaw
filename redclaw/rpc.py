"""JSON-RPC over stdio mode — the bridge for the Godot app.

Protocol:
  Godot → Python: JSON-RPC request (single line)
  Python → Godot: JSONL events (one per line)

Requests:
  {"jsonrpc":"2.0","id":1,"method":"prompt","params":{"text":"..."}}
  {"jsonrpc":"2.0","id":2,"method":"abort"}
  {"jsonrpc":"2.0","id":3,"method":"new_session"}
  {"jsonrpc":"2.0","id":4,"method":"compact"}
  {"jsonrpc":"2.0","id":5,"method":"get_state"}
  {"jsonrpc":"2.0","id":6,"method":"set_model","params":{"model":"..."}}
  {"jsonrpc":"2.0","id":7,"method":"set_provider","params":{"provider":"...","base_url":"..."}}

Events (streamed):
  {"type":"text_delta","text":"..."}
  {"type":"tool_call","id":"...","name":"...","input":"..."}
  {"type":"tool_result","id":"...","result":"...","is_error":false}
  {"type":"usage","input_tokens":0,"output_tokens":0}
  {"type":"done","error":null}
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path
from typing import Any

from redclaw.api.client import LLMClient
from redclaw.api.providers import get_provider
from redclaw.api.types import Usage
from redclaw.runtime.compact import compact_session
from redclaw.runtime.conversation import ConversationCallbacks, ConversationRuntime
from redclaw.runtime.permissions import PermissionMode, PermissionPolicy
from redclaw.runtime.session import Session, load_session
from redclaw.runtime.usage import UsageTracker
from redclaw.tools.registry import ToolExecutor


def _emit(event: dict[str, Any]) -> None:
    """Write a JSONL event to stdout."""
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _reply(id: int | None, result: Any = None, error: Any = None) -> None:
    """Write a JSON-RPC reply."""
    resp: dict[str, Any] = {"jsonrpc": "2.0", "id": id}
    if error is not None:
        resp["error"] = {"code": -32000, "message": str(error)}
    else:
        resp["result"] = result
    _emit(resp)


async def _handle_sim_command(action: str, params: dict[str, Any], engine: Any) -> dict[str, Any]:
    """Dispatch simulation commands."""
    if action == "spawn_entity":
        props = params.get("properties", {})
        entity = engine.spawn_entity(
            entity_type=params.get("entity_type", "particle"),
            x=float(params.get("x", 0)),
            y=float(params.get("y", 0)),
            properties=props,
        )
        _emit({"type": "sim_entity_spawned", "entity_id": entity.entity_id, "entity_type": entity.entity_type, "x": entity.x, "y": entity.y})
        return {"entity_id": entity.entity_id, "x": entity.x, "y": entity.y}
    elif action == "remove_entity":
        ok = engine.remove_entity(params.get("entity_id", ""))
        _emit({"type": "sim_entity_removed", "entity_id": params.get("entity_id", "")})
        return {"removed": ok}
    elif action == "set_parameter":
        param = engine.set_parameter(params.get("name", ""), float(params.get("value", 0)))
        return {"name": param.name, "value": param.value}
    elif action == "query_state":
        return engine.query_state(
            filter_type=params.get("filter_type"),
            entity_id=params.get("entity_id"),
        )
    elif action == "apply_force":
        ok = engine.apply_force(params.get("entity_id", ""), float(params.get("fx", 0)), float(params.get("fy", 0)))
        return {"applied": ok}
    elif action == "start":
        return {"started": True}
    elif action == "stop":
        return {"stopped": True}
    elif action == "reset":
        engine.reset()
        _emit({"type": "sim_reset"})
        return {"reset": True}
    elif action == "get_metrics":
        metrics = engine.get_metrics()
        return {
            "total_entities": metrics.total_entities,
            "total_ticks": metrics.total_ticks,
            "stability_score": metrics.stability_score,
            "entity_types": metrics.entity_types,
        }
    else:
        return {"error": f"Unknown sim action: {action}"}


async def run_rpc(
    provider_name: str,
    model: str,
    base_url: str | None,
    perm_mode: str,
    session_id: str | None,
    working_dir: str | None,
    sim_enabled: bool = False,
) -> None:
    """Run the JSON-RPC server on stdio."""
    cwd = working_dir or str(Path.cwd())
    provider = get_provider(provider_name, base_url)
    client = LLMClient(provider)

    session = Session(id=session_id or uuid.uuid4().hex[:8])
    session.model = model
    session.provider = provider_name
    session.working_dir = cwd

    tools = ToolExecutor(working_dir=cwd)
    policy = PermissionPolicy(mode=PermissionMode(perm_mode))
    tracker = UsageTracker()

    # Simulation engine (gated behind --sim flag)
    _sim_engine = None
    _sim_runner = None
    if sim_enabled:
        from redclaw.sim.engine import SimEngine
        from redclaw.sim.runner import SimRunner
        from redclaw.sim.tools import register_sim_tools
        _sim_engine = SimEngine()
        register_sim_tools(tools, _sim_engine)

        async def _emit_sim_tick(data: dict[str, Any]) -> None:
            _emit(data)

        _sim_runner = SimRunner(_sim_engine, emit_fn=_emit_sim_tick)
        await _sim_runner.start()

    rt = ConversationRuntime(
        client=client,
        provider=provider,
        model=model,
        session=session,
        tools=tools,
        permission_policy=policy,
        usage_tracker=tracker,
        working_dir=cwd,
    )

    _emit({"type": "ready", "session_id": session.id, "model": model, "provider": provider_name})

    loop = asyncio.get_event_loop()
    current_task: asyncio.Task | None = None

    async def _read_stdin() -> None:
        nonlocal current_task
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        while True:
            line = await reader.readline()
            if not line:
                break
            line = line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                _reply(None, error="Invalid JSON")
                continue

            req_id = request.get("id")
            method = request.get("method", "")
            params = request.get("params", {})

            if method == "prompt":
                text = params.get("text", "")
                if current_task and not current_task.done():
                    _reply(req_id, error="A turn is already running")
                    continue

                async def _run_prompt(t: str, rid: int | None) -> None:
                    cb = ConversationCallbacks(
                        on_text_delta=lambda txt: _emit_async({"type": "text_delta", "text": txt}),
                        on_tool_begin=lambda tid, tn, ti: _emit_async({"type": "tool_call", "id": tid, "name": tn, "input": ti}),
                        on_tool_result=lambda tid, r, ie: _emit_async({"type": "tool_result", "id": tid, "result": r, "is_error": ie}),
                        on_usage=lambda u: _emit_async({"type": "usage", "input_tokens": u.input_tokens, "output_tokens": u.output_tokens}),
                        on_error=lambda m: _emit_async({"type": "error", "message": m}),
                    )
                    summary = await rt.run_turn(t, cb)
                    _emit({"type": "done", "error": summary.error})
                    _reply(rid, result={"tool_calls": summary.tool_calls})

                def _emit_async(event: dict[str, Any]) -> Any:
                    _emit(event)
                    # Return a coroutine-like that does nothing
                    async def _noop() -> None:
                        pass
                    return _noop()

                current_task = asyncio.create_task(_run_prompt(text, req_id))

            elif method == "abort":
                rt.abort()
                _reply(req_id, result="aborted")

            elif method == "new_session":
                new_id = uuid.uuid4().hex[:8]
                rt.session = Session(id=new_id, model=model, provider=provider_name, working_dir=cwd)
                _reply(req_id, result={"session_id": new_id})

            elif method == "compact":
                compact_session(rt.session)
                _reply(req_id, result="compacted")

            elif method == "get_state":
                _reply(req_id, result={
                    "session_id": rt.session.id,
                    "model": rt.model,
                    "provider": provider_name,
                    "message_count": len(rt.session.messages),
                    "usage": tracker.summary(),
                })

            elif method == "set_model":
                rt.model = params.get("model", model)
                _reply(req_id, result={"model": rt.model})

            elif method == "set_provider":
                new_provider = params.get("provider", provider_name)
                new_base = params.get("base_url")
                provider = get_provider(new_provider, new_base)
                await client.close()
                client = LLMClient(provider)
                rt.client = client
                rt.provider = provider
                _reply(req_id, result={"provider": new_provider})

            elif method == "plan_mode":
                enabled = params.get("enabled", True)
                rt.set_plan_mode(enabled)
                _emit({"type": "plan_mode_changed", "enabled": rt.plan_mode})
                _reply(req_id, result={"plan_mode": rt.plan_mode})

            elif method == "wiki_query":
                question = params.get("question", "")
                if not question:
                    _reply(req_id, error="'question' is required")
                    continue
                try:
                    from redclaw.wiki.tools import execute_wiki
                    answer = await execute_wiki(
                        action="query", question=question,
                        client=client, provider=provider, model=model,
                    )
                    _reply(req_id, result={"answer": answer})
                except Exception as e:
                    _reply(req_id, error=str(e))

            elif method == "wiki_stats":
                try:
                    from redclaw.wiki.tools import execute_wiki
                    stats = await execute_wiki(
                        action="stats",
                        client=client, provider=provider, model=model,
                    )
                    _reply(req_id, result={"stats": stats})
                except Exception as e:
                    _reply(req_id, error=str(e))

            elif method == "wiki_ingest":
                source = params.get("source", "")
                topic = params.get("topic", "general")
                if not source:
                    _reply(req_id, error="'source' is required")
                    continue
                try:
                    from redclaw.wiki.tools import execute_wiki
                    result_text = await execute_wiki(
                        action="ingest", source=source, topic=topic,
                        client=client, provider=provider, model=model,
                    )
                    _reply(req_id, result={"result": result_text})
                except Exception as e:
                    _reply(req_id, error=str(e))

            elif method == "sim_command":
                if not _sim_engine:
                    _reply(req_id, error="Simulation not enabled. Use --sim flag.")
                    continue
                action = params.get("action", "")
                try:
                    result = await _handle_sim_command(action, params, _sim_engine)
                    _reply(req_id, result=result)
                except Exception as e:
                    _reply(req_id, error=str(e))

            else:
                _reply(req_id, error=f"Unknown method: {method}")

    try:
        await _read_stdin()
    except Exception:
        pass
    finally:
        if _sim_runner and _sim_runner.running:
            await _sim_runner.stop()
        await client.close()
