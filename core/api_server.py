from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import json
import logging
import os
import sys
import uuid
import datetime
from jose import jwt, JWTError
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from core.db_connectors.db_manager import db_manager
from core.analytics.efficiency_matrix import build_agent_efficiency_matrix
from core.diagnostics.preflight import run_preflight_diagnostics
from core.agent_catalog import get_api_catalog, get_agent_spec, resolve_agent_role
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


async def get_current_user(request: Request):
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing Authorization header")

    token = auth_header.replace("Bearer ", "")
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=["HS256"])
        return payload
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
