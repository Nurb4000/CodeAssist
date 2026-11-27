"""Coverage for unattended automations: schedule parsing, persistence, isolation, and REST."""
import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from codeassist import automations as am
from codeassist.automations import (
    AutomationEngine,
    AutomationStore,
    compute_next,
    parse_cron,
    validate_automation,
)
from codeassist.config import AutomationsConfig
from codeassist.routes.automations import router
from codeassist.session import init_db


@pytest_asyncio.fixture
async def automation_db():
    """Isolated DB with the automation tables, bound to the test's event loop."""
    await init_db()


def test_cron_expression_parsing_and_next_fire():
    schedule = parse_cron("*/5 * * * *")
    now = datetime(2026, 11, 27, 10, 1, 30, tzinfo=UTC)
    assert schedule.next_after(now).isoformat() == "2026-11-27T10:05:00+00:00"

    daily = parse_cron("0 9 * * 1-5")
    # Friday's 09:00 has just passed; the next weekday slot is Monday.
    assert daily.next_after(datetime(2026, 11, 27, 9, 0, 1, tzinfo=UTC)).isoformat() == (
        "2026-11-30T09:00:00+00:00"
    )

    for bad in ("* * * *", "60 * * * *", "0 24 * * *", "*/0 * * * *", "0 0 30 2 */x"):
        with pytest.raises(ValueError):
            parse_cron(bad)

    # Impossible dates must terminate rather than looping forever.
    assert parse_cron("0 0 30 2 *").next_after(now) is None


def test_interval_and_event_schedules():
    now = datetime(2026, 11, 27, 10, 0, 0, tzinfo=UTC)
    assert compute_next("interval", "60", now) == "2026-11-27T10:01:00+00:00"
    assert compute_next("event", "deploy-finished", now) is None
    with pytest.raises(ValueError):
        compute_next("interval", "0", now)
    with pytest.raises(ValueError):
        compute_next("event", "  ", now)
    with pytest.raises(ValueError):
        compute_next("teleport", "1", now)


def test_validation_rejects_unknown_fields_and_bad_payloads():
    with pytest.raises(ValueError, match="unknown automation field"):
        validate_automation({"name": "x", "surprise": 1})
    with pytest.raises(ValueError, match="payload is required"):
        validate_automation({"payload": "  "})
    with pytest.raises(ValueError, match="action must be"):
        validate_automation({"action": "dance"})
    assert validate_automation({"name": " x ", "enabled": 1}) == {"name": "x", "enabled": True}


@pytest.mark.asyncio
async def test_store_crud_and_append_only_run_history(automation_db):
    store = AutomationStore()
    created = await store.create({
        "name": "nightly", "kind": "cron", "schedule": "0 3 * * *",
        "action": "prompt", "payload": "summarise", "enabled": True, "max_runs": 2,
    })
    assert created["next_run_at"] is not None
    assert (await store.get(created["id"]))["name"] == "nightly"

    updated = await store.update(created["id"], {"schedule": "30 3 * * *"})
    assert updated["next_run_at"] != created["next_run_at"]
    assert (await store.get(created["id"]))["schedule"] == "30 3 * * *"

    run_id = await store.start_run(created["id"])
    await store.finish_run(run_id, "error", output="partial", error="boom")
    runs = await store.list_runs(created["id"])
    assert len(runs) == 1
    assert runs[0]["status"] == "error" and runs[0]["error"] == "boom"
    assert (await store.get(created["id"]))["run_count"] == 1

    assert await store.delete(created["id"]) is True
    assert await store.get(created["id"]) is None
    assert await store.list_runs(created["id"]) == []


