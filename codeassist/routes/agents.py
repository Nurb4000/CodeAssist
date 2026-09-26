"""Agent management API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def list_agents():
    """List all available agents (default, research, review, custom)."""
    from codeassist.agents import agent_manager
    return agent_manager.list_agents()


@router.post("")
async def create_agent(body: dict):
    """Create a new agent with custom permissions and configuration. Body must contain 'name'."""
    from codeassist.agents import agent_manager
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    agent = await agent_manager.create_agent(
        name=name,
        description=body.get("description"),
        instructions=body.get("instructions"),
        model=body.get("model"),
        max_iterations=body.get("max_iterations"),
        steps=body.get("steps"),
        permissions=body.get("permissions", {}),
    )
    return agent.to_dict() if hasattr(agent, 'to_dict') else {"name": name}


@router.patch("/{agent_name}")
async def update_agent(agent_name: str, body: dict):
    """Update an agent's editable fields.

    Custom agents expose every field; built-in agents only expose the per-agent
    step budget (their permissions/instructions are code-defined). Built-in
    edits persist as a keyed DB override so they survive restarts.
    """
    from codeassist.agents import BUILTIN_AGENT_KEYS, agent_manager
    is_builtin = agent_name in BUILTIN_AGENT_KEYS
    try:
        await agent_manager.update_agent(
            agent_name,
            **({} if is_builtin else {
                "description": body.get("description"),
                "instructions": body.get("instructions"),
                "model": body.get("model"),
                "max_iterations": body.get("max_iterations"),
            }),
            steps=body.get("steps"),
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"ok": True}


@router.delete("/{agent_name}")
async def delete_agent(agent_name: str):
    """Delete an agent by name."""
    from codeassist.agents import agent_manager
    try:
        await agent_manager.delete_agent(agent_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}
