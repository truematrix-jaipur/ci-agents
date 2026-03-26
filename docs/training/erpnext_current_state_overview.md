# ERPNext Current State Overview (Derived from Live Runbooks)

## Deployed Setup Snapshot
- Host workspace: `/home/erpnext`
- Main stack: `/home/erpnext/frappe_docker`
- Bench site (primary): `erp.igmhealth.com`
- Runtime services expected healthy:
  - `frappe_docker-backend-1`
  - `frappe_docker-queue-short-1`
  - `frappe_docker-queue-long-1`
  - `frappe_docker-scheduler-1`
  - `frappe_docker-frontend-1`
- Backup units:
  - `erpnext-full-backup.timer`
  - `erpnext-full-backup.service`

## Installed/Referenced Apps (Operationally Relevant)
- `woocommerce_fusion`
- `igm_custom`
- `igm_autonomous`
- `clefincode_chat`
- `insights`

## Baseline & Validation Contracts
- Mandatory gate before deployment work:
  - `/home/erpnext/frappe_docker/scripts/baseline-check.sh`
- Asset-impacting changes:
  - `/home/erpnext/frappe_docker/scripts/build-assets.sh <site>`
- Post-change validations:
  - route checks (`/`, `/app`)
  - Error Log scan in deployment window

## Recurrent Bug/Incident Patterns
1. Missing module/importability in queue/scheduler/websocket containers after app/runtime changes.
2. Dependency drift in runtime venv causing runtime/import failures.
3. Woo sync regressions from stale watermark values or duplicate key handling.
4. Frontend asset mismatch after JS/CSS changes when assets are not rebuilt correctly.
5. OAuth/config drift across env and app settings leading to integration auth errors.

## Production-Safe Fix Patterns
- Prefer reversible app-level or config-level change before infrastructure-wide changes.
- Keep stock sync disabled unless explicitly approved in Woo flows.
- Use idempotent sync logic and explicit normalization for server identifiers (`www` vs apex).
- Restart only impacted services, not entire stack by default.

## Ownership Expectations for ERPNext Dev Agent
- Treat runbooks as source-of-truth for live behavior.
- Execute release plan via deterministic steps and persist logs.
- Include rollback plan before any non-trivial deployment.
- Handle multi-site plans as explicit ordered operations.
- Escalate to `server_agent` only for host/service-level interventions outside bench scope.
