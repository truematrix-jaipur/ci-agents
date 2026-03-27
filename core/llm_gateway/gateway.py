import os
from openai import OpenAI
import anthropic
from google import genai
import logging
import sys
import time
import random
import shlex
import subprocess

# Append project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from config.settings import config

logger = logging.getLogger(__name__)

class LLMGateway:
    def __init__(self):
        self.openai_client = OpenAI(api_key=config.OPENAI_API_KEY) if config.OPENAI_API_KEY else None
        self.anthropic_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY) if config.ANTHROPIC_API_KEY else None
        
        if config.GEMINI_API_KEY:
            # Using the new google-genai SDK
            self.gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
        else:
            self.gemini_client = None
        self._provider_disabled_until = {}
        self._model_inventory_cache: dict[str, tuple[float, set[str]]] = {}

    def _provider_temporarily_disabled(self, provider: str) -> bool:
        until = self._provider_disabled_until.get(provider)
        if not until:
            return False
        if time.time() >= until:
            self._provider_disabled_until.pop(provider, None)
            return False
        return True

    def _mark_provider_temporarily_disabled(self, provider: str):
        ttl = max(60, int(getattr(config, "PROVIDER_DISABLE_TTL_SECONDS", 21600)))
        self._provider_disabled_until[provider] = time.time() + ttl
        logger.warning(f"Temporarily disabling provider {provider} for {ttl}s due to fatal auth/quota error")

    def _model_fallback_candidates(self, provider: str, requested_model: str | None) -> list[str]:
        candidates: list[str] = []

        def _add(model_name: str | None):
            if not model_name:
                return
            m = str(model_name).strip()
            if not m or m in candidates:
                return
            candidates.append(m)

        _add(requested_model)

        defaults = {
            "openai": getattr(config, "OPENAI_MODEL", "gpt-4o"),
            "anthropic": getattr(config, "ANTHROPIC_MODEL", "claude-sonnet-4-6"),
            "gemini": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        }
        _add(defaults.get(provider))

        env_map = {
            "openai": "OPENAI_MODEL_CANDIDATES",
            "anthropic": "ANTHROPIC_MODEL_CANDIDATES",
            "gemini": "GEMINI_MODEL_CANDIDATES",
        }
        for item in os.getenv(env_map[provider], "").split(","):
            _add(item)

        builtins = {
            "openai": ["gpt-4.1", "gpt-4o", "gpt-4o-mini"],
            "anthropic": ["claude-sonnet-4-6", "claude-3-7-sonnet-latest", "claude-3-5-sonnet-latest"],
            # Keep only current Gemini models to avoid repeated retries on retired model IDs.
            "gemini": ["gemini-2.5-flash", "gemini-2.5-pro"],
        }
        for b in builtins.get(provider, []):
            _add(b)
        return candidates

    def _is_model_unavailable_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        needles = (
            "not found",
            "unknown model",
            "unsupported model",
            "does not exist",
            "not available",
            "invalid model",
            "no such model",
        )
        return "model" in msg and any(n in msg for n in needles)

    def _get_available_models(self, provider: str) -> set[str]:
        cache_ttl = max(60, int(os.getenv("LLM_MODEL_INVENTORY_CACHE_SECONDS", "600")))
        now = time.time()
        cached = self._model_inventory_cache.get(provider)
        if cached and now - cached[0] <= cache_ttl:
            return cached[1]

        available: set[str] = set()
        try:
            if provider == "openai" and self.openai_client:
                listing = self.openai_client.models.list()
                for m in getattr(listing, "data", []):
                    model_id = getattr(m, "id", None)
                    if model_id:
                        available.add(model_id)
            elif provider == "gemini" and self.gemini_client:
                for m in self.gemini_client.models.list():
                    # Gemini SDK returns e.g. "models/gemini-2.5-flash"
                    name = getattr(m, "name", None)
                    if name:
                        available.add(str(name).split("/")[-1])
        except Exception as e:
            logger.warning(f"Failed to list available {provider} models: {e}")

        self._model_inventory_cache[provider] = (now, available)
        return available

    def _ordered_models(self, provider: str, requested_model: str | None) -> list[str]:
        candidates = self._model_fallback_candidates(provider, requested_model)
        if not candidates:
            return candidates

        if os.getenv("LLM_MODEL_AUTO_SELECT", "true").lower() not in ("1", "true", "yes"):
            return candidates

        available = self._get_available_models(provider)
        if not available:
            return candidates

        available_in_candidates = [m for m in candidates if m in available]
        if available_in_candidates:
            missing = [m for m in candidates if m not in available]
            if missing:
                logger.warning(f"{provider}: requested/candidate models unavailable, auto-fallback will skip: {missing}")
            return available_in_candidates

        # If none of our candidates are available, try any known available model.
        chosen = sorted(available)
        if chosen:
            logger.warning(f"{provider}: no preferred candidates available; auto-switching to discovered model {chosen[0]}")
            return chosen
        return candidates

    def _call_with_model_fallback(
        self,
        provider: str,
        call_fn,
        requested_model: str | None,
    ) -> str:
        models = self._ordered_models(provider, requested_model)
        if not models:
            raise ValueError(f"No model candidates available for provider {provider}")

        last_exc: Exception | None = None
        for model in models:
            try:
                logger.info(f"{provider}: trying model {model}")
                return call_fn(model)
            except Exception as e:
                last_exc = e
                if self._is_model_unavailable_error(e):
                    logger.warning(f"{provider}: model unavailable ({model}), trying next candidate")
                    continue
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"{provider}: no models were attempted")

    def call_openai(self, messages, model="gpt-4o", temperature=0.2):
        if not self.openai_client:
            raise ValueError("OpenAI client not configured")

        def _do_call(current_model: str) -> str:
            response = self.openai_client.chat.completions.create(
                model=current_model,
                messages=messages,
                temperature=temperature
            )
            return response.choices[0].message.content

        return self._call_with_model_fallback("openai", _do_call, model)

    def call_anthropic(self, prompt, system_prompt="", model="claude-sonnet-4-6", temperature=0.2):
        if not self.anthropic_client:
            raise ValueError("Anthropic client not configured")

        def _do_call(current_model: str) -> str:
            response = self.anthropic_client.messages.create(
                model=current_model,
                system=system_prompt,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=8096,
            )
            return response.content[0].text

        return self._call_with_model_fallback("anthropic", _do_call, model)

    def call_gemini(self, prompt, system_prompt="", model="gemini-2.5-flash", temperature=0.2):
        if not self.gemini_client:
            raise ValueError("Gemini client not configured")

        def _do_call(current_model: str) -> str:
            # New SDK supports separate system instruction
            response = self.gemini_client.models.generate_content(
                model=current_model,
                contents=prompt,
                config={
                    'system_instruction': system_prompt,
                    'temperature': temperature,
                }
            )
            return response.text

        return self._call_with_model_fallback("gemini", _do_call, model)

    def _execute_single(self, prompt, provider, system_prompt, **kwargs):
        """Internal helper for a single LLM attempt"""
        if provider == "openai":
            messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": prompt}]
            return self.call_openai(messages, **kwargs)
        elif provider == "anthropic":
            return self.call_anthropic(prompt, system_prompt=system_prompt, **kwargs)
        elif provider == "gemini":
            return self.call_gemini(prompt, system_prompt=system_prompt, **kwargs)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _default_cli_command(self, cli_name: str, full_prompt: str) -> str:
        escaped = shlex.quote(full_prompt)
        defaults = {
            "codex": f"codex exec {escaped}",
            "claude": f"claude -p {escaped}",
            "copilot": f"copilot chat --prompt {escaped}",
            "gemini": f"gemini -p {escaped}",
        }
        return defaults.get(cli_name, f"{cli_name} {escaped}")

    def _execute_cli_fallback(self, prompt: str, system_prompt: str = "") -> str | None:
        if not getattr(config, "LLM_CLI_FALLBACK_ENABLED", False):
            return None

        combined_prompt = prompt.strip()
        if system_prompt and system_prompt.strip():
            combined_prompt = f"{system_prompt.strip()}\n\nUser:\n{combined_prompt}"

        configured = getattr(config, "LLM_CLI_COMMANDS", {}) or {}
        cli_order = list(getattr(config, "LLM_CLI_ORDER", []) or [])
        for fallback_name in ("codex", "claude", "copilot", "gemini"):
            if fallback_name not in cli_order:
                cli_order.append(fallback_name)
        timeout_seconds = max(10, int(getattr(config, "LLM_CLI_TIMEOUT_SECONDS", 120)))

        for cli_name in cli_order:
            template = configured.get(cli_name)
            if template:
                command = template.format(
                    prompt=shlex.quote(prompt),
                    system_prompt=shlex.quote(system_prompt),
                    full_prompt=shlex.quote(combined_prompt),
                )
            else:
                command = self._default_cli_command(cli_name, combined_prompt)
            try:
                logger.warning(f"Trying CLI LLM fallback via {cli_name}")
                proc = subprocess.run(
                    ["bash", "-lc", command],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                )
                if proc.returncode != 0:
                    stderr = (proc.stderr or "").strip()
                    logger.warning(f"CLI fallback failed ({cli_name}): {stderr or 'non-zero exit'}")
                    continue
                stdout = (proc.stdout or "").strip()
                if stdout:
                    return stdout
                logger.warning(f"CLI fallback produced empty output ({cli_name})")
            except Exception as e:
                logger.warning(f"CLI fallback exception ({cli_name}): {e}")
        return None

    def execute(self, prompt, provider="anthropic", system_prompt="", retries=2, **kwargs):
        """Unified entry point with Retry AND Multi-Provider Fallback logic"""
        configured_disabled = set(getattr(config, "LLM_DISABLED_PROVIDERS", []) or [])

        # Priority order for fallback if the primary fails.
        all_providers = [p for p in ["anthropic", "openai", "gemini"] if p not in configured_disabled]
        if provider in configured_disabled:
            provider = None
        if provider in all_providers:
            all_providers.remove(provider)
            providers_to_try = [provider] + all_providers
        else:
            providers_to_try = all_providers

        last_exception = None

        for current_provider in providers_to_try:
            if self._provider_temporarily_disabled(current_provider):
                logger.info(f"Provider {current_provider} is temporarily disabled, skipping")
                continue
            logger.info(f"Attempting execution with provider: {current_provider}")
            
            for attempt in range(retries):
                try:
                    return self._execute_single(prompt, current_provider, system_prompt, **kwargs)
                except Exception as e:
                    last_exception = e
                    # Specific "Out of Credits" or "Quota" error: skip retries and jump to fallback
                    error_str = str(e).lower()
                    if (
                        "invalid x-api-key" in error_str
                        or "authentication_error" in error_str
                        or "permission_denied" in error_str
                        or "resource_exhausted" in error_str
                        or "credit balance" in error_str
                        or "insufficient_quota" in error_str
                        or "rate limit" in error_str
                    ):
                        self._mark_provider_temporarily_disabled(current_provider)
                        logger.warning(f"Quota/Balance error for {current_provider}, skipping to next provider: {e}")
                        break
                    
                    logger.warning(f"Attempt {attempt + 1} failed for {current_provider}: {e}")
                    time.sleep(1 + attempt + random.random()) # Simple backoff
            
            logger.error(f"Provider {current_provider} exhausted. Trying fallback if available.")

        # If we get here, all providers failed
        logger.critical(f"ALL LLM providers failed. Last error: {last_exception}")
        cli_result = self._execute_cli_fallback(prompt=prompt, system_prompt=system_prompt)
        if cli_result:
            return cli_result
        if last_exception is not None:
            raise last_exception
        raise RuntimeError("All LLM providers are unavailable or temporarily disabled. Check API keys and quota.")

llm_gateway = LLMGateway()
