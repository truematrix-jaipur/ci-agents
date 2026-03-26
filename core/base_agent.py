import uuid
import json
import logging
import sys
import os
import time
from datetime import datetime

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from core.llm_gateway.gateway import llm_gateway
from core.db_connectors.db_manager import db_manager

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
            "task": task_payload
        }
        target_channel = f"task_queue_{target_agent_role}"
        self.redis_client.publish(target_channel, json.dumps(message))
        logger.info(f"Published task to {target_agent_role}: {task_payload}")
        return task_payload["task_id"]

    def run(self):
        """Main loop: Listen for tasks and ensure we ALWAYS reply."""
        logger.info(f"Agent {self.AGENT_ROLE} ({self.agent_id}) started.")
        while True:
            try:
                message = self.pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message['type'] == 'message':
                    data = json.loads(message['data'])
                    logger.info(f"Processing task: {data}")
                    self._publish_task_event(
                        task_context=data,
                        event_type="accepted",
                        message=f"{self.AGENT_ROLE} accepted task",
                        status="info",
                        payload={"task_type": data.get("task", {}).get("type")},
                    )
                    
                    try:
                        result = self.handle_task(data)
                        self._publish_task_event(
                            task_context=data,
                            event_type="completed",
                            message="Task completed",
                            status="success",
                            payload=result if isinstance(result, dict) else {"result": str(result)},
                        )
                        # If handling is successful but silent, send a wrap-up
                        if result and isinstance(result, dict):
                            msg = result.get("message", "Task completed successfully.")
                            self.speak(msg, task_context=data)
                        elif result:
                            self.speak(str(result), task_context=data)
                    except Exception as e:
                        error_msg = f"❌ Error executing task: {str(e)}"
                        logger.error(error_msg)
                        self._publish_task_event(
                            task_context=data,
                            event_type="failed",
                            message=error_msg,
                            status="error",
                        )
                        self.speak(error_msg, task_context=data)
                        
                time.sleep(0.1)
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
                self.handle_task(data)
            except Exception as e:
                logger.error(f"Error: {e}")

    def handle_task(self, task_data):
        """Default task handler. Can be overridden by subclasses."""
        task_type = task_data.get("task", {}).get("type")
        
        if task_type == "manual_command":
            return self._handle_manual_command(task_data)
            
        raise NotImplementedError(f"Agent {self.AGENT_ROLE} does not implement task type: {task_type}")

    def _handle_manual_command(self, task_data):
        """Processes a free-text command from the user using the LLM."""
        command = task_data.get("task", {}).get("command")
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
