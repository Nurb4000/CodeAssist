"""REST API for unattended automations."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from .. import automations as automations_mod
from ..automations import (
    ACTIONS,
    KINDS,
    AutomationEngine,
    AutomationStore,
    compute_next,
    validate_automation,
)

router = APIRouter(prefix="/api/automations", tags=["automations"])
store = AutomationStore()


def _cfg():
    # Imported lazily like the sibling routers: server imports the route registry.
    from ..server import get_config as _get_config

    return _get_config()


def _validate_create(body: dict[str, Any]) -> dict[str, Any]:
    missing = {"name", "kind", "schedule", "action", "payload"} - set(body)
    if missing:
        raise HTTPException(status_code=400, detail=f"missing: {', '.join(sorted(missing))}")
    try:
        data = validate_automation(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if data["action"] == "shell" and not _cfg().automations.allow_shell:
        raise HTTPException(status_code=400, detail="shell automations are disabled")
    return data


def _engine() -> AutomationEngine:
    # Read through the module so tests (and a config reload) can swap the engine.
    engine = automations_mod.automation_engine
    if engine is None:
        raise HTTPException(status_code=503, detail="Automation engine is not running")
    return engine


@router.get("")
async def list_automations() -> dict[str, Any]:
    return {
        "automations": await store.list_all(),
        "enabled": bool(_cfg().automations.enabled),
    }


@router.post("")
async def create_automation(body: dict[str, Any]) -> dict[str, Any]:
    data = _validate_create(body)
    automation = await store.create(data)
    return {"id": automation["id"], "next_run_at": automation["next_run_at"]}


@router.put("/{automation_id}")
async def update_automation(automation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    current = await store.get(automation_id)
    if current is None:
        raise HTTPException(status_code=404, detail="automation not found")
    try:
        patch = validate_automation(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not patch:
        raise HTTPException(status_code=400, detail="no fields to update")
    # Cross-field check: a body may change only one of kind/schedule, so validate
    # the pair that will actually be stored.
    merged = {**current, **patch}
    try:
        compute_next(merged["kind"], merged["schedule"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if merged["action"] == "shell" and not _cfg().automations.allow_shell:
        raise HTTPException(status_code=400, detail="shell automations are disabled")
    updated = await store.update(automation_id, patch)
    return {"ok": True, "next_run_at": (updated or {}).get("next_run_at")}


@router.delete("/{automation_id}")
async def delete_automation(automation_id: str) -> dict[str, Any]:
    if not await store.delete(automation_id):
        raise HTTPException(status_code=404, detail="automation not found")
    return {"ok": True}


@router.get("/{automation_id}/runs")
async def list_runs(automation_id: str, limit: int = 50) -> dict[str, Any]:
    if await store.get(automation_id) is None:
        raise HTTPException(status_code=404, detail="automation not found")
    return {"runs": await store.list_runs(automation_id, limit)}


@router.post("/{automation_id}/run")
async def run_automation_now(automation_id: str) -> dict[str, Any]:
    if await store.get(automation_id) is None:
        raise HTTPException(status_code=404, detail="automation not found")
    if not await _engine().run_now(automation_id):
        raise HTTPException(status_code=404, detail="automation not found")
    return {"ok": True}


@router.post("/events/{event_name}")
async def trigger_automation_event(event_name: str) -> dict[str, Any]:
    return {"triggered": await _engine().trigger_event(event_name)}


__all__ = ["router", "ACTIONS", "KINDS"]
