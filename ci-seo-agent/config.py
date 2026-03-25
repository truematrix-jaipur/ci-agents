"""
CI SEO Agent — Configuration
Reads from /home/agents/ci-seo-agent/.env
"""
import os
import json
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent
load_dotenv(BASE_DIR / ".env")


class Config:
    # ── Paths ──────────────────────────────────────────────────────────────
    BASE_DIR: Path = BASE_DIR
    DATA_DIR: Path = BASE_DIR / "data"
    CHROMA_DIR: Path = BASE_DIR / "data" / "chroma"
    LOGS_DIR: Path = BASE_DIR / "logs"
    CREDS_DIR: Path = BASE_DIR / "credentials"
    PROJECT_MCP_JSON_PATH: Path = Path(
        os.getenv("PROJECT_MCP_JSON_PATH", "/var/www/html/indogenmed.org/.mcp.json")
    )
    AGENT_MCP_JSON_PATH: Path = Path(
        os.getenv("AGENT_MCP_JSON_PATH", str(BASE_DIR / ".mcp.json"))
    )

    # ── Google Search Console ──────────────────────────────────────────────
    GSC_SERVICE_ACCOUNT_FILE: str = os.getenv(
        "GSC_SERVICE_ACCOUNT_FILE",
        str(BASE_DIR / "credentials" / "gsc_service_account.json"),
    )
    GSC_OAUTH_FILE: str = os.getenv(
        "GSC_OAUTH_FILE",
        "/root/.cache/google-vscode-extension/auth/application_default_credentials.json",
    )
    GSC_FORCE_SERVICE_ACCOUNT: bool = (
        os.getenv("GSC_FORCE_SERVICE_ACCOUNT", "true").lower() in ("1", "true", "yes")
    )
    GSC_SITE_URLS: list[str] = [
        s.strip()
        for s in os.getenv(
            "GSC_SITE_URLS",
            "sc-domain:indogenmed.org,https://indogenmed.org/",
        ).split(",")
        if s.strip()
    ]
    GSC_DAYS_HISTORY: int = int(os.getenv("GSC_DAYS_HISTORY", "28"))
    GSC_ROW_LIMIT: int = int(os.getenv("GSC_ROW_LIMIT", "5000"))

    # ── Google Analytics 4 ────────────────────────────────────────────────
    GA4_PROPERTY_ID: str = os.getenv("GA4_PROPERTY_ID", "250072994")
    GA4_MEASUREMENT_ID: str = os.getenv("GA4_MEASUREMENT_ID", "G-LRP6DLLB0Q")
    GA4_WEB_DATA_STREAM_ID: str = os.getenv("GA4_WEB_DATA_STREAM_ID", "")
    GA4_MP_API_SECRET: str = os.getenv("GA4_MP_API_SECRET", "")

    # ── Google APIs ───────────────────────────────────────────────────────
    GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")

    # ── LLM ───────────────────────────────────────────────────────────────
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    ANTHROPIC_MODEL: str = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    OPENAI_EMBEDDING_MODEL: str = os.getenv(
        "OPENAI_EMBEDDING_MODEL", "text-embedding-3-small"
    )
    LLM_PROVIDER_ORDER: list[str] = [
        s.strip() for s in os.getenv("LLM_PROVIDER_ORDER", "anthropic,openai").split(",") if s.strip()
    ]
    try:
        LLM_USECASE_PRIORITIES: dict = json.loads(os.getenv("LLM_USECASE_PRIORITIES", "{}"))
    except Exception:
        LLM_USECASE_PRIORITIES = {}

    # ── WordPress / WooCommerce ────────────────────────────────────────────
    WP_BASE_URL: str = os.getenv("WP_BASE_URL", "https://indogenmed.org/wp-json/wp/v2")
    WP_USER: str = os.getenv("WP_USER", "")
    WP_APP_PASSWORD: str = os.getenv("WP_APP_PASSWORD", "")
    WP_CLI_PATH: str = os.getenv("WP_CLI_PATH", "/usr/local/bin/wp")
    WP_ROOT: str = os.getenv("WP_ROOT", "/var/www/html/indogenmed.org/html")

    # ── MySQL Persistence ──────────────────────────────────────────────────
    DB_HOST: str = os.getenv("DB_HOST", "127.0.0.1")
    DB_NAME: str = os.getenv("DB_NAME", "ai-agents")
    DB_USER: str = os.getenv("DB_USER", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")

    # ── Redis ──────────────────────────────────────────────────────────────
    REDIS_HOST: str = os.getenv("REDIS_HOST", "localhost")
    REDIS_PORT: int = int(os.getenv("REDIS_PORT", "6379"))

    # ── ChromaDB ──────────────────────────────────────────────────────────
    CHROMA_SERVER_HOST: str = os.getenv("CHROMA_SERVER_HOST", "localhost")
    CHROMA_SERVER_PORT: int = int(os.getenv("CHROMA_SERVER_PORT", "8000"))
    CHROMA_COLLECTION_GSC: str = "gsc_data"
    CHROMA_COLLECTION_GA: str = "ga_data"
    CHROMA_COLLECTION_ACTIONS: str = "action_items"
    CHROMA_COLLECTION_REPORTS: str = "analysis_reports"
    CHROMA_COLLECTION_PAGES: str = "site_pages"
    CHROMA_COLLECTION_METRICS: str = "agent_metrics"

    # ── Scheduler ─────────────────────────────────────────────────────────
    SCHEDULE_FETCH_HOUR: int = int(os.getenv("SCHEDULE_FETCH_HOUR", "6"))
    SCHEDULE_FETCH_MINUTE: int = int(os.getenv("SCHEDULE_FETCH_MINUTE", "0"))
    SCHEDULE_IMPLEMENT_HOUR: int = int(os.getenv("SCHEDULE_IMPLEMENT_HOUR", "7"))
    SCHEDULE_IMPLEMENT_MINUTE: int = int(os.getenv("SCHEDULE_IMPLEMENT_MINUTE", "30"))
    SCHEDULE_VALIDATE_HOUR: int = int(os.getenv("SCHEDULE_VALIDATE_HOUR", "18"))
    SCHEDULE_VALIDATE_MINUTE: int = int(os.getenv("SCHEDULE_VALIDATE_MINUTE", "0"))
    SCHEDULE_IMPACT_HOUR: int = int(os.getenv("SCHEDULE_IMPACT_HOUR", "9"))
    SCHEDULE_IMPACT_MINUTE: int = int(os.getenv("SCHEDULE_IMPACT_MINUTE", "0"))
    PAUSE_SCHEDULED_FETCH: bool = os.getenv("PAUSE_SCHEDULED_FETCH", "false").lower() in ("1", "true", "yes")
    LOCK_DIR: Path = Path(os.getenv("LOCK_DIR", str(BASE_DIR / ".locks")))
    LOCK_STALE_SECONDS: int = int(os.getenv("LOCK_STALE_SECONDS", "3600"))
    PROVIDER_DISABLE_TTL_SECONDS: int = int(os.getenv("PROVIDER_DISABLE_TTL_SECONDS", "21600"))

    # ── FastAPI ────────────────────────────────────────────────────────────
    API_HOST: str = os.getenv("API_HOST", "0.0.0.0")
    API_PORT: int = int(os.getenv("API_PORT", "9001"))
    API_SECRET: str = os.getenv("API_SECRET", "")

    # ── Thresholds ───────────────────────────────────────────────
    CTR_DROP_THRESHOLD: float = float(os.getenv("CTR_DROP_THRESHOLD", "0.3"))
    POSITION_DROP_THRESHOLD: float = float(os.getenv("POSITION_DROP_THRESHOLD", "5.0"))
    MIN_IMPRESSIONS: int = int(os.getenv("MIN_IMPRESSIONS", "50"))
    LOW_CTR_IMPRESSION_MIN: int = int(os.getenv("LOW_CTR_IMPRESSION_MIN", "100"))
    LOW_CTR_RATE_MAX: float = float(os.getenv("LOW_CTR_RATE_MAX", "0.02"))


cfg = Config()
