"""
SEO Agent — Configuration Adapter
Provides a `cfg` object that maps the central swarm config to the attribute
names used throughout the CI SEO agent modules.  This avoids rewriting every
module while keeping a single source of truth in config/settings.py.
"""
import sys
import os
from pathlib import Path

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from config.settings import config as _swarm_cfg


class _SEOConfig:
    """Thin proxy that exposes CI SEO config attribute names, backed by swarm config."""

    def __getattr__(self, name):
        # First check if the swarm config has the attribute directly
        if hasattr(_swarm_cfg, name):
            return getattr(_swarm_cfg, name)
        raise AttributeError(f"SEO config has no attribute '{name}'")

    # ── Explicit mappings where CI SEO names differ from swarm names ──────

    @property
    def BASE_DIR(self) -> Path:
        return _swarm_cfg.SEO_AGENT_DIR

    @property
    def DATA_DIR(self) -> Path:
        return _swarm_cfg.SEO_DATA_DIR

    @property
    def CHROMA_DIR(self) -> Path:
        return _swarm_cfg.SEO_DATA_DIR / "chroma"

    @property
    def LOGS_DIR(self) -> Path:
        return _swarm_cfg.SEO_LOGS_DIR

    @property
    def CREDS_DIR(self) -> Path:
        return _swarm_cfg.SEO_CREDS_DIR

    @property
    def API_HOST(self) -> str:
        return "0.0.0.0"

    @property
    def API_PORT(self) -> int:
        return _swarm_cfg.SEO_API_PORT

    @property
    def API_SECRET(self) -> str:
        return _swarm_cfg.SEO_API_SECRET

    @property
    def DB_HOST(self) -> str:
        return _swarm_cfg.MYSQL_HOST

    @property
    def DB_NAME(self) -> str:
        return _swarm_cfg.MYSQL_DATABASE

    @property
    def DB_USER(self) -> str:
        return _swarm_cfg.MYSQL_USER

    @property
    def DB_PASSWORD(self) -> str:
        return _swarm_cfg.MYSQL_PASSWORD


cfg = _SEOConfig()
