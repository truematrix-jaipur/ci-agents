import uuid
import json
import logging
import sys
import os
import time
import threading
from datetime import datetime
from typing import Any, Callable

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from core.llm_gateway.gateway import llm_gateway
from core.db_connectors.db_manager import db_manager
from core.task_queue import (
    ack_task,
    checkpoint_task,
    claim_task,
    enqueue_task,
    recover_stale_processing,
    touch_processing,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BaseAgent:
    """
    Abstract Base Class for all specialized agents in the swarm.
    Handles communication, LLM execution, state management, and anti-hallucination policies.
    """
    
    AGENT_ROLE = "generic"
    SYSTEM_PROMPT = """You are an AI Agent operating within the TrueMatrix Swarm Architecture. 
    
    CORE OPERATIONAL RULES:
    1. STRICT ANTI-HALLUCINATION: Never assume facts. If you lack data, you must fetch it from databases or tools.
    2. SWARM CONTEXT: You have been trained on the 'TrueMatrix Swarm Operations Manual'. Always consult your internal knowledge base (get_knowledge) to understand your specific role, the environment (/home/agents), and the delegation protocols.
    3. DELEGATION: Use publish_task_to_agent to delegate specialized tasks (e.g., server config to server_agent, research to skill_agent).
    
    If you lack data to fulfill a task, state it clearly and request the missing parameters."""

    def __init__(self, agent_id=None):
        self.agent_id = agent_id or str(uuid.uuid4())
        self.state = "idle"
        self.redis_client = db_manager.get_redis_client()
        
        # Channels to listen to
        self.role_channel = f"task_queue_{self.AGENT_ROLE}"
        self.specific_channel = f"agent_{self.AGENT_ROLE}_{self.agent_id}"
        self.queue_stale_after_seconds = max(30, int(os.getenv("TASK_STALE_AFTER_SECONDS", "900")))
        self.queue_recover_interval_seconds = max(5, int(os.getenv("TASK_RECOVER_INTERVAL_SECONDS", "30")))
        self.max_task_retries = max(0, int(os.getenv("TASK_MAX_RETRIES", "2")))
        self.autofix_agent_role = os.getenv("TASK_AUTOFIX_AGENT_ROLE", "skill_agent").strip() or "skill_agent"
        self.autonomous_idle_enabled = str(
            os.getenv("AGENT_AUTONOMOUS_IDLE_ENABLED", "true")
        ).strip().lower() in {"1", "true", "yes", "on"}
        self.autonomous_idle_interval_seconds = max(
            30, int(os.getenv("AGENT_AUTONOMOUS_IDLE_INTERVAL_SECONDS", "300"))
        )
        self._last_idle_dispatch_ts = 0.0
        self.task_summary_email_enabled = str(
            os.getenv("TASK_SUMMARY_EMAIL_ENABLED", "false")
        ).strip().lower() in {"1", "true", "yes", "on"}
        exclude_roles_raw = os.getenv("TASK_SUMMARY_EMAIL_EXCLUDE_ROLES", "email_marketing_agent")
        self.task_summary_email_exclude_roles = {
            r.strip() for r in exclude_roles_raw.split(",") if r.strip()
        }
        self.task_summary_min_interval_seconds = max(
            0, int(os.getenv("TASK_SUMMARY_MIN_INTERVAL_SECONDS", "120"))
        )
        self.task_summary_max_per_hour = max(
            1, int(os.getenv("TASK_SUMMARY_MAX_PER_HOUR", "12"))
        )
        self.task_summary_dedupe_seconds = max(
            30, int(os.getenv("TASK_SUMMARY_DEDUPE_SECONDS", "900"))
        )
        
        # Subscribe to both general role channel and specific ID channel
        self.pubsub = self.redis_client.pubsub()
        self.pubsub.subscribe(self.role_channel, self.specific_channel)
        
        logger.info(f"Initialized {self.AGENT_ROLE} agent with ID: {self.agent_id}")
        logger.info(f"Listening on channels: {self.role_channel}, {self.specific_channel}")

    def _task_id_from_context(self, task_context):
        if isinstance(task_context, dict):
            return task_context.get("task_id")
        return None

    def _publish_task_event(self, task_context, event_type, message, status="info", payload=None):
        """Persist per-task events for dashboard chat timeline + stream."""
        task_id = self._task_id_from_context(task_context)
        if not task_id:
            return
        event = {
            "timestamp": datetime.utcnow().isoformat(),
            "task_id": task_id,
            "agent_role": self.AGENT_ROLE,
            "agent_id": self.agent_id,
            "event_type": event_type,
            "status": status,
            "message": message,
            "payload": payload or {},
        }
        key = f"task_events:{task_id}"
        try:
            self.redis_client.rpush(key, json.dumps(event))
            self.redis_client.expire(key, 3600)
            self.redis_client.lpush("dashboard_recent_tasks", task_id)
            self.redis_client.ltrim("dashboard_recent_tasks", 0, 199)
            self.redis_client.expire("dashboard_recent_tasks", 3600 * 24)
            self.redis_client.publish(f"task_response_{task_id}", json.dumps(event))
        except Exception as e:
            logger.error(f"Failed to publish task event for {task_id}: {e}")

    def _compose_user_result_message(self, result: Any) -> str:
        if isinstance(result, dict):
            message = str(result.get("message") or "Task completed successfully.")
            detail_keys = (
                "summary",
                "findings",
                "implementation",
                "report",
                "details",
                "actions",
                "metrics",
                "ci_status",
            )
            details = {k: result.get(k) for k in detail_keys if k in result and result.get(k) not in (None, "", [], {})}
            if not details:
                return message
            try:
                compact = json.dumps(details, ensure_ascii=True)[:1200]
                return f"{message}\nDetails: {compact}"
            except Exception:
                return message
        if result:
            return str(result)
        return "Task completed successfully."

    def log_execution(self, task, thought_process, action_taken, status="success"):
        """Logs execution to Redis for the UI Live Stream."""
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "agent_role": self.AGENT_ROLE,
            "agent_id": self.agent_id,
            "task": task,
            "thought_process": thought_process,
            "action_taken": action_taken,
            "status": status
        }
        try:
            self.redis_client.lpush("global_execution_log", json.dumps(log_entry))
            self.redis_client.ltrim("global_execution_log", 0, 999)
        except Exception as e:
            logger.error(f"Failed to log execution: {e}")

    def speak(self, message, task_context=None):
        """Sends a direct message to the user UI via the execution log."""
        self._publish_task_event(
            task_context=task_context,
            event_type="agent_message",
            message=message,
            status="info",
        )
        self.log_execution(
            task=task_context or "Direct Communication",
            thought_process="Communicating with user via Chat.",
            action_taken=message,
            status="info"
        )

    def get_knowledge(self, query, limit=3):
        """Retrieves trained knowledge from ChromaDB for RAG"""
        try:
            chroma_client = db_manager.get_chroma_client()
            collection = chroma_client.get_or_create_collection(name=f"knowledge_{self.AGENT_ROLE}")
            results = collection.query(query_texts=[query], n_results=limit)
            return results.get("documents", [[]])[0]
        except Exception as e:
            logger.error(f"Knowledge retrieval failed: {e}")
            return []

    def execute_llm(self, prompt, provider="anthropic", temperature=0.2, use_knowledge=True):
        """Wrapper for LLM Gateway executing with Anti-Hallucination prompt and RAG context"""
        context = ""
        if use_knowledge:
            knowledge = self.get_knowledge(prompt)
            if knowledge:
                context = "\n\nRELEVANT TRAINED KNOWLEDGE:\n" + "\n".join(knowledge)

        anti_hallucination_suffix = "\n\nCRITICAL: Do not hallucinate data. Base all responses ONLY on provided context."
        full_system_prompt = self.SYSTEM_PROMPT + context + anti_hallucination_suffix
        
        logger.info(f"Agent {self.agent_id} calling {provider} LLM")
        return llm_gateway.execute(
            prompt=prompt,
            provider=provider,
            system_prompt=full_system_prompt,
            temperature=temperature
        )

    def publish_task_to_agent(self, target_agent_role, task_payload):
        """Sends a task to another class of agent"""
        if not isinstance(task_payload, dict):
            raise ValueError("task_payload must be a dictionary")

        # Ensure every delegated task has a stable task id for traceability/correlation.
        task_payload = dict(task_payload)
        task_payload.setdefault("task_id", str(uuid.uuid4()))

        message = {
            "source_agent": self.AGENT_ROLE,
            "source_id": self.agent_id,
            "task_id": task_payload["task_id"],
            "task": task_payload
        }
        enqueue_task(redis_client=self.redis_client, role=target_agent_role, message=message)
        logger.info(f"Published task to {target_agent_role}: {task_payload}")
        return task_payload["task_id"]

    def _consume_legacy_pubsub_into_durable_queue(self):
        """
        Compatibility bridge:
        If any publisher still only emits Pub/Sub messages, mirror them into durable queue.
        """
        message = self.pubsub.get_message(ignore_subscribe_messages=True, timeout=0.01)
        if not message or message.get("type") != "message":
            return
        try:
            data = json.loads(message.get("data"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        task = data.get("task")
        if not isinstance(task, dict):
            return
        if not data.get("task_id"):
            data["task_id"] = task.get("task_id") or str(uuid.uuid4())
        enqueue_task(redis_client=self.redis_client, role=self.AGENT_ROLE, message=data)

    def _normalize_task_envelope(self, task_data: Any) -> dict:
        """
        Ensure every inbound task passed to agent handlers has a dict payload at `task`.
        This avoids downstream `task_data.get("task", {}).get(...)` crashes in legacy agents.
        """
        if not isinstance(task_data, dict):
            return {
                "task": {"type": "malformed_task_payload", "raw_task": task_data},
            }

        normalized = dict(task_data)
        payload = normalized.get("task")
        if isinstance(payload, dict):
            return normalized
        if payload is None:
            normalized["task"] = {}
            return normalized

        normalized["task"] = {"type": "malformed_task_payload", "raw_task": payload}
        return normalized

    def _start_heartbeat(self, task_id: str):
        stop_event = threading.Event()

        def _beat():
            while not stop_event.wait(5):
                try:
                    touch_processing(
                        redis_client=self.redis_client,
                        task_id=task_id,
                        role=self.AGENT_ROLE,
                        agent_id=self.agent_id,
                        stage="running",
                    )
                except Exception:
                    pass

        thread = threading.Thread(target=_beat, daemon=True)
        thread.start()
        return stop_event

    def _process_claimed_task(self, raw_message: str):
        try:
            data = json.loads(raw_message)
        except Exception as e:
            logger.error(f"Dropping invalid task payload: {e}")
            return

        data = self._normalize_task_envelope(data)

        task_id = str(data.get("task_id") or data.get("task", {}).get("task_id") or str(uuid.uuid4()))
        data["task_id"] = task_id
        retry_count = int(data.get("retry_count", 0) or 0)
        logger.info(f"Processing durable task: {task_id}")
        task_payload = data.get("task", {})
        task_type = task_payload.get("type") if isinstance(task_payload, dict) else None
        error_text = None

        touch_processing(
            redis_client=self.redis_client,
            task_id=task_id,
            role=self.AGENT_ROLE,
            agent_id=self.agent_id,
            stage="accepted",
        )
        self._publish_task_event(
            task_context=data,
            event_type="accepted",
            message=f"{self.AGENT_ROLE} accepted task",
            status="info",
            payload={"task_type": task_type},
        )

        heartbeat_stop = self._start_heartbeat(task_id)
        final_status = "completed"
        try:
            result = self.handle_task(data)
            touch_processing(
                redis_client=self.redis_client,
                task_id=task_id,
                role=self.AGENT_ROLE,
                agent_id=self.agent_id,
                stage="completed",
            )
            self._publish_task_event(
                task_context=data,
                event_type="completed",
                message="Task completed",
                status="success",
                payload=result if isinstance(result, dict) else {"result": str(result)},
            )
            self._maybe_email_task_summary(
                task_context=data,
                task_type=task_type,
                status="completed",
                result=result,
                error_text=None,
            )
            self.speak(self._compose_user_result_message(result), task_context=data)
        except Exception as e:
            error_msg = f"❌ Error executing task: {str(e)}"
            error_text = str(e)
            logger.error(error_msg)
            touch_processing(
                redis_client=self.redis_client,
                task_id=task_id,
                role=self.AGENT_ROLE,
                agent_id=self.agent_id,
                stage="failed",
            )
            self._publish_task_event(
                task_context=data,
                event_type="failed",
                message=error_msg,
                status="error",
            )
            self._maybe_email_task_summary(
                task_context=data,
                task_type=task_type,
                status="failed",
                result=None,
                error_text=error_text,
            )
            # Auto-retry failed tasks so agents can pick them again autonomously.
            if retry_count < self.max_task_retries:
                retry_payload = dict(data)
                retry_payload["retry_count"] = retry_count + 1
                retry_payload["last_error"] = str(e)
                enqueue_task(redis_client=self.redis_client, role=self.AGENT_ROLE, message=retry_payload)
                final_status = "retried"
                checkpoint_task(
                    redis_client=self.redis_client,
                    task_id=task_id,
                    role=self.AGENT_ROLE,
                    status="retried",
                    agent_id=self.agent_id,
                    extra={"retry_count": retry_count + 1, "last_error": str(e)},
                )
                self._publish_task_event(
                    task_context=data,
                    event_type="retried",
                    message=f"Task requeued for retry {retry_count + 1}/{self.max_task_retries}",
                    status="warning",
                    payload={"retry_count": retry_count + 1, "max_retries": self.max_task_retries},
                )
            else:
                final_status = "failed"
                checkpoint_task(
                    redis_client=self.redis_client,
                    task_id=task_id,
                    role=self.AGENT_ROLE,
                    status="failed",
                    agent_id=self.agent_id,
                    extra={"retry_count": retry_count, "last_error": str(e)},
                )

            # Auto-escalate failures to fixer agent that can use available CLI tools.
            if self.AGENT_ROLE != self.autofix_agent_role:
                try:
                    autofix_task_id = self.publish_task_to_agent(
                        self.autofix_agent_role,
                        {
                            "type": "manual_command",
                            "command": (
                                f"Autofix request for failed task.\n"
                                f"source_agent={self.AGENT_ROLE}\n"
                                f"task_id={task_id}\n"
                                f"retry_count={retry_count}\n"
                                f"error={str(e)}\n"
                                f"Use available CLI agents/tools to diagnose and fix root cause, then report remediation."
                            ),
                            "source_task_id": task_id,
                        },
                    )
                    self._publish_task_event(
                        task_context=data,
                        event_type="autofix_dispatched",
                        message=f"Autofix task dispatched to {self.autofix_agent_role}",
                        status="warning",
                        payload={"autofix_task_id": autofix_task_id, "source_task_id": task_id},
                    )
                except Exception as sub_e:
                    logger.error(f"Failed to publish autofix task: {sub_e}")

            self.speak(error_msg, task_context=data)
        finally:
            heartbeat_stop.set()
            ack_task(
                redis_client=self.redis_client,
                role=self.AGENT_ROLE,
                raw_message=raw_message,
                task_id=task_id,
                status=final_status,
                agent_id=self.agent_id,
            )

    def _maybe_email_task_summary(
        self,
        task_context: dict,
        task_type: str | None,
        status: str,
        result: Any | None,
        error_text: str | None,
    ):
        if not self.task_summary_email_enabled:
            return
        if self.AGENT_ROLE in self.task_summary_email_exclude_roles:
            return
        if self.AGENT_ROLE == "email_marketing_agent":
            return
        task = self._extract_task_payload(task_context)
        # Avoid recursive notification storms for summary/newsletter tasks.
        if task.get("type") in {"send_newsletter", "send_autonomous_summary"}:
            return

        task_id = str(task_context.get("task_id") or task.get("task_id") or "")
        source_agent = str(task_context.get("source_agent") or "unknown")
        if not self._task_summary_email_allowed(
            task_id=task_id,
            task_type=(task_type or task.get("type") or "unknown"),
            status=status,
            source_agent=source_agent,
        ):
            return

        details = self._build_task_summary_details(
            task_context=task_context,
            task_type=task_type,
            status=status,
            result=result,
            error_text=error_text,
        )
        summary = {
            "task_id": task_id,
            "agent_role": self.AGENT_ROLE,
            "status": status,
            "task_type": task_type or task.get("type") or "unknown",
            "source_agent": source_agent,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "details": details,
        }
        if isinstance(result, dict):
            summary["message"] = str(result.get("message") or "")
        if error_text:
            summary["error"] = error_text[:1000]
        body = self._render_task_summary_email_body(summary)
        try:
            self.publish_task_to_agent(
                "email_marketing_agent",
                {
                    "type": "send_autonomous_summary",
                    "subject": f"[Task Summary] {self.AGENT_ROLE} {status} ({summary['task_type']})",
                    "body": body,
                    "source": "task_summary_notifier",
                },
            )
        except Exception as e:
            logger.error(f"Failed to enqueue task summary email: {e}")

    @staticmethod
    def _compact_json(value: Any, max_len: int = 1000) -> str:
        try:
            raw = json.dumps(value, ensure_ascii=True)
        except Exception:
            raw = str(value)
        return raw[:max_len]

    def _task_summary_email_allowed(
        self,
        task_id: str,
        task_type: str,
        status: str,
        source_agent: str,
    ) -> bool:
        redis_client = getattr(self, "redis_client", None)
        if redis_client is None:
            return True
        now = time.time()
        role = self.AGENT_ROLE

        try:
            # 1) Short-term dedupe for repeated same-type/status notifications.
            signature = f"{role}|{task_type}|{status}|{source_agent}"
            dedupe_key = f"task_summary_dedupe:{signature}"
            created = redis_client.setnx(dedupe_key, "1")
            if not created:
                return False
            redis_client.expire(dedupe_key, self.task_summary_dedupe_seconds)
        except Exception:
            pass

        try:
            # 2) Minimum interval per role.
            if self.task_summary_min_interval_seconds > 0:
                last_key = f"task_summary_last_sent:{role}"
                raw_last = redis_client.get(last_key)
                if raw_last is not None:
                    if isinstance(raw_last, bytes):
                        raw_last = raw_last.decode("utf-8", errors="replace")
                    last_ts = float(str(raw_last or "0"))
                    if (now - last_ts) < self.task_summary_min_interval_seconds:
                        return False
                redis_client.set(
                    last_key,
                    str(now),
                    ex=max(60, self.task_summary_min_interval_seconds * 10),
                )
        except Exception:
            pass

        try:
            # 3) Per-hour cap per role.
            hour_bucket = datetime.utcnow().strftime("%Y%m%d%H")
            bucket_key = f"task_summary_hourly:{role}:{hour_bucket}"
            count = int(redis_client.incr(bucket_key))
            redis_client.expire(bucket_key, 3700)
            if count > self.task_summary_max_per_hour:
                return False
        except Exception:
            pass
        return True

    def _build_task_summary_details(
        self,
        task_context: dict,
        task_type: str | None,
        status: str,
        result: Any | None,
        error_text: str | None,
    ) -> dict[str, Any]:
        task = self._extract_task_payload(task_context)
        source_agent = str(task_context.get("source_agent") or "unknown")

        why = (
            str(task.get("objective") or "").strip()
            or str(task.get("reason") or "").strip()
            or str(task.get("source") or "").strip()
            or f"Execute task type '{task_type or task.get('type') or 'unknown'}'."
        )

        where_candidates = [
            task.get("site"),
            task.get("url"),
            task.get("domain"),
            task.get("site_url"),
            task.get("site_path"),
            task.get("project_id"),
            task.get("property_id"),
            task.get("campaign_name"),
            task.get("platform"),
        ]
        where = ", ".join([str(v).strip() for v in where_candidates if str(v or "").strip()]) or "not_specified"

        whom_candidates = [
            source_agent,
            task.get("target_agent"),
            task.get("email"),
            task.get("customer_email"),
            task.get("assignee"),
        ]
        whom = ", ".join(
            [str(v).strip() for v in whom_candidates if str(v or "").strip()]
        ) or "system_internal"

        expected_outcome = (
            str(task.get("expected_outcome") or "").strip()
            or str(task.get("expected_impact") or "").strip()
            or str(task.get("objective") or "").strip()
            or "Task completed with verified data and actionable result."
        )

        current_message = ""
        if isinstance(result, dict):
            current_message = str(result.get("message") or result.get("status") or "").strip()
        if not current_message and error_text:
            current_message = str(error_text).strip()

        return {
            "why": why,
            "where": where,
            "whom": whom,
            "expected_outcome": expected_outcome,
            "current_status": status,
            "current_message": current_message or "No message provided.",
            "task_payload_compact": self._compact_json(task, max_len=1200),
        }

    def _render_task_summary_email_body(self, summary: dict[str, Any]) -> str:
        details = summary.get("details", {}) if isinstance(summary, dict) else {}
        lines = [
            "<h3>Autonomous Task Summary</h3>",
            f"<p><strong>Agent:</strong> {summary.get('agent_role','')}</p>",
            f"<p><strong>Task ID:</strong> {summary.get('task_id','')}</p>",
            f"<p><strong>Task Type:</strong> {summary.get('task_type','')}</p>",
            f"<p><strong>Current Status:</strong> {summary.get('status','')}</p>",
            f"<p><strong>Timestamp (UTC):</strong> {summary.get('timestamp_utc','')}</p>",
            "<hr>",
            f"<p><strong>Why:</strong> {details.get('why','')}</p>",
            f"<p><strong>Where:</strong> {details.get('where','')}</p>",
            f"<p><strong>Whom:</strong> {details.get('whom','')}</p>",
            f"<p><strong>Expected Outcome:</strong> {details.get('expected_outcome','')}</p>",
            f"<p><strong>Current Message:</strong> {details.get('current_message','')}</p>",
            "<details><summary><strong>Payload Snapshot</strong></summary>",
            f"<pre>{details.get('task_payload_compact','')}</pre>",
            "</details>",
            "<hr>",
            "<details><summary><strong>Raw Summary JSON</strong></summary>",
            f"<pre>{json.dumps(summary, indent=2)}</pre>",
            "</details>",
        ]
        return "\n".join(lines)

    def run(self):
        """Main loop: durable queue + checkpoint + crash recovery."""
        logger.info(f"Agent {self.AGENT_ROLE} ({self.agent_id}) started.")
        last_recovery = 0.0
        while True:
            try:
                now = time.time()
                if now - last_recovery >= self.queue_recover_interval_seconds:
                    recovered = recover_stale_processing(
                        redis_client=self.redis_client,
                        role=self.AGENT_ROLE,
                        stale_after_seconds=self.queue_stale_after_seconds,
                    )
                    if recovered:
                        logger.warning(f"Recovered {recovered} stale task(s) for {self.AGENT_ROLE}")
                    last_recovery = now

                self._consume_legacy_pubsub_into_durable_queue()

                raw_message = claim_task(
                    redis_client=self.redis_client,
                    role=self.AGENT_ROLE,
                    timeout_seconds=1,
                )
                if raw_message:
                    self._process_claimed_task(raw_message)
                else:
                    self._maybe_dispatch_idle_autonomous_task(now=time.time())
            except Exception as e:
                logger.error(f"Critical loop error: {e}")
                time.sleep(1)

    def process_incoming_tasks(self):
        """Legacy helper for one-off checks"""
        while True:
            message = self.pubsub.get_message(ignore_subscribe_messages=True)
            if not message: break
            try:
                data = json.loads(message['data'])
                self.handle_task(self._normalize_task_envelope(data))
            except Exception as e:
                logger.error(f"Error: {e}")

    def handle_task(self, task_data):
        """Default task handler. Can be overridden by subclasses."""
        payload = self._extract_task_payload(task_data)
        task_type = payload.get("type")

        if task_type == "autonomous_self_check":
            return {
                "status": "success",
                "message": f"{self.AGENT_ROLE} autonomous self-check complete.",
                "verification": {
                    "role": self.AGENT_ROLE,
                    "agent_id": self.agent_id,
                    "timestamp_utc": datetime.utcnow().isoformat(),
                    "queue_channels": {
                        "role_channel": self.role_channel,
                        "specific_channel": self.specific_channel,
                    },
                },
            }

        if task_type == "manual_command":
            return self._handle_manual_command(task_data)

        # Avoid hard-failing the loop for unsupported cross-agent broadcasts.
        ignored = {
            t.strip()
            for t in os.getenv("AGENT_IGNORED_TASK_TYPES", "training_update_received").split(",")
            if t.strip()
        }
        if task_type in ignored:
            if task_type == "training_update_received":
                return {
                    "status": "success",
                    "message": "Training update acknowledged.",
                    "task_type": task_type,
                    "ignored": True,
                }
            return {
                "status": "warning",
                "message": f"Ignored broadcast task type: {task_type}",
                "task_type": task_type,
                "ignored": True,
            }

        return {
            "status": "warning",
            "message": f"Unsupported task type for {self.AGENT_ROLE}: {task_type}",
            "task_type": task_type,
        }

    def _execute_with_goal_target(
        self,
        task_data: dict[str, Any],
        executor: Callable[[dict[str, Any]], Any],
        operation_name: str,
    ) -> dict[str, Any]:
        # Legacy compatibility shim: execute operation once with normalized response.
        result = executor(task_data)
        return result if isinstance(result, dict) else {"status": "success", "result": result}

    def _handle_manual_command(self, task_data):
        """Processes a free-text command from the user using the LLM."""
        payload = self._extract_task_payload(task_data)
        command = payload.get("command")
        if not command:
            return {"status": "error", "message": "No command provided"}

        self.speak(f"Processing your request: \"{command}\"...", task_context=task_data)
        
        try:
            # Use LLM to generate a response based on the agent's role and knowledge
            response = self.execute_llm(prompt=command)
            return {"status": "success", "message": response}
        except Exception as e:
            return {"status": "error", "message": f"LLM Error: {str(e)}"}

    def spawn_subagent(self, subagent_class, task_payload):
        """Dynamically instantiate and trigger a subagent"""
        logger.info(f"Spawning subagent of type {subagent_class.__name__}")
        subagent = subagent_class()
        return subagent.handle_task(task_payload)

    def _extract_task_payload(self, task_data: Any) -> dict:
        """Normalize incoming task envelope to a dict payload."""
        if not isinstance(task_data, dict):
            return {}
        payload = task_data.get("task")
        if isinstance(payload, dict):
            return payload
        if payload is None:
            return {}
        return {"type": "malformed_task_payload", "raw_task": payload}

    def _maybe_dispatch_idle_autonomous_task(self, now: float | None = None):
        if not self.autonomous_idle_enabled:
            return
        ts = now if now is not None else time.time()
        if not self._idle_dispatch_due(ts):
            return
        task_payload = self._build_idle_autonomous_task()
        if not isinstance(task_payload, dict) or not task_payload.get("type"):
            return
        try:
            task_id = self.publish_task_to_agent(self.AGENT_ROLE, task_payload)
            self._last_idle_dispatch_ts = ts
            self._mark_idle_dispatch(ts)
            logger.info(
                f"Dispatched autonomous idle task for {self.AGENT_ROLE}: "
                f"{task_payload.get('type')} ({task_id})"
            )
        except Exception as e:
            logger.error(f"Failed to dispatch autonomous idle task for {self.AGENT_ROLE}: {e}")

    def _idle_dispatch_due(self, ts: float) -> bool:
        last_local = float(self._last_idle_dispatch_ts or 0.0)
        if (ts - last_local) < self.autonomous_idle_interval_seconds:
            return False
        redis_client = getattr(self, "redis_client", None)
        if redis_client is None:
            return True
        try:
            raw = redis_client.get(f"agent_idle_autonomous_last:{self.AGENT_ROLE}")
            if raw is None:
                return True
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            last_remote = float(str(raw or "0"))
            return (ts - last_remote) >= self.autonomous_idle_interval_seconds
        except Exception:
            return True

    def _mark_idle_dispatch(self, ts: float):
        redis_client = getattr(self, "redis_client", None)
        if redis_client is None:
            return
        try:
            key = f"agent_idle_autonomous_last:{self.AGENT_ROLE}"
            redis_client.set(key, str(ts), ex=max(60, self.autonomous_idle_interval_seconds * 4))
        except Exception:
            return

    @staticmethod
    def _is_valid_wp_root(path: str) -> bool:
        if not path:
            return False
        try:
            if os.path.isfile(os.path.join(path, "wp-config.php")):
                return True
            if os.path.isfile(os.path.join(path, "html", "wp-config.php")):
                return True
        except Exception:
            return False
        return False

    def _build_idle_autonomous_task(self) -> dict[str, Any] | None:
        """
        Build a safe, role-specific autonomous task when the role queue is idle.
        Tasks are intentionally verification/data-fetch oriented and non-destructive by default.
        """
        common = {
            "source": "autonomous_idle_scheduler",
            "objective": "maximize_traffic_sales_revenue_with_verified_data",
            "require_verification": True,
        }
        role = self.AGENT_ROLE

        if role == "seo_agent":
            return {
                "type": "run_autonomous_pipeline",
                **common,
            }
        if role == "google_agent":
            credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_PATH", "").strip()
            if not credentials_path or not os.path.exists(credentials_path):
                return {"type": "autonomous_self_check", **common}
            return {
                "type": "fetch_multisite_marketing_data",
                "days": 28,
                **common,
            }
        if role == "data_analyser":
            return {
                "type": "summarize_sales_trend",
                "days": 30,
                "database": "erpnext",
                **common,
            }
        if role == "growth_agent":
            return {
                "type": "plan_quarterly_growth",
                "execution_mode": "async",
                "window_days": 30,
                "total_budget": float(os.getenv("GROWTH_IDLE_TOTAL_BUDGET", "10000")),
                **common,
            }
        if role == "campaign_planner_agent":
            return {
                "type": "plan_campaign",
                "campaign_name": f"auto_growth_{datetime.utcnow().strftime('%Y%m%d')}",
                "google_budget": float(os.getenv("CAMPAIGN_IDLE_GOOGLE_BUDGET", "6000")),
                "fb_budget": float(os.getenv("CAMPAIGN_IDLE_FB_BUDGET", "4000")),
                **common,
            }
        if role == "wordpress_tech":
            wp_root = os.getenv("WP_ROOT", "/var/www/html/indogenmed.org/html")
            if not self._is_valid_wp_root(wp_root):
                return {"type": "autonomous_self_check", **common}
            return {
                "type": "health_check",
                "site_path": wp_root,
                **common,
            }
        if role == "server_agent":
            return {
                "type": "get_system_metrics",
                **common,
            }
        if role == "devops_agent":
            return {
                "type": "get_system_metrics",
                **common,
            }
        if role == "erpnext_dev_agent":
            return {
                "type": "plan_release",
                "sites": [os.getenv("ERP_SITE_DEFAULT", "erp.igmhealth.com")],
                "apps": [],
                "patches": [],
                **common,
            }
        if role == "erpnext_agent":
            email = os.getenv("ERP_IDLE_CUSTOMER_EMAIL", "").strip()
            if email:
                return {
                    "type": "get_customer_id",
                    "email": email,
                    **common,
                }
            return {"type": "autonomous_self_check", **common}
        if role == "integration_agent":
            sku = os.getenv("INTEGRATION_IDLE_SKU", "").strip()
            if sku:
                return {
                    "type": "check_stock_levels",
                    "sku": sku,
                    **common,
                }
            return {"type": "autonomous_self_check", **common}
        if role == "fb_campaign_manager":
            if os.getenv("FB_ACCESS_TOKEN", "").strip() or os.getenv("FB_USER_ACCESS_TOKEN", "").strip():
                return {
                    "type": "fetch_assets",
                    **common,
                }
            return {"type": "autonomous_self_check", **common}
        if role == "skill_agent":
            return {
                "type": "fetch_best_practices",
                "topic": "ecommerce seo, technical seo, conversion optimization, and erpnext data-driven growth",
                "target_agent": "seo_agent",
                **common,
            }
        if role == "training_agent":
            return {"type": "autonomous_self_check", **common}
        if role == "design_agent":
            return {
                "type": "generate_image_prompt",
                "topic": "high-converting ecommerce hero banner for healthcare products",
                **common,
            }
        if role == "smo_agent":
            return {"type": "autonomous_self_check", **common}
        if role == "email_marketing_agent":
            return {"type": "autonomous_self_check", **common}
        if role == "agent_builder":
            return {"type": "autonomous_self_check", **common}

        return {"type": "autonomous_self_check", **common}
