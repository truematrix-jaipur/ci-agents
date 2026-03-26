# MCP Connections Runbook

## Scope

This runbook documents the MCP connectivity baseline used by Codex CLI, Codex IDE extension, and desktop/editor integrations on this server.

## Source Of Truth

- Primary Codex config: `/home/mcp/codex_config.toml`
- Codex symlinked config: `/root/.codex/config.toml -> /home/mcp/codex_config.toml`
- Unified JSON for desktop/editor clients: `/home/mcp/unified_mcp_config.json`
- Unified template: `/home/mcp/unified_mcp_config.template.json`
- Environment source for generation: `/home/mcp/.env`

## Verified ERPNext MCP Endpoints

- `mysql-erpnext`
  - `MYSQL_HOST=127.0.0.1`
  - `MYSQL_PORT=3307`
- `redis-erpnext`
  - `REDIS_URL=redis://172.18.0.10:6379`

## Key Fixes Applied

- Corrected stale MySQL endpoint (`172.18.0.4:3306`) to mapped host TCP (`127.0.0.1:3307`).
- Corrected stale Redis endpoint (`172.18.0.3:6379`) to active Redis container endpoint (`172.18.0.10:6379`).
- Added `openaiDeveloperDocs` server to unified MCP configs so agent MCP dependency checks are complete.
- Patched `/home/mcp/verify_env_mcp.py` to support URL-only MCP entries (no `command` key).
- Re-synced MCP JSON to desktop/editor config targets via `/home/mcp/sync_mcp.py`.

## Verification Commands

```bash
python3 /home/mcp/verify_env_mcp.py
python3 /home/mcp/sync_mcp.py
python3 scripts/agent_healthcheck.py > logs/agent_health_report.json
python3 - <<'PY'
from core.diagnostics.preflight import run_preflight_diagnostics
r = run_preflight_diagnostics()
print(r['agent_runtime_readiness']['ok'])
print(r['agent_runtime_readiness']['agents_with_missing_requirements'])
PY
```

## Expected Result

- MCP verification summary shows all configured servers `OK`.
- Agent runtime readiness reports:
  - `ok = True`
  - `agents_with_missing_requirements = 0`

## Operational Notes

- Prefer host-mapped ports for DB MCPs over ephemeral Docker IPs where possible.
- Keep unified MCP JSON and Codex TOML aligned when adding/removing servers.
- Re-run sync after any unified config change so IDE/Desktop clients inherit updates.
