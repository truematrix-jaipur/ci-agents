from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AgentSpec:
    role: str
    module_path: str
    class_name: str
    capabilities: tuple[str, ...]
    required_env: tuple[str, ...] = ()
    required_binaries: tuple[str, ...] = ()
    required_mcps: tuple[str, ...] = ()
    permission_profile: tuple[str, ...] = ()
    smoke_task: dict[str, Any] | None = None
    deprecated: bool = False
    alias_of: str | None = None


_AGENT_SPECS: tuple[AgentSpec, ...] = (
    AgentSpec(
        role="wordpress_tech",
        module_path="agents.wordpress_tech.agent",
        class_name="WordPressTechAgent",
        capabilities=("wp_cli_health", "wordpress_ops"),
        required_binaries=("wp",),
        required_mcps=("filesystem", "wordpress-indogenmed"),
        permission_profile=("filesystem:wordpress", "network:https_wordpress", "process:wp_cli"),
        smoke_task={"task": {"type": "health_check", "site_path": "/var/www/html/indogenmed.org/html"}},
    ),
    AgentSpec(
        role="seo_agent",
        module_path="agents.seo_agent.agent",
        class_name="SEOAgent",
        capabilities=("seo_orchestration", "ga4", "gsc", "autonomous_pipeline"),
        required_env=("SEO_API_SECRET",),
        required_mcps=("chromadb", "fetch", "playwright", "wordpress-indogenmed"),
        permission_profile=("filesystem:project", "network:https", "llm:anthropic_openai_gemini"),
        smoke_task={"task": {"type": "status"}},
    ),
    AgentSpec(
        role="data_analyser",
        module_path="agents.data_analyser.agent",
        class_name="DataAnalyserAgent",
        capabilities=("mysql_query", "metrics_analysis"),
        required_mcps=("mysql-igm", "mysql-erpnext", "chromadb"),
        permission_profile=("database:read_mysql", "filesystem:project"),
        smoke_task={"task": {"type": "query_db", "query": "SELECT 1"}},
    ),
    AgentSpec(
        role="integration_agent",
        module_path="agents.integration_agent.agent",
        class_name="IntegrationAgent",
        capabilities=("woocommerce_sync", "stock_check", "erp_bridge"),
        required_env=("WC_URL", "WC_INDOGENMED_CK", "WC_INDOGENMED_CS"),
        required_mcps=("wordpress-indogenmed", "erpnext-igmhealth"),
        permission_profile=("network:https_woocommerce", "network:https_erpnext"),
        smoke_task={"task": {"type": "check_stock_levels", "sku": "health-sku"}},
    ),
    AgentSpec(
        role="integrator_agent",
        module_path="agents.integration_agent.agent",
        class_name="IntegrationAgent",
        capabilities=("woocommerce_sync", "stock_check", "erp_bridge"),
        deprecated=True,
        alias_of="integration_agent",
        required_env=("WC_URL", "WC_INDOGENMED_CK", "WC_INDOGENMED_CS"),
        required_mcps=("wordpress-indogenmed", "erpnext-igmhealth"),
        permission_profile=("network:https_woocommerce", "network:https_erpnext"),
        smoke_task={"task": {"type": "check_stock_levels", "sku": "health-sku"}},
    ),
    AgentSpec(
        role="erpnext_agent",
        module_path="agents.erpnext_agent.agent",
        class_name="ERPNextAgent",
        capabilities=("erp_customer_lookup", "erp_sales_order"),
        required_env=("ERP_URL", "ERP_API_KEY", "ERP_API_SECRET"),
        required_mcps=("erpnext-igmhealth", "mysql-erpnext"),
        permission_profile=("network:https_erpnext", "database:read_erpnext"),
        smoke_task={"task": {"type": "get_customer_id", "email": "healthcheck@localhost"}},
    ),
    AgentSpec(
        role="erpnext_dev_agent",
        module_path="agents.erpnext_dev_agent.agent",
        class_name="ERPNextDevAgent",
        capabilities=(
            "doctype_creation",
            "erp_fix_delegation",
            "bench_release_planning",
            "bench_release_execution",
            "bench_rollback_workflow",
            "multi_site_release_orchestration",
        ),
        required_env=("ERP_URL", "ERP_API_KEY", "ERP_API_SECRET"),
        required_mcps=("erpnext-igmhealth", "docker", "filesystem"),
        required_binaries=("docker", "systemctl"),
        permission_profile=("network:https_erpnext", "process:ops_delegate", "process:bench_lifecycle"),
    ),
    # Canonical runtime ops owner.
    AgentSpec(
        role="server_agent",
        module_path="agents.server_agent.agent",
        class_name="ServerAgent",
        capabilities=("system_audit", "resource_optimization", "service_recovery", "container_metrics"),
        required_binaries=("systemctl", "journalctl", "ps"),
        required_mcps=("docker", "filesystem"),
        permission_profile=("system:service_control", "system:journal", "filesystem:mcp_config"),
        smoke_task={"task": {"type": "get_system_metrics"}},
    ),
    # Backward compatibility route for legacy callers.
    AgentSpec(
        role="devops_agent",
        module_path="agents.devops_agent.agent",
        class_name="DevOpsAgent",
        capabilities=("legacy_runtime_ops_route",),
        deprecated=True,
        alias_of="server_agent",
        required_mcps=("docker",),
        permission_profile=("system:compat_route_only",),
        smoke_task={"task": {"type": "get_system_metrics"}},
    ),
    AgentSpec(
        role="design_agent",
        module_path="agents.design_agent.agent",
        class_name="DesignAgent",
        capabilities=("creative_prompt_generation",),
        required_mcps=("openaiDeveloperDocs",),
        permission_profile=("llm:prompt_generation",),
    ),
    AgentSpec(
        role="growth_agent",
        module_path="agents.growth_agent.agent",
        class_name="GrowthAgent",
        capabilities=(
            "growth_planning",
            "cross_agent_delegation",
            "closed_loop_growth_execution",
            "multi_source_growth_intelligence",
            "custom_report_ingestion",
        ),
        required_mcps=(
            "chromadb",
            "mysql-igm",
            "mysql-erpnext",
            "wordpress-indogenmed",
            "erpnext-igmhealth",
            "filesystem",
            "fetch",
            "openaiDeveloperDocs",
        ),
        permission_profile=("orchestration:read_only_strategy",),
        smoke_task={"task": {"type": "plan_quarterly_growth"}},
    ),
    AgentSpec(
        role="campaign_planner_agent",
        module_path="agents.campaign_planner_agent.agent",
        class_name="CampaignPlannerAgent",
        capabilities=("campaign_budget_planning", "cross_channel_dispatch"),
        required_mcps=("chromadb",),
        permission_profile=("orchestration:cross_channel_budget",),
        smoke_task={"task": {"type": "plan_campaign"}},
    ),
    AgentSpec(
        role="email_marketing_agent",
        module_path="agents.email_marketing_agent.agent",
        class_name="EmailMarketingAgent",
        capabilities=("newsletter_dispatch",),
        required_env=("SMTP_HOST", "SMTP_USER", "SMTP_PASS"),
        required_mcps=("fetch",),
        permission_profile=("network:smtp",),
    ),
    AgentSpec(
        role="google_agent",
        module_path="agents.google_agent.agent",
        class_name="GoogleAgent",
        capabilities=("gcp_api_management", "ga4_fetch", "gsc_fetch"),
        required_env=("GOOGLE_SERVICE_ACCOUNT_PATH",),
        required_mcps=("openaiDeveloperDocs", "chromadb"),
        permission_profile=("network:google_apis",),
        smoke_task={"task": {"type": "get_ga4_conversions"}},
    ),
    AgentSpec(
        role="fb_campaign_manager",
        module_path="agents.fb_campaign_manager.agent",
        class_name="FBCampaignManagerAgent",
        capabilities=("campaign_bid_opt", "budget_updates"),
        required_mcps=("chromadb",),
        permission_profile=("network:meta_ads_api",),
        smoke_task={"task": {"type": "optimize_bidding", "campaign_id": "health"}},
    ),
    AgentSpec(
        role="smo_agent",
        module_path="agents.smo_agent.agent",
        class_name="SMOResponsiveAgent",
        capabilities=("social_posting",),
        required_mcps=("chromadb",),
        permission_profile=("network:social_platforms",),
        smoke_task={"task": {"type": "post_update", "platform": "x", "content": "health check"}},
    ),
    AgentSpec(
        role="skill_agent",
        module_path="agents.skill_agent.agent",
        class_name="SkillAgent",
        capabilities=("knowledge_research", "knowledge_dispatch"),
        required_env=("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY"),
        required_mcps=("fetch", "openaiDeveloperDocs", "chromadb"),
        permission_profile=("network:web_research", "llm:multi_provider"),
    ),
    AgentSpec(
        role="training_agent",
        module_path="agents.training_agent.agent",
        class_name="TrainingAgent",
        capabilities=("knowledge_indexing",),
        required_mcps=("chromadb",),
        permission_profile=("database:vector_write",),
    ),
    AgentSpec(
        role="agent_builder",
        module_path="agents.agent_builder.agent",
        class_name="AgentBuilder",
        capabilities=("scaffold_generation",),
        required_mcps=("filesystem",),
        permission_profile=("filesystem:codegen_write",),
    ),
)


