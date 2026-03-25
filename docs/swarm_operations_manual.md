# TrueMatrix Swarm Operations Manual

## 1. Swarm Environment
- **Project Root:** `/home/agents`
- **MCP Configuration:** `/home/mcp`
- **Logs:** `/home/agents/logs`
- **Data/Memory:** `/home/agents/data/chroma`
- **API Base:** `/army-api` (Internal port 8020)

## 2. Core Operational Mandates
- **Anti-Hallucination:** You must never assume facts. Use tools or database queries.
- **Task Protocol:** Receive tasks via Redis PubSub (`task_queue_{role}`). Log every action using `log_execution`.
- **Delegation:** If a task involves server configuration, delegate to `server_agent`. If it involves external research, delegate to `skill_agent`.

## 3. Agent Roles (Full Swarm)
1. **wordpress_tech**: Manages WP-CLI, themes, and site health.
2. **seo_agent**: Performs audits, keyword research, and optimization.
3. **data_analyser**: Queries MySQL/Redis for factual business data.
4. **integration_agent**: Bridges external APIs (WooCommerce, Shopify) to internal ERPNext.
5. **erpnext_agent**: Handles functional workflows (Sales Orders, Items) in ERPNext.
6. **erpnext_dev_agent**: Modifies Frappe code, creates doctypes, and handles Bench.
7. **devops_agent**: Manages Docker, Nginx, and system reliability.
8. **design_agent**: Generates UI components and frontend artifacts.
9. **growth_agent**: Orchestrates multi-channel marketing strategies.
10. **campaign_planner_agent**: Plans schedules and content for FB/Google ads.
11. **email_marketing_agent**: Manages outreach, templates, and SMTP health.
12. **google_agent**: Connects to GSC, GA4, and Google Workspace.
13. **fb_campaign_manager**: Directly manages Facebook/Meta Ads API.
14. **smo_agent**: Social Media Optimization and engagement tracking.
15. **skill_agent**: Researches best practices and fetches documentation.
16. **training_agent**: Indexes knowledge into long-term memory (ChromaDB).
17. **agent_builder**: Generates code for new specialized agents.
18. **server_agent**: Guardian of system security and standard Linux SRE tasks.

## 4. How Skill Agent Works
The `skill_agent` acts as the "Brain Upgrade" unit. 
- It identifies gaps in knowledge or receives a request to research a tool.
- It uses the LLM to research technical docs or best practices.
- It structured this data and dispatches a `train_agent` task to the `training_agent`.
- The `training_agent` then stores this in the target agent's RAG database.

## 5. Workflow Example
User: "@integration_agent sync WooCommerce order 123"
1. `integration_agent` identifies order 123 in WC.
2. `integration_agent` requests `erpnext_agent` to create a Sales Order.
3. `erpnext_agent` queries `data_analyser` for the customer record.
4. All actions logged to `global_execution_log`.
