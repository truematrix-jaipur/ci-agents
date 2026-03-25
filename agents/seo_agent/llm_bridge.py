"""
SEO Agent — LLM Bridge
Routes all LLM calls through the swarm's LLM Gateway, which already provides
multi-provider fallback (anthropic -> openai -> gemini) with retry logic.

This replaces the direct anthropic/openai SDK calls in the CI SEO analyzer
while keeping the same call signature so the analyzer code needs minimal changes.
"""
import sys
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.llm_gateway.gateway import llm_gateway

logger = logging.getLogger("seo.llm_bridge")


def call_llm(
    prompt: str,
    system_prompt: str = "",
    provider: str = "anthropic",
    temperature: float = 0.2,
    max_tokens: int = 4000,
    usecase: str = "",
) -> str:
    """
    Unified LLM call that delegates to the swarm gateway.

    Parameters match what the CI SEO analyzer expects, but execution goes
    through llm_gateway.execute() which handles retry + provider fallback.
    """
    logger.info(f"LLM call via gateway: provider={provider}, usecase={usecase}")
    return llm_gateway.execute(
        prompt=prompt,
        provider=provider,
        system_prompt=system_prompt,
        temperature=temperature,
    )
