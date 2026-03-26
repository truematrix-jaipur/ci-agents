# CLAUDE.md — TrueMatrix Swarm Agent Repository

## Working Directory

**Always work in `/home/agents`** — this is the project root for all code, agents, and configuration.

```
/home/agents/
├── agents/          # Individual agent implementations
├── core/            # BaseAgent, API server, LLM gateway, DB connectors
├── army-dashboard/  # Single-file React SPA (index.html — no build step)
├── config/          # Settings and environment configuration
├── docs/            # Training playbooks and documentation
└── scripts/         # Utility and data-fetch scripts
```

## Git Workflow

- **Primary branch:** `main`
- **Feature branches:** `feat_*`, `chore_*`, `fix_*`
- **Always push every change to git** — no local-only commits
- Merge feature branches into `main` when complete; keep `main` deployable
- Pull from `main` before starting new work to stay in sync
- Follow conventional commit messages: `feat:`, `fix:`, `chore:`, `refactor:`

## Architecture

- **Framework:** FastAPI (Python) + React 18 inline SPA (no build step, Tailwind CDN)
- **Messaging:** Redis pub/sub for inter-agent task routing
- **Databases:** MySQL (app data), ERPNext MySQL (sales/ERP), ChromaDB (vector store)
- **Auth:** JWT sessions with Google OAuth support

## Agent Development Rules

1. All agents extend `core.base_agent.BaseAgent`
2. Every agent must define `AGENT_ROLE` and `SYSTEM_PROMPT`
3. Use `_execute_with_goal_target(task_data, method, operation_name)` for all goal-trackable operations
4. Use `self.publish_task_to_agent(role, payload)` for async inter-agent delegation
5. Use `self.spawn_subagent(AgentClass, task_data)` for synchronous sub-execution
6. Never hallucinate data — all results must be backed by real API/DB calls
7. Log every significant action via `self.log_execution(...)`

## Goal & Objective System

Agents support per-operation goal targets stored in Redis (`agent_goal_target:{role}`).

- **Set via API:** `POST /agents/{role}/goal-target`
- **Set via task:** `task_type = "set_goal_target"` with `goal_target` payload
- **Fields:** `metric`, `target_value`, `comparator` (gte/lte/eq/gt/lt), `max_attempts`, `retry_delay_seconds`, `enabled`
- Goal-wired operations retry autonomously until the metric is met or attempts are exhausted

## Environment

Key environment variables are defined in `.env`. Required service accounts go in `config/`.
Do not commit `.env`, `*.json` credentials, or `config/google_*.json` to git.
