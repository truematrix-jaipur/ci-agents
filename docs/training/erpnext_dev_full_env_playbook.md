# ERPNext Dev Full-Environment Playbook

## Mission
`erpnext_dev_agent` owns end-to-end Frappe bench lifecycle for deployed environments:
- app install/uninstall orchestration,
- multi-site migrate/build/restart sequencing,
- patch execution ordering,
- rollback planning and execution,
- post-deploy validation and regression checks.

## Live Environment Baseline
- Workspace: `/home/erpnext`
- Frappe stack: `/home/erpnext/frappe_docker`
- Core site: `erp.igmhealth.com`
- Runtime containers: backend, queue-short, queue-long, scheduler, websocket, frontend
- Gate scripts:
  - `/home/erpnext/frappe_docker/scripts/baseline-check.sh`
  - `/home/erpnext/frappe_docker/scripts/build-assets.sh <site>`

## Production Invariants
1. Zero data loss.
2. Smallest reversible fix.
3. Baseline before mutation.
4. Multi-site actions are explicit and ordered.
5. Always include rollback plan before execution.
6. Validate public route + Error Log after each deployment.

## First-Class Task Types
- `plan_release`
- `execute_release`
- `rollback_release`

## Release Orchestration Standard
1. Build release plan:
  - target sites
  - app list
  - patch callables
  - restart service list
2. Baseline gate:
  - run baseline-check
  - abort on non-zero
3. Execute in order:
  - install apps per site
  - migrate per site
  - execute patches
  - build assets per site
  - restart runtime services
4. Persist run logs with command-level outputs.
5. Post-deploy verify:
  - route health
  - no new regression signatures in Error Log

## Rollback Workflow Standard
1. Require `run_id` from prior release log.
2. Generate default rollback plan when custom steps are not supplied.
3. Support dry-run rollback planning by default.
4. Execute rollback only with explicit `execute=true`.
5. Persist rollback log as separate artifact.

## Known Incident Patterns to Remember
- Missing runtime module in queue/scheduler containers after deploy:
  - verify mount consistency across all runtime services
  - install editable app in each runtime container when needed
- Asset mismatch after frontend-affecting change:
  - use `build-assets.sh`, not raw ad-hoc build
- Integration regressions during Woo/ERP sync:
  - preserve idempotency and watermark correctness
  - avoid replaying historical data accidentally
- Config drift in credentials/dependencies:
  - validate exact env and dependency set in runtime containers before closure

## Validation Checklist
- `python3 -m py_compile` for touched Python modules.
- baseline-check before execution.
- container/service restart limited to impacted set.
- public URL checks for root + app route.
- Error Log inspection window after deployment timestamp.

## Operator-Facing Outputs
Every release/rollback must return:
- run id
- step-by-step command status
- failing command (if any)
- persisted log path
- summary of sites/apps/patches/services touched
