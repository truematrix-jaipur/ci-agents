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

    def call_openai(self, messages, model="gpt-4o", temperature=0.2):
        if not self.openai_client:
            raise ValueError("OpenAI client not configured")
        
        response = self.openai_client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature
        )
        return response.choices[0].message.content

    def call_anthropic(self, prompt, system_prompt="", model="claude-sonnet-4-6", temperature=0.2):
        if not self.anthropic_client:
            raise ValueError("Anthropic client not configured")

        response = self.anthropic_client.messages.create(
            model=model,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=8096,
        )
        return response.content[0].text

    def call_gemini(self, prompt, system_prompt="", model="gemini-2.0-flash", temperature=0.2):
        if not self.gemini_client:
            raise ValueError("Gemini client not configured")
        
        # New SDK supports separate system instruction
        response = self.gemini_client.models.generate_content(
            model=model,
            contents=prompt,
            config={
                'system_instruction': system_prompt,
                'temperature': temperature,
            }
        )
        return response.text

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
        cli_order = getattr(config, "LLM_CLI_ORDER", []) or []
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
        
        # Priority order for fallback if the primary fails
        all_providers = ["anthropic", "openai", "gemini"]
        # Move primary to front
        if provider in all_providers:
            all_providers.remove(provider)
        providers_to_try = [provider] + all_providers

        last_exception = None

        for current_provider in providers_to_try:
            logger.info(f"Attempting execution with provider: {current_provider}")
            
            for attempt in range(retries):
                try:
                    return self._execute_single(prompt, current_provider, system_prompt, **kwargs)
                except Exception as e:
                    last_exception = e
                    # Specific "Out of Credits" or "Quota" error: skip retries and jump to fallback
                    error_str = str(e).lower()
                    if "credit balance" in error_str or "insufficient_quota" in error_str or "rate limit" in error_str:
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
        raise last_exception

llm_gateway = LLMGateway()
