from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import logging
import os
import sys
import uuid
import datetime
import threading
from jose import jwt, JWTError, ExpiredSignatureError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from core.db_connectors.db_manager import db_manager
from core.analytics.efficiency_matrix import build_agent_efficiency_matrix
from core.diagnostics.preflight import run_preflight_diagnostics
from core.agent_catalog import get_api_catalog, get_agent_spec, resolve_agent_role
from core.agent_runtime import ensure_agents_running
from config.settings import config
from tracker.tracker_core import swarm_tracker, EntryType, Status

app = FastAPI(title="TrueMatrix Swarm API", root_path="/army-api")


def _parse_allowed_origins() -> list[str]:
    raw = os.getenv("API_ALLOWED_ORIGINS", "http://localhost,http://127.0.0.1")
    return [v.strip() for v in raw.split(",") if v.strip()]


allowed_origins = _parse_allowed_origins()
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)


def _load_valid_users() -> dict[str, str]:
    raw = os.getenv("VALID_USERS_JSON", "")
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logging.getLogger(__name__).error("VALID_USERS_JSON is not valid JSON")
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(k): str(v) for k, v in parsed.items() if k and v}


VALID_USERS = _load_valid_users()


def _autostart_loop(stop_event: threading.Event):
    while not stop_event.is_set():
        try:
            started = ensure_agents_running()
            if started:
                logging.getLogger(__name__).info(f"Auto-started missing agents: {', '.join(started)}")
        except Exception as e:
            logging.getLogger(__name__).error(f"Agent autostart loop failed: {e}")
        stop_event.wait(max(5, config.AGENT_AUTOSTART_INTERVAL_SECONDS))


@app.on_event("startup")
async def startup_event():
    if not config.AGENT_AUTOSTART_ENABLED:
        return
    stop_event = threading.Event()
    app.state.autostart_stop_event = stop_event
    thread = threading.Thread(target=_autostart_loop, args=(stop_event,), daemon=True)
    app.state.autostart_thread = thread
    thread.start()


@app.on_event("shutdown")
async def shutdown_event():
    stop_event = getattr(app.state, "autostart_stop_event", None)
    if stop_event:
        stop_event.set()


async def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = auth_header.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        return payload
    except ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")


class TaskRequest(BaseModel):
    agent_role: str
    task_type: str
    payload: dict = {}


class LoginRequest(BaseModel):
    email: str
    password: str


class GoogleLoginRequest(BaseModel):
    token: str


class WebhookTaskRequest(BaseModel):
    payload: dict = {}
    source: str = "webhook"


@app.post("/login")
async def login(request: LoginRequest):
    if not VALID_USERS:
        raise HTTPException(status_code=503, detail="Login is not configured")

    user_password = VALID_USERS.get(request.email)
    if not user_password or user_password != request.password:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    token = jwt.encode(
        {
            "sub": request.email,
            "name": request.email.split('@')[0].capitalize(),
            "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24),
        },
        config.JWT_SECRET,
        algorithm="HS256",
    )

    return {
        "status": "success",
        "token": token,
        "user": {
            "email": request.email,
            "name": request.email.split('@')[0].capitalize(),
        },
    }


@app.post("/google-login")
async def google_login(request: GoogleLoginRequest):
    try:
        idinfo = id_token.verify_oauth2_token(
            request.token,
            google_requests.Request(),
            config.GOOGLE_CLIENT_ID,
        )

        email = idinfo.get("email")
        name = idinfo.get("name", email.split("@")[0])

        if not email or not email.endswith("@truematrix.io"):
            raise HTTPException(status_code=403, detail="Only @truematrix.io emails are authorized")

        token = jwt.encode(
            {
                "sub": email,
                "name": name,
                "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24),
            },
            config.JWT_SECRET,
            algorithm="HS256",
        )

        return {"status": "success", "token": token, "user": {"email": email, "name": name}}
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Google auth failed: {str(e)}")


@app.get("/health")
async def health_check():
    return {"status": "ok", "timestamp": datetime.datetime.utcnow().isoformat()}


@app.get("/agents")
async def list_agents(user=Depends(get_current_user)):
    include_deprecated = os.getenv("API_INCLUDE_DEPRECATED_AGENTS", "false").lower() in ("1", "true", "yes")
    catalog = get_api_catalog(include_deprecated=include_deprecated)
    return [{**entry, "status": "online"} for entry in catalog]