def get_agent_specs(include_deprecated: bool = False) -> list[AgentSpec]:
    if include_deprecated:
        return list(_AGENT_SPECS)
    return [s for s in _AGENT_SPECS if not s.deprecated]


def get_agent_spec(role: str) -> AgentSpec | None:
    for spec in _AGENT_SPECS:
        if spec.role == role:
            return spec
    return None


def resolve_agent_role(role: str) -> str:
    spec = get_agent_spec(role)
    if spec and spec.alias_of:
        return spec.alias_of
    return role


def get_agent_roles(include_deprecated: bool = False) -> list[str]:
    return [s.role for s in get_agent_specs(include_deprecated=include_deprecated)]


def get_training_target_roles() -> list[str]:
    # Train only canonical/non-deprecated roles to avoid duplicate vector collections.
    return get_agent_roles(include_deprecated=False)


def get_api_catalog(include_deprecated: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for spec in get_agent_specs(include_deprecated=include_deprecated):
        out.append(
            {
                "role": spec.role,
                "deprecated": spec.deprecated,
                "routed_to": spec.alias_of,
                "capabilities": list(spec.capabilities),
                "required_env": list(spec.required_env),
                "required_binaries": list(spec.required_binaries),
                "required_mcps": list(spec.required_mcps),
                "permission_profile": list(spec.permission_profile),
            }
        )
    return out
