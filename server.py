import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from config import Config
from session import Session, init_db
from agent import Agent
from tools import create_registry

log = logging.getLogger(__name__)
config = Config.load()
tools = create_registry(config.workspace)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("CodeAssist starting | model=%s workspace=%s", config.llm.model, config.workspace)
    yield


app = FastAPI(title="CodeAssist", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.get("/api/sessions")
async def list_sessions():
    return await Session.list_all()


@app.post("/api/sessions")
async def create_session():
    session = await Session.create()
    return {"id": session.id}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    session = Session(session_id)
    await session.delete()
    return {"ok": True}


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    session = Session(session_id)
    return await session.get_messages()


@app.get("/api/config")
async def get_config():
    return {
        "model": config.llm.model,
        "provider": config.llm.provider,
        "workspace": str(config.workspace),
        "agent_name": config.agent.name,
    }


@app.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str):
    await websocket.accept()
    session = Session(session_id)
    agent = Agent(config, session, tools)

    try:
        while True:
            data = await websocket.receive_json()

            if data.get("type") == "user_message":
                content = data.get("content", "")
                if not content.strip():
                    continue

                try:
                    async for event in agent.run(content):
                        await websocket.send_json({
                            "type": event.type,
                            **event.data,
                        })
                except Exception as e:
                    log.exception("Agent error")
                    await websocket.send_json({"type": "error", "message": str(e)})

            elif data.get("type") == "cancel":
                pass

    except WebSocketDisconnect:
        log.info("Client disconnected from session %s", session_id)
    except Exception as e:
        log.exception("WebSocket error")
        try:
            await websocket.close()
        except Exception:
            pass


def main():
    import uvicorn
    uvicorn.run(
        "server:app",
        host=config.server.host,
        port=config.server.port,
        reload=True,
    )


if __name__ == "__main__":
    main()