@app.post("/task")
async def assign_task(request: TaskRequest, user=Depends(get_current_user)):
    return _publish_task(
        agent_role=request.agent_role,
        task_type=request.task_type,
        payload=request.payload,
        source_agent="api_gateway",
        username=user.get("sub", "system"),
    )


def _get_task_events(task_id: str, limit: int = 200) -> list[dict]:
    redis_client = db_manager.get_redis_client()
    key = f"task_events:{task_id}"
    raw = redis_client.lrange(key, 0, max(0, limit - 1))
    events = []
    for item in raw:
        try:
            events.append(json.loads(item))
        except Exception:
            continue
    return events


def _get_recent_chat_events(limit: int = 300) -> list[dict]:
    redis_client = db_manager.get_redis_client()
    task_ids = redis_client.lrange("dashboard_recent_tasks", 0, 99)
    all_events: list[dict] = []
    for task_id in task_ids:
        all_events.extend(_get_task_events(task_id, limit=20))
    all_events.sort(key=lambda item: item.get("timestamp", ""))
    return all_events[-max(1, limit) :]


def _publish_task(agent_role: str, task_type: str, payload: dict, source_agent: str, username: str = "system"):
    spec = get_agent_spec(agent_role)
    if spec is None:
        raise HTTPException(status_code=400, detail=f"Unknown agent_role: {agent_role}")
    routed_role = resolve_agent_role(agent_role)

    redis_client = db_manager.get_redis_client()
    task_id = str(uuid.uuid4())

    message = {
        "source_agent": source_agent,
        "task_id": task_id,
        "task": {"type": task_type, **(payload or {})},
        "user": username,
    }

    target_channel = f"task_queue_{routed_role}"
    redis_client.publish(target_channel, json.dumps(message))
    # Seed task event stream so UI can immediately render dispatch acknowledgment.
    try:
        seeded_event = {
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "task_id": task_id,
            "agent_role": "api_gateway",
            "event_type": "dispatched",
            "status": "info",
            "message": f"Task dispatched to {routed_role}",
            "payload": {"task_type": task_type, "requested_agent": agent_role, "routed_to": routed_role},
        }
        key = f"task_events:{task_id}"
        redis_client.rpush(key, json.dumps(seeded_event))
        redis_client.expire(key, 3600)
        redis_client.publish(f"task_response_{task_id}", json.dumps(seeded_event))
        redis_client.lpush("dashboard_recent_tasks", task_id)
        redis_client.ltrim("dashboard_recent_tasks", 0, 199)
        redis_client.expire("dashboard_recent_tasks", 3600 * 24)
    except Exception:
        pass
    return {
        "status": "success",
        "task_id": task_id,
        "agent": agent_role,
        "routed_to": routed_role,
        "task_type": task_type,
    }


@app.post("/webhook/{agent_role}/{task_type}")
async def webhook_task(
    agent_role: str,
    task_type: str,
    request: WebhookTaskRequest,
    x_webhook_secret: str = Header(default=""),
):
    if not config.WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Webhook secret is not configured")
    if x_webhook_secret != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid webhook secret")
    return _publish_task(
        agent_role=agent_role,
        task_type=task_type,
        payload=request.payload,
        source_agent=request.source or "webhook",
        username="webhook",
    )


@app.get("/task/{task_id}/events")
async def get_task_events(task_id: str, limit: int = 200, user=Depends(get_current_user)):
    return {"task_id": task_id, "events": _get_task_events(task_id, limit=limit)}


@app.get("/chat/events")
async def get_chat_events(limit: int = 300, user=Depends(get_current_user)):
    return {"events": _get_recent_chat_events(limit=limit)}


@app.get("/logs")
async def get_logs(limit: int = 50, user=Depends(get_current_user)):
    redis_client = db_manager.get_redis_client()
    try:
        logs = redis_client.lrange("global_execution_log", 0, limit - 1)
        return [json.loads(log) for log in logs]
    except Exception:
        return []


@app.get("/tracker/entries")
async def get_tracker_entries(user=Depends(get_current_user)):
    return swarm_tracker.get_entries()


@app.get("/metrics/agent-efficiency-matrix")
async def get_agent_efficiency_matrix(
    limit: int = 1000,
    hours: int | None = None,
    user=Depends(get_current_user),
):
    redis_client = db_manager.get_redis_client()
    try:
        return build_agent_efficiency_matrix(redis_client=redis_client, limit=limit, hours=hours)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to build efficiency matrix: {e}")


@app.get("/diagnostics/preflight")
async def diagnostics_preflight(user=Depends(get_current_user)):
    try:
        return run_preflight_diagnostics()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to run preflight diagnostics: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8020)