@pytest.mark.asyncio
async def test_tick_arms_jobs_once_and_a_failure_does_not_block_the_next(automation_db):
    cfg = SimpleNamespace(
        automations=AutomationsConfig(enabled=True, tick_seconds=0.01, timeout_seconds=5),
        workspace=Path.cwd(),
    )
    store = AutomationStore()
    first = await store.create({
        "name": "fails", "kind": "interval", "schedule": "10",
        "action": "prompt", "payload": "fail", "enabled": True,
    })
    second = await store.create({
        "name": "works", "kind": "interval", "schedule": "10",
        "action": "prompt", "payload": "work", "enabled": True,
    })

    async def runner(automation, session):
        if automation["payload"] == "fail":
            raise RuntimeError("expected failure")
        await asyncio.sleep(0.02)
        return "done"

    engine = AutomationEngine(cfg, store=store, prompt_runner=runner)
    # Force both next_run times into the past.
    for automation in (first, second):
        await store.set_next_run(automation["id"], "2020-01-01T00:00:00+00:00")

    due = await engine.tick()
    assert set(due) == {first["id"], second["id"]}
    assert await engine.tick() == [], "a due job must not be re-armed before its next time"
    await asyncio.gather(*list(engine._tasks), return_exceptions=True)

    statuses = {r["automation_id"]: r["status"] for r in await store.list_runs(first["id"])}
    assert statuses == {first["id"]: "error"}
    assert (await store.list_runs(second["id"]))[0]["status"] == "ok"
    assert await engine.tick() == [], "tick still works after a job failed"


@pytest.mark.asyncio
async def test_event_automations_run_only_on_matching_events(automation_db):
    cfg = SimpleNamespace(automations=AutomationsConfig(enabled=True), workspace=Path.cwd())
    store = AutomationStore()
    await store.create({
        "name": "on-deploy", "kind": "event", "schedule": "deploy-finished",
        "action": "prompt", "payload": "check deploy", "enabled": True,
    })
    engine = AutomationEngine(cfg, store=store, prompt_runner=lambda a, s: _ok("ok"))
    assert await engine.trigger_event("unrelated") == 0
    assert await engine.trigger_event("deploy-finished") == 1
    await asyncio.gather(*list(engine._tasks), return_exceptions=True)
    assert (await store.list_all())[0]["last_status"] == "ok"


async def _ok(text):
    return text


@pytest.mark.asyncio
async def test_shell_action_is_gated_and_records_output(automation_db):
    store = AutomationStore()
    job = await store.create({
        "name": "echo", "kind": "event", "schedule": "ping",
        "action": "shell", "payload": "printf hello", "enabled": True,
    })
    blocked = SimpleNamespace(
        automations=AutomationsConfig(allow_shell=False), workspace=Path.cwd()
    )
    engine = AutomationEngine(blocked, store=store)
    assert await engine.run(job) == "error"
    assert "allow_shell" in (await store.list_runs(job["id"]))[0]["error"]

    allowed = SimpleNamespace(
        automations=AutomationsConfig(allow_shell=True, timeout_seconds=5), workspace=Path.cwd()
    )
    engine = AutomationEngine(allowed, store=store)
    assert await engine.run(job) == "ok"
    assert (await store.list_runs(job["id"]))[0]["output"] == "hello"


@pytest.mark.asyncio
async def test_plugin_hook_can_fan_out_related_events(automation_db):
    store = AutomationStore()
    for event in ("deploy", "post-deploy"):
        await store.create({
            "name": event, "kind": "event", "schedule": event,
            "action": "prompt", "payload": event, "enabled": True,
        })
    seen = []

    async def hooks(hook_name, event_name):
        assert hook_name == "automation.trigger"
        seen.append(event_name)
        return ["post-deploy"] if event_name == "deploy" else []

    engine = AutomationEngine(
        SimpleNamespace(automations=AutomationsConfig(enabled=True), workspace=Path.cwd()),
        store=store, prompt_runner=lambda a, s: _ok("ok"), hook_runner=hooks,
    )
    assert await engine.trigger_event("deploy") == 2
    assert seen == ["deploy", "post-deploy"]
    if engine._tasks:
        await asyncio.gather(*list(engine._tasks), return_exceptions=True)

    seen.clear()
    assert await engine.trigger_event("post-deploy") == 1
    assert seen == ["post-deploy"], "a hook must not re-fire an already seen event"
    if engine._tasks:
        await asyncio.gather(*list(engine._tasks), return_exceptions=True)


