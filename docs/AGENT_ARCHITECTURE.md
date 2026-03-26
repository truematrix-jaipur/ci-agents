# TrueMatrix Agent Architecture

Last updated: 2026-03-26 (UTC)

## 1) Architecture Principles

- Single owner per operational domain (no duplicated runtime authority).
- Backward compatibility via role alias routing, not duplicate long-running workers.
- Agent capability, requirements, and deprecation status are source-of-truth in `core/agent_catalog.py`.
- Cross-agent actions flow through Redis pub/sub queues (`task_queue_<role>`).

## 2) Canonical Runtime Topology

- API entrypoint: `core/api_server.py`
- Core base class: `core/base_agent.py`
- Agent catalog and routing policy: `core/agent_catalog.py`
- Canonical runtime ops owner: `server_agent`
- Legacy compatibility alias: `devops_agent -> server_agent`

## 3) Agent Inventory

### Canonical Agents

- `wordpress_tech`: WP CLI health and WordPress operational checks.
- `seo_agent`: SEO orchestration, autonomous pipeline, GA4/GSC reporting, action workflows.
- `data_analyser`: parameterized read-only SQL querying and metrics analysis.
- `integration_agent`: WooCommerce to ERPNext bridge and stock checks.
- `erpnext_agent`: ERP customer lookup and sales order creation.
- `erpnext_dev_agent`: ERP schema/dev operations and delegated runtime fixes.
- `server_agent`: system audit, service recovery, resource operations, container/system metrics.
- `design_agent`: creative prompt generation.
- `growth_agent`: growth planning and multi-agent delegation.
- `campaign_planner_agent`: cross-channel budget planning and dispatch.
- `email_marketing_agent`: SMTP-based newsletter dispatch.
- `google_agent`: Google Cloud API management and GA4/GSC fetch paths.
- `fb_campaign_manager`: campaign bid and budget operations.
- `smo_agent`: social posting operations.
- `skill_agent`: knowledge research and training dispatch.
- `training_agent`: knowledge indexing into per-agent Chroma collections.
- `agent_builder`: scaffold generation for new agent modules.

### Deprecated Compatibility Alias

- `devops_agent`:
  - Status: deprecated compatibility route.
  - Canonical route: `server_agent`.
  - Kept for existing callers and queues; avoid starting as primary runtime owner.

## 4) Subagent Design

- `agents/server_agent/subagents/runtime_ops.py` (`RuntimeOpsSubagent`):
  - Shared execution for:
    - `check_container_status`
    - `get_system_metrics`
  - Used by both `server_agent` and compatibility `devops_agent`.

## 5) Routing and Queue Semantics

- API task publishing resolves aliases with `resolve_agent_role(...)` before publishing.
- `/task` and `/webhook/{agent_role}/{task_type}` return both requested role and `routed_to` role.
- `server_agent` subscribes to `task_queue_devops_agent` for legacy producers.

## 6) Cross-Agent Delegation Map

- `campaign_planner_agent` -> `google_agent`, `fb_campaign_manager`.
- `growth_agent` -> `data_analyser`, `seo_agent` (canonical GA4 summary path).
- `integration_agent` -> `erpnext_agent`.
- `erpnext_dev_agent` -> `server_agent` (service fix delegation).
- `skill_agent` -> `training_agent`.
- `seo_agent` -> `data_analyser` and internal subagents.

## 7) Tooling and Permission Readiness

Static per-agent requirements are declared in `core/agent_catalog.py` and exposed by:

- `GET /diagnostics/preflight`:
  - Includes `agent_runtime_readiness` with:
    - `missing_env`
    - `missing_binaries`
- `scripts/agent_healthcheck.py`:
  - Uses the catalog as source-of-truth.
  - Reports per-agent requirement gaps and smoke execution status.

Examples of requirement types:

- Env credentials: `ERP_*`, `WC_*`, `SMTP_*`, `SEO_API_SECRET`, `GOOGLE_SERVICE_ACCOUNT_PATH`.
- Binaries: `wp`, `systemctl`, `journalctl`, `ps`.

## 8) Startup and Training Policy

- Startup (`start_swarm.sh`) runs canonical agents; deprecated `devops_agent` is omitted from primary process startup.
- Training bootstrap (`scripts/train_all_agents.py`) targets canonical, non-deprecated roles only.

## 9) Operational Guardrails

- Use `server_agent` for runtime/system operations.
- Keep alias-only agents (`devops_agent`) for backward compatibility until clients are migrated.
- Add new agents only through catalog registration, then wire startup/healthcheck automatically via catalog consumers.
- For new operational capabilities, prefer adding subagents under the domain owner rather than creating a second peer owner.

## 10) Migration Guidance

- Existing producers can continue publishing to `devops_agent` queues during migration.
- New producers should publish directly to `server_agent`.
- Remove `devops_agent` alias only after all clients and dashboards stop referencing it.
