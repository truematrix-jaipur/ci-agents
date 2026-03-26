# MCP Autonomy Playbook

## Objective
Enable autonomous MCP lifecycle handling:
- connect new MCP servers,
- configure MCP entries safely,
- debug startup/handshake failures,
- recover broken MCP sessions quickly.

## MCP Lifecycle Workflow
1. Identify active config sources:
- `/home/mcp/codex_config.toml`
- `/home/mcp/codex_mcp_config.toml`
- `/home/mcp/unified_mcp_config.json`
- `/home/mcp/unified_mcp_config.template.json`

2. Add or update server entries consistently across TOML + JSON.

3. Validate server binary and runtime assumptions:
- `command` path exists and executable.
- runtime dependencies are present (`node`, `python3`, `npx`, `uvx`, etc.).
- env vars are actually consumed by the target server (do not assume `REDIS_URL` is supported).

4. Run MCP verification:
- `python3 /home/mcp/verify_env_mcp.py`

5. Propagate to all downstream clients:
- `python3 /home/mcp/sync_mcp.py`

6. Restart client session/process so MCP config is reloaded.

## Failure Pattern Library

### Pattern A: Initialize handshake closes immediately
Symptoms:
- `MCP startup failed: handshaking with MCP server failed: connection closed: initialize response`

Likely causes:
- server writes non-protocol text to stdout before framed MCP response,
- server expects line-delimited JSON instead of `Content-Length` framed MCP,
- command exits before processing initialize.

Fix strategy:
1. Probe executable directly.
2. Confirm protocol behavior mismatch.
3. Use protocol adapter wrapper between client and server.
4. Repoint MCP `command`/`args` to wrapper.
5. Re-validate `initialize` and `tools/list`.

### Pattern B: Server starts but wrong backend target
Symptoms:
- tools load but operations fail or hit wrong service.

Likely causes:
- stale Docker IP,
- env var ignored by server,
- incorrect host/port args.

Fix strategy:
1. inspect server source/help flags;
2. use explicit host/port args over unsupported env vars;
3. prefer stable host endpoints over ephemeral container IPs when possible.

## Redis MCP Compatibility Note
Known incompatibility class:
- `redis-mcp` variants that print startup banner and/or parse raw JSON lines.

Operational adaptation:
- route through `/home/agents/scripts/redis_mcp_stdio_wrapper.js`,
- set explicit args:
  - local: `--redis-host 127.0.0.1 --redis-port 6379`
  - ERPNext: `--redis-host 172.18.0.10 --redis-port 6379`

## Other MCP Training Modules

### MySQL MCP
- Validate endpoint first with `SELECT 1`.
- Verify host/port/user/db are correct for each environment (`mysql-igm`, `mysql-erpnext`).
- If DB connection fails, check socket vs TCP mismatch and firewall/bind-address.

### ChromaDB MCP
- Confirm service is reachable (`localhost:8000` in this stack).
- Validate collection lifecycle:
  - create/get collection,
  - add/query sample vectors,
  - verify persistence after restart.

### Filesystem MCP
- Keep allowed roots minimal and explicit.
- Reject path traversal and absolute-path escapes outside allowed roots.
- Validate read/write/list behavior on each mounted root.

### Docker MCP
- Confirm daemon socket access (`/var/run/docker.sock`).
- Validate `projects`, `compose ps`, and healthcheck operations.
- On failures, distinguish permission error vs missing compose project.

### Playwright MCP
- Verify browser binaries and headless flags are compatible with host.
- If startup fails, check cached browser path and sandbox flags.
- Validate at least one navigation + snapshot operation.

### Fetch MCP
- Validate outbound network and DNS reachability.
- Differentiate transport failures from target-site blocking/429.
- Apply domain filters for high-risk or compliance-sensitive workflows.

### GitHub MCP
- Validate PAT scope for repo operations before PR/issue automation.
- Distinguish auth errors (401/403) from repository/path errors (404).

### WordPress MCP
- Confirm WP REST and WooCommerce REST credentials separately.
- Validate with a low-risk read endpoint before any write operation.
- Include rollback plan before content/config mutations.

### ERPNext MCP
- Validate auth (`key:secret`) and site URL first.
- Use lightweight read probes before write/create doc flows.
- Log docnames/IDs for traceable recovery.

### OpenAI Docs MCP (URL-based)
- Handle URL-based MCP entries without `command`.
- Skip local initialize checks and validate via fetch/query usage.

## Autonomous Debug Routine
1. Capture exact failing server names and error string.
2. Verify current config entry and command path.
3. Execute binary with minimal args to inspect stdout/stderr behavior.
4. Send a manual initialize probe and confirm response framing.
5. Patch only minimal config lines required.
6. Re-run `verify_env_mcp.py`.
7. Run `sync_mcp.py`.
8. Report:
- root cause,
- exact files changed,
- validation commands and outcomes,
- restart instruction.

## Guardrails
- Never delete unrelated MCP entries.
- Never rotate secrets implicitly.
- Prefer reversible config changes with clear rollback path.
- Always include protocol-level validation before declaring success.