@pytest.mark.asyncio
async def test_prompt_results_are_delivered_but_shell_output_is_not(automation_db):
    store = AutomationStore()
    delivered = []

    async def deliver(message):
        delivered.append(message)

    cfg = SimpleNamespace(
        automations=AutomationsConfig(enabled=True, allow_shell=True), workspace=Path.cwd()
    )
    engine = AutomationEngine(cfg, store=store, prompt_runner=lambda a, s: _ok("summary"), deliver=deliver)
    prompt = await store.create({
        "name": "summary", "kind": "event", "schedule": "ping",
        "action": "prompt", "payload": "summarise", "enabled": True,
        "session_id": "alpha",
    })
    shell = await store.create({
        "name": "secret", "kind": "event", "schedule": "ping",
        "action": "shell", "payload": "printf secret", "enabled": True,
    })
    assert await engine.run(prompt) == "ok"
    assert await engine.run(shell) == "ok"
    assert len(delivered) == 1
    assert delivered[0] == {
        "type": "automation_result", "automation_id": prompt["id"], "name": "summary",
        "status": "ok", "output": "summary", "error": "", "session_id": "alpha",
    }


def _routes_app(monkeypatch):
    """Minimal app with only the automations router, bypassing the app lifespan.

    An ASGI client (rather than TestClient) keeps requests on the same event loop
    as the pooled SQLite connections.
    """
    app = FastAPI()
    app.include_router(router)
    from codeassist import server

    monkeypatch.setattr(
        server, "get_config",
        lambda: SimpleNamespace(automations=AutomationsConfig(enabled=True, allow_shell=True)),
    )
    return app


def _client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_automation_routes_validate_and_persist(automation_db, monkeypatch):
    async with _client(_routes_app(monkeypatch)) as client:
        assert (await client.get("/api/automations")).json() == {"automations": [], "enabled": True}

        bad = await client.post("/api/automations", json={"name": "x"})
        assert bad.status_code == 400
        created = await client.post("/api/automations", json={
            "name": "hourly", "kind": "interval", "schedule": "3600",
            "action": "prompt", "payload": "check logs",
        })
        assert created.status_code == 200, created.text
        automation_id = created.json()["id"]

        listed = (await client.get("/api/automations")).json()["automations"]
        assert len(listed) == 1 and listed[0]["name"] == "hourly"

        invalid_update = await client.put(
            f"/api/automations/{automation_id}", json={"kind": "nope"}
        )
        assert invalid_update.status_code == 400
        updated = await client.put(f"/api/automations/{automation_id}", json={"enabled": False})
        assert updated.status_code == 200
        assert (await client.get("/api/automations")).json()["automations"][0]["enabled"] is False

        runs = await client.get(f"/api/automations/{automation_id}/runs")
        assert runs.json() == {"runs": []}
        assert (await client.delete(f"/api/automations/{automation_id}")).json() == {"ok": True}
        assert (await client.delete(f"/api/automations/{automation_id}")).status_code == 404


@pytest.mark.asyncio
async def test_automation_run_and_event_routes(automation_db, monkeypatch):
    async with _client(_routes_app(monkeypatch)) as client:
        engine = AutomationEngine(
            SimpleNamespace(automations=AutomationsConfig(enabled=True), workspace=Path.cwd()),
            prompt_runner=lambda a, s: _ok("ok"),
        )
        monkeypatch.setattr(am, "automation_engine", engine)
        created = (await client.post("/api/automations", json={
            "name": "event job", "kind": "event", "schedule": "deploy",
            "action": "prompt", "payload": "go",
        })).json()
        assert (await client.post(f"/api/automations/{created['id']}/run")).json() == {"ok": True}
        assert (await client.post("/api/automations/events/deploy")).json() == {"triggered": 1}
        assert (await client.post("/api/automations/events/other")).json() == {"triggered": 0}
        assert (await client.post("/api/automations/missing/run")).status_code == 404
        if engine._tasks:
            await asyncio.gather(*list(engine._tasks), return_exceptions=True)
