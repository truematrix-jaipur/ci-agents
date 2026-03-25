# CI SEO Agent — Codex Baseline

Date (UTC): 2026-03-22
Service root: `/home/agents/ci-seo-agent`
API: `http://localhost:9001`
Managed by: `systemctl status ci-seo-agent`

---

## 1) File tree

```text
/home/agents/ci-seo-agent/
├── main.py               FastAPI app + lifespan (483 lines)
├── config.py             Config from .env
├── gsc_client.py         GSC API via service account
├── gsc_extended.py       Extended GSC queries (511 lines)
├── analyzer.py           LLM analysis (Claude primary, GPT-4o fallback)
├── extended_analyzer.py  Deep analysis: content briefs, schema, headings
├── implementer.py        WordPress changes via WP-CLI + REST API
├── validator.py          Guardrails + backup/rollback
├── vector_store.py       ChromaDB collections (420 lines)
├── scheduler.py          APScheduler cron jobs (455 lines)
├── notifier.py           HTML email via wp_mail()
├── mail_poller.py        IMAP poller for approval emails
├── mail_pipe.py          Email pipe handler
├── .env                  Environment config (see Section 4)
├── credentials/
│   └── gsc_service_account.json   erpnext@erpnext-486922.iam.gserviceaccount.com
├── data/
│   └── chroma/           ChromaDB persistent storage
└── logs/
    ├── agent.log         Rotating 10MB × 10
    └── actions.log       Implementation actions only
```

---

## 2) Runtime environment

```text
Python 3.12.3
PHP 8.3.30 (for WP-CLI)

Key packages:
fastapi==0.115.6
chromadb==1.5.5
anthropic==0.85.0
openai==2.29.0
APScheduler==3.11.2
```

---

## 3) Existing Python lint errors

```text
No syntax errors (all files pass python3 -m py_compile)
```

---

## 4) Environment config (non-sensitive)

```ini
# .env — sensitive values omitted
GSC_SERVICE_ACCOUNT_FILE=/home/agents/ci-seo-agent/credentials/gsc_service_account.json
GSC_SITE_URLS=sc-domain:indogenmed.org,https://indogenmed.org/
GSC_DAYS_HISTORY=28
GSC_ROW_LIMIT=5000

OPENAI_MODEL=gpt-4o
ANTHROPIC_MODEL=claude-sonnet-4-6

WP_BASE_URL=https://indogenmed.org/wp-json/wp/v2
WP_USER=admin
WP_CLI_PATH=/usr/local/bin/wp
WP_ROOT=/var/www/html/indogenmed.org

API_HOST=0.0.0.0
API_PORT=9001

SCHEDULE_FETCH_HOUR=6       # UTC — GSC fetch + analysis + approval email
SCHEDULE_IMPLEMENT_HOUR=7   # UTC — auto-implement if approved
SCHEDULE_IMPLEMENT_MINUTE=30
SCHEDULE_VALIDATE_HOUR=18   # UTC — evening validation

CTR_DROP_THRESHOLD=0.3
POSITION_DROP_THRESHOLD=5.0
MIN_IMPRESSIONS=50
LOW_CTR_IMPRESSION_MIN=100
LOW_CTR_RATE_MAX=0.02
```

---

## 5) ChromaDB collections

| Collection | Count (2026-03-22) | Purpose |
|---|---|---|
| `gsc_data` | 501 | GSC snapshots (keyword+page rows) |
| `action_items` | 37 | Action items with status tracking |
| `analysis_reports` | 10 | LLM analysis reports |
| `site_pages` | 0 | Page metadata cache |

---

## 6) Systemd service definition

