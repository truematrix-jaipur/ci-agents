# Swarm AI Agent Army - Deployment Guide

## Overview
This repository implements a modular, anti-hallucination-first Swarm AI Agent architecture. Each agent is specialized in a domain (SEO, WordPress, ERPNext, Marketing, etc.) and uses a centralized Redis communication layer to collaborate.

## Directory Structure
- `/home/agents/core/`: Base agent classes, LLM gateway, and DB connectors.
- `/home/agents/agents/`: Specialized agent implementations.
- `/home/agents/config/`: Configuration settings and environment variables.
- `/home/agents/tests/`: Integration and simulation tests.

## Key Agents
- **WordPress Tech**: Manages WP-CLI and site configurations.
- **SEO Agent**: Orchestrates SEO audits via sub-agents like Speed Optimizer.
- **Data Analyser**: Factual data retrieval from MySQL/ERPNext DBs.
- **ERPNext Agent**: Handles functional workflows in ERPNext.
- **Growth Agent**: Chief strategist coordinating marketing efforts.
- **Integration Agent**: WooCommerce-to-ERPNext data bridge.

## Operational Mandates
1. **Anti-Hallucination**: Agents NEVER assume data. They must query the database or use a tool.
2. **Database First**: MySQL and ERPNext DB are the sources of truth.
3. **Communication**: Redis Pub/Sub is used for inter-agent task delegation.

## Setup
1. **Credentials**: Edit `/home/agents/.env` with actual API keys and DB passwords.
2. **Environment**: Ensure Python 3.12+ is installed with dependencies from `requirements.txt`.
3. **Execution**: Run agents in the background to listen for tasks.
   ```bash
   python3 agents/seo_agent/agent.py &
   python3 agents/data_analyser/agent.py &
   # ... etc
   ```

## Running the Simulation
Execute the test script to verify agent-to-agent communication:
```bash
python3 tests/test_swarm.py
```
