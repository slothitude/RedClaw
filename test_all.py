"""Comprehensive test suite for RedClaw assistant + knowledge graph."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone

tmpdir = tempfile.mkdtemp()
passed = 0
failed = 0
errors = []


def run_test(name, coro):
    try:
        coro()
        passed += 1
        print(f"  PASS: {name}")
    except AssertionError as e:
        failed += 1
        errors.append(f"  FAIL: {name}: {e}")
    except Exception as e:
        failed += 1
        errors.append(f"  ERROR: {name}: {e}")


# ── Store classes ──────────────────────────────────────────────

stores = {}


def set_stores_for_test(ts, ns, rs):
    """Set stores for assistant tools."""
    from redclaw.tools.assistant_tools import set_stores
    set_stores(ts, ns, rs)


# ── Tests ──────────────────────────────────────────────────────


async def test_config():
    from redclaw.assistant.config import AssistantConfig
    d = os.path.join(tmpdir, "c")
    cfg = AssistantConfig(timezone="US/Pacific", briefing_time="06:00", briefing_weather=False, news_topics=["science"])
    cfg._path = str(os.path.join(d, "config.json"))
    cfg.save()
    cfg2 = AssistantConfig.load(config_dir=d)
    assert cfg2.timezone == "US/Pacific"
    assert cfg2.briefing_time == "06:00"
    assert not cfg2.briefing_weather
    assert cfg2.news_topics == ["science"]
    shutil.rmtree(tmpdir)


async def test_tasks():
    from redclaw.assistant.tasks import TaskStore
    p = os.path.join(tmpdir, "t.json")
    ts = TaskStore(path=p)
    t = ts.add("Buy groceries", priority="high", tags=["personal"], due="2025-12-25T18:00:00")
    assert t.title == "Buy groceries"
    assert t.priority == "high"
    tasks = ts.list_tasks()
    assert len(tasks) == 1
    ts.update(t.id, status="done")
    assert ts.get(t.id).status == "done"
    results = ts.search("groceries")
    assert len(results) == 1
    assert ts.delete(t.id)
    assert ts.get(t.id) is None


async def test_notes():
    from redclaw.assistant.notes import NoteStore
    p = os.path.join(tmpdir, "n.json")
    ns = NoteStore(path=p)
    n = ns.add("Meeting notes", content="Discussed roadmap", tags=["work"])
    assert n.title == "Meeting notes"
    notes = ns.list_notes()
    assert len(notes) == 1
    results = ns.search("Meeting")
    assert len(results) == 1
    ns.update(n.id, content="Updated content")
    assert ns.get(n.id).content == "Updated content"
    assert ns.delete(n.id)


async def test_reminders():
    from redclaw.assistant.reminders import ReminderStore
    p = os.path.join(tmpdir, "r.json")
    rs = ReminderStore(path=p)
    r = rs.add("Call mom", trigger_at="2025-06-01T10:00:00", recurrence="weekly")
    assert r.text == "Call mom"
    assert not r.delivered
    pending = rs.get_pending()
    assert len(pending) == 1
    next_t = rs.get_next_occurrence(r)
    assert next_t is not None
    assert "2025-06-08" in next_t.isoformat()
    rs.mark_delivered(r.id)
    updated = rs.get(r.id)
    assert "2025-06-08" in updated.trigger_at
    assert not updated.delivered


async def test_assistant_tools():
    from redclaw.assistant.tasks import TaskStore
    from redclaw.assistant.notes import NoteStore
    from redclaw.assistant.reminders import ReminderStore
    from redclaw.tools.assistant_tools import execute_task, execute_note, execute_reminder, set_stores
    ts = TaskStore(path=os.path.join(tmpdir, "at.json"))
    ns = NoteStore(path=os.path.join(tmpdir, "an.json"))
    rs = ReminderStore(path=os.path.join(tmpdir, "ar.json"))
    set_stores(ts, ns, rs)
    r = await execute_task(action="add", title="Test task", priority="urgent", tags="test")
    data = json.loads(r)
    assert data["title"] == "Test task"
    tid = data["id"]
    r = await execute_task(action="list")
    assert "Test task" in r
    r = await execute_task(action="update", task_id=tid, status="done")
    assert "done" in r
    r = await execute_task(action="search", query="Test")
    assert "Test task" in r
    r = await execute_task(action="delete", task_id=tid)
    assert "deleted" in r
    r = await execute_note(action="add", title="Test note", content="Hello", tags="test")
    data = json.loads(r)
    assert data["title"] == "Test note"
    nid = data["id"]
    r = await execute_note(action="search", query="Test")
    assert "Test note" in r
    r = await execute_note(action="delete", note_id=nid)
    assert "deleted" in r
    r = await execute_reminder(action="add", text="Test rem", trigger_at="2025-06-01T10:00:00")
    data = json.loads(r)
    assert data["text"] == "Test rem"
    rid = data["id"]
    r = await execute_reminder(action="list")
    assert "Test rem" in r
    r = await execute_reminder(action="delete", reminder_id=rid)
    assert "deleted" in r


async def test_kg():
    from redclaw.memory_graph import KnowledgeGraph, KnowledgeStatus
    kg = KnowledgeGraph()
    assert kg.available
    assert kg.status == KnowledgeStatus.IDLE
    stats = await kg.stats()
    assert stats.status == KnowledgeStatus.IDLE
    datasets = await kg.list_datasets()
    assert isinstance(datasets, list)
    r = await kg.delete_dataset("nonexistent_xyz")
    assert "not found" in r.lower()


async def test_kg_tools():
    from redclaw.memory_graph.tools import execute_knowledge
    r = await execute_knowledge(action="stats")
    assert "Status" in r
    r = await execute_knowledge(action="bogus")
    assert "Unknown action" in r


def test_prompt():
    from redclaw.runtime.prompt import build_system_prompt
    p1 = build_system_prompt("/tmp")
    assert "coding agent" in p1
    p2 = build_system_prompt("/tmp", mode="assistant")
    assert "personal assistant" in p2
    p3 = build_system_prompt("/tmp", mode="assistant", assistant_context="Tasks: 3")
    assert "Tasks: 3" in p3


def test_toolsets():
    from redclaw.tools.toolsets import BUILTIN_TOOLSETS, resolve_toolset
    assert "assistant" in BUILTIN_TOOLSETS
    assert "knowledge" in BUILTIN_TOOLSETS
    assert resolve_toolset("assistant") == {"task", "note", "reminder"}
    assert resolve_toolset("knowledge") == {"knowledge"}


def test_cli():
    from redclaw.cli import build_parser
    p = build_parser()
    args = p.parse_args(["--assistant", "--mode", "telegram"])
    assert args.assistant
    args2 = p.parse_args([])
    assert not args2.assistant


async def test_fallback():
    from redclaw.memory_graph import KnowledgeGraph, KnowledgeStatus
    kg = KnowledgeGraph.__new__(KnowledgeGraph)
    kg._cognee = None
    kg._status = KnowledgeStatus.UNAVAILABLE
    kg._data_dir = None
    kg._llm_api_key = None
    assert not kg.available
    r = await kg.add("test")
    assert "unavailable" in r.lower()
    assert (await kg.search("test")) == []
    assert "unavailable" in (await kg.cognify()).lower()


    assert "unavailable" in (await kg.memify()).lower()
    assert (await kg.stats()).status == KnowledgeStatus.UNAVAILABLE
    assert "unavailable" in (await kg.prune()).lower()
    assert (await kg.list_datasets()) == []


async def test_scheduler():
    from redclaw.assistant.scheduler import AssistantScheduler
    from redclaw.assistant.config import AssistantConfig
    from redclaw.assistant.tasks import TaskStore
    from redclaw.assistant.reminders import ReminderStore
    ts = TaskStore(path=os.path.join(tmpdir, "st.json"))
    rs = ReminderStore(path=os.path.join(tmpdir, "sr.json"))
    config = AssistantConfig(timezone="UTC", briefing_enabled=False)
    sent = []
    async def mock_send(uid, text):
        sent.append((uid, text))
    sched = AssistantScheduler(config=config, tasks=ts, reminders=rs, send_fn=mock_send, user_id=12345, search_url=None)
    r = rs.add("Test reminder", trigger_at="2020-01-01T00:00:00")
    await sched._check_reminders(datetime(2020, 1, 1, 0, 0, 1))
    assert len(sent) == 1
    assert "Test reminder" in sent[0][1]
    updated = rs.get(r.id)
    assert updated.delivered


async def test_briefing():
    from redclaw.assistant.briefing import BriefingGenerator
    from redclaw.assistant.config import AssistantConfig
    from redclaw.assistant.tasks import TaskStore
    from redclaw.assistant.reminders import ReminderStore
    ts = TaskStore(path=os.path.join(tmpdir, "bt.json"))
    rs = ReminderStore(path=os.path.join(tmpdir, "br.json"))
    config = AssistantConfig(timezone="UTC", briefing_weather=False, briefing_news=False)
    gen = BriefingGenerator(config=config, tasks=ts, reminders=rs, search_url=None)
    briefing = await gen.generate()
    assert "Good morning" in briefing
    ts.add("Due task", due="2025-12-25T18:00:00")
    briefing2 = await gen.generate()
    assert "Due task" in briefing2 or "No tasks" in briefing2
    rs.add("Test reminder", trigger_at="2025-06-01T10:00:00")
    briefing3 = await gen.generate()
    assert "Test reminder" in briefing3 or "No pending" in briefing3


def test_runtime():
    from redclaw.runtime.conversation import ConversationRuntime
    import inspect
    sig = inspect.signature(ConversationRuntime.__init__)
    params = list(sig.parameters.keys())
    assert "mode" in params
    assert "assistant_context" in params


# ── Run all ──────────────────────────────────────────────────────

tests = [
    test_config,
    test_tasks,
    test_notes,
    test_reminders,
    test_assistant_tools,
    test_kg,
    test_kg_tools,
    test_prompt,
    test_toolsets,
    test_cli,
    test_fallback,
    test_scheduler,
    test_briefing,
    test_runtime,
]

for test_func in tests:
    run_test(test.__name__)

print(f"\nResults: {passed} passed, {failed} failed, {len(errors)} errors")
  0)
if errors:
    for e in errors:
        print(f"  {e}")
    sys.exit(1)
else:
    print("ALL 14 TESTS PASSED")

shutil.rmtree(tmpdir)
