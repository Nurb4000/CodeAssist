"""Agent management API routes."""
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def list_agents():
    """List all available agents (default, research, review, custom)."""
    from agents import agent_manager
    return agent_manager.list_agents()


@router.post("")
async def create_agent(body: dict):
    """Create a new agent with custom permissions and configuration. Body must contain 'name'."""
    from agents import agent_manager
    name = body.get("name")
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    agent = await agent_manager.create_agent(
        name=name,
        description=body.get("description"),
        instructions=body.get("instructions"),
        model=body.get("model"),
        max_iterations=body.get("max_iterations"),
        permissions=body.get("permissions", {}),
    )
    return agent.to_dict() if hasattr(agent, 'to_dict') else {"name": name}


@router.delete("/{agent_name}")
async def delete_agent(agent_name: str):
    """Delete an agent by name."""
    from agents import agent_manager
    await agent_manager.delete_agent(agent_name)
    return {"ok": True}
