"""
CI SEO Agent — shared MCP configuration helpers.

Keeps the project-root and agent-local .mcp.json files in sync so CLI agents and
subagents can discover the same MCP servers.
"""
import json
from pathlib import Path

from config import cfg


def build_stdio_mcp_config() -> dict:
    """Return the stdio MCP config consumed by CLI agents."""
    return {
        "mcpServers": {
            "playwright": {
                "type": "stdio",
                "command": "/usr/local/bin/playwright-mcp",
                "args": ["--headless", "--no-sandbox"],
                "env": {
                    "DEBUG": "pw:mcp",
                    "NODE_ENV": "production",
                    "PLAYWRIGHT_BROWSERS_PATH": "/home/automation/.cache/ms-playwright",
                },
            },
            "ci-seo-agent": {
                "type": "stdio",
                "command": "python3",
                "args": [str(cfg.BASE_DIR / "mcp_stdio.py")],
                "env": {
                    "PYTHONPATH": str(cfg.BASE_DIR),
                },
            },
        }
    }


def build_http_mcp_config() -> dict:
    """Return the HTTP MCP connection details for remote/agent consumers."""
    return {
        "mcpServers": {
            "ci-seo-agent": {
                "type": "http",
                "url": f"http://localhost:{cfg.API_PORT}/mcp/",
            }
        }
    }


def sync_mcp_config_files() -> dict:
    """Write synced .mcp.json files to the site root and agent root."""
    config = build_stdio_mcp_config()
    written_paths: list[str] = []
    for path in (cfg.PROJECT_MCP_JSON_PATH, cfg.AGENT_MCP_JSON_PATH):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
            written_paths.append(str(path))
        except OSError as e:
            # Some environments mount the project directory read-only; don't fail startup for this
            written_paths.append(f"skipped:{path} (error: {e})")

    return {
        "written_paths": written_paths,
        "stdio_config": config,
        "http_config": build_http_mcp_config(),
    }
