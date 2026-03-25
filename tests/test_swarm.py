import sys
import os
import json
import logging

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from agents.seo_agent.agent import SEOAgent
from agents.data_analyser.agent import DataAnalyserAgent
from agents.wordpress_tech.agent import WordPressTechAgent

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def run_simulation():
    logger.info("--- Starting Swarm Simulation ---")
    
    # Instantiate agents
    seo_agent = SEOAgent()
    data_agent = DataAnalyserAgent()
    wp_agent = WordPressTechAgent()
    
    # 1. Trigger SEO Agent
    seo_task = {
        "task": {
            "type": "full_audit",
            "url": "https://indogenmed.org"
        }
    }
    
    logger.info("Triggering SEO Agent for full audit...")
    seo_result = seo_agent.handle_task(seo_task)
    logger.info(f"SEO Agent Result: {json.dumps(seo_result, indent=2)}")
    
    # 2. Simulate Data Analyser receiving the PubSub message (since we aren't running an async loop here)
    data_task = {
        "task": {
            "type": "query_db",
            "database": "mysql",
            "query": "SELECT page_views FROM traffic_stats WHERE url = 'https://indogenmed.org'"
        }
    }
    logger.info("Triggering Data Analyser Agent (simulating PubSub receive)...")
    data_result = data_agent.handle_task(data_task)
    logger.info(f"Data Analyser Result: {json.dumps(data_result, indent=2)}")
    
    # 3. Trigger WP Tech Agent
    wp_task = {
        "task": {
            "type": "health_check",
            "site_path": "/var/www/html/indogenmed.org"
        }
    }
    logger.info("Triggering WordPress Tech Agent...")
    wp_result = wp_agent.handle_task(wp_task)
    logger.info(f"WordPress Tech Result: {json.dumps(wp_result, indent=2)}")

    # 4. Simulate Learning Loop
    from agents.skill_agent.agent import SkillAgent
    from agents.training_agent.agent import TrainingAgent
    
    skill_agent = SkillAgent()
    training_agent = TrainingAgent()
    
    logger.info("--- Starting Learning Loop Simulation ---")
    skill_task = {
        "task": {
            "type": "fetch_best_practices",
            "topic": "ecommerce seo for medical devices",
            "target_agent": "seo_agent"
        }
    }
    logger.info("Skill Agent fetching best practices...")
    skill_result = skill_agent.handle_task(skill_task)
    
    # Simulate Training Agent receiving the dispatch
    training_task = {
        "task": {
            "type": "train_agent",
            "target_agent": "seo_agent",
            "knowledge_content": "1. Use schema for medical products. 2. Optimize for FDA related keywords.",
            "source": "Skill Agent Research"
        }
    }
    logger.info("Training Agent indexing knowledge...")
    train_result = training_agent.handle_task(training_task)
    logger.info(f"Training Result: {train_result}")

if __name__ == "__main__":
    run_simulation()