```ini
# /etc/systemd/system/ci-seo-agent.service
[Unit]
Description=CI SEO Agent — Autonomous GSC Monitor for IndogenMed.org

[Service]
Type=simple
User=root
WorkingDirectory=/home/agents/ci-seo-agent
ExecStart=/usr/bin/python3 /home/agents/ci-seo-agent/main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## 7) API surface

```
GET  /                          Dashboard HTML
GET  /status                    Agent state JSON
GET  /report/latest             Latest analysis report
GET  /actions?status=pending    Action items filtered by status
GET  /logs?lines=N              Last N log lines
POST /run-now                   Trigger full pipeline (X-API-Secret header required)
POST /approve/{report_id}       Approve action plan (X-API-Secret header required)
GET  /approve/{report_id}?secret=...   Approve via email link
GET  /reject/{report_id}?secret=...    Reject via email link
```

---

## 8) Action types supported

| Action Type | Implementation Method | Requires Approval |
|---|---|---|
| `UPDATE_META_DESCRIPTION` | WP REST API (Rank Math meta) | Yes |
| `UPDATE_PAGE_TITLE` | WP REST API (Rank Math title) | Yes |
| `ADD_INTERNAL_LINK` | Manual review only | Yes + manual |
| `CREATE_CONTENT_BRIEF` | No site changes (flag only) | Yes |
| `FIX_CANONICAL` | WP REST API | Yes |
| `UPDATE_SCHEMA` | WP REST API | Yes |
| `OPTIMIZE_HEADING` | WP REST API | Yes |
| `FLAG_FOR_REVIEW` | No site changes | Yes |

---

## 9) Guardrails

- Domain whitelist: `indogenmed.org` only
- Meta description: 50–160 chars
- Title: 20–60 chars
- Prohibited medical claims: `cure`, `miracle`, `guaranteed`, `100% safe`, `no side effects`, `clinically proven`
- Pre-change backup of all Rank Math meta before writing
- Auto-rollback if post-change HTTP verification fails (non-200 status)
- Max 10 actions per pipeline run
- `ADD_INTERNAL_LINK` always routed to manual review queue
- Zero automated implementation without human approval via email click

---

## 10) Approval workflow

```
06:00 UTC  Pipeline runs:
           → GSC fetch (28 days, 5000 rows)
           → LLM analysis (Claude/GPT-4o)
           → 5–10 action items generated
           → HTML approval email → surya@truematrix.io

           Email contains:
           → Action plan table (priority, page, action type, description)
           → APPROVE button: GET /approve/{report_id}?secret=ci-seo-agent-2026
           → REJECT button:  GET /reject/{report_id}?secret=ci-seo-agent-2026

07:30 UTC  Auto-implement if approval received since 06:00
           → Backup taken before each change
           → Change applied via WP REST API
           → Verification check (HTTP 200)
           → Rollback if verification fails
           → Results email sent

18:00 UTC  Evening validation:
           → Re-checks all implemented changes
           → Flags any regressions
```

---

## 11) Integration points

| System | Connection | Purpose |
|---|---|---|
| Google Search Console | Service account JSON (OAuth2) | Fetch query/page performance data |
| WordPress REST API | App password (admin user) | Read/write post meta (Rank Math) |
| WP-CLI | `/usr/local/bin/wp --path=/var/www/html/indogenmed.org` | Post content edits |
| Anthropic Claude API | ANTHROPIC_API_KEY in .env | Primary LLM analysis |
| OpenAI GPT-4o | OPENAI_API_KEY in .env | Fallback LLM analysis |
| wp_mail() | Calls WP-CLI to invoke wp_mail() | Approval email delivery |
| ChromaDB | Local ONNX (all-MiniLM-L6-v2) | Vector storage, no external API |

---

## 12) Known issues / open items

- `ADD_INTERNAL_LINK` implementer is stub (always routes to manual review) — full NLP
  implementation deferred
- `UPDATE_SCHEMA` and `FIX_CANONICAL` require per-page logic; current implementation
  uses Rank Math REST API fields
- ANTHROPIC_API_KEY not yet set in .env (GPT-4o fallback active until key added)
- `site_pages` ChromaDB collection is empty — page metadata caching not yet implemented

---

*Generated by Claude Code on 2026-03-22. Update after major architecture changes.*
