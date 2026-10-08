"""REST API for subagent task management.

Exposes list/get/cancel endpoints so the UI can monitor and manage
long-running subagent tasks from the TaskTool.
"""
from fastapi import APIRouter, HTTPException

from ..subagent import subagent_manager

router = APIRouter(prefix="/api/subagents", tags=["subagents"])


@router.get("/tasks")
async def list_tasks(session_id: str | None = None):
    """List all subagent tasks, optionally filtered by parent session."""
    tasks = subagent_manager.list_tasks()
    if session_id:
        tasks = [t for t in tasks if t.get("parent_session_id") == session_id]
    return {"tasks": tasks}


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a specific subagent task by ID."""
    task = subagent_manager.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found")
    return {"task": task.to_dict()}


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running or pending subagent task."""
    cancelled = await subagent_manager.cancel_task(task_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail=f"Task '{task_id}' not found or already finished")
    return {"ok": True, "task_id": task_id}


@router.get("/tasks/active")
async def list_active_tasks(session_id: str | None = None):
    """List only tasks that are pending or running."""
    tasks = subagent_manager.get_active_tasks()
    if session_id:
        tasks = [t for t in tasks if t.parent_session_id == session_id]
    return {"tasks": [t.to_dict() for t in tasks]}
