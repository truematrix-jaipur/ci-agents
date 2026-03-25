import sys
import os
import logging
import json
import uuid

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from core.db_connectors.db_manager import db_manager

logger = logging.getLogger(__name__)

class TrainingAgent(BaseAgent):
    AGENT_ROLE = "training_agent"
    SYSTEM_PROMPT = """You are the Swarm Training & Optimization Agent.
    Your role is to receive structured knowledge and best practices and 
    index them into the long-term memory (ChromaDB) of specific agents.
    
    This process 'trains' the agents to follow specific protocols and 
    use new tools or methodologies without manual code changes."""

    def handle_task(self, task_data):
        logger.info(f"Training Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "train_agent":
            return self._train_agent(task_data)
        else:
            return super().handle_task(task_data)

    def _train_agent(self, task_data):
        target_agent = task_data.get("task", {}).get("target_agent")
        content = task_data.get("task", {}).get("knowledge_content")
        source = task_data.get("task", {}).get("source", "unknown")
        
        if not target_agent or not content:
            return {"status": "error", "message": "Missing target_agent or content"}

        try:
            chroma_client = db_manager.get_chroma_client()
            # Index into target agent's specific collection
            collection_name = f"knowledge_{target_agent}"
            collection = chroma_client.get_or_create_collection(name=collection_name)
            
            # Simple chunking by paragraph if content is large
            chunks = [c.strip() for c in content.split("\n\n") if len(c.strip()) > 20]
            if not chunks: chunks = [content]
            
            ids = [str(uuid.uuid4()) for _ in chunks]
            metadatas = [{"source": source, "timestamp": str(os.times()[4])} for _ in chunks]
            
            collection.add(
                documents=chunks,
                metadatas=metadatas,
                ids=ids
            )
            
            self.log_execution(
                task=task_data,
                thought_process=f"Indexed {len(chunks)} knowledge chunks for {target_agent}.",
                action_taken=f"ChromaDB collection '{collection_name}' updated."
            )
            
            # Optionally, notify the agent that new training is available via PubSub
            self.redis_client.publish(f"task_queue_{target_agent}", json.dumps({
                "source_agent": "training_agent",
                "task": {"type": "training_update_received", "source": source}
            }))
            
            return {"status": "success", "message": f"Agent {target_agent} trained with {len(chunks)} chunks."}
        except Exception as e:
            logger.error(f"Training failed: {e}")
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    agent = TrainingAgent()
    agent.run()
