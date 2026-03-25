"""
CI SEO Agent — Validator & Guardrails
Production-safe validation for every action before and after execution.
Anti-hallucination checks, URL verification, rollback tracking.
"""
import json
import logging
import re
import subprocess
from urllib.parse import urlparse

import requests

from config import cfg

logger = logging.getLogger("ci.validator")

ALLOWED_DOMAIN = "indogenmed.org"
MAX_META_DESC_LEN = 160
MIN_META_DESC_LEN = 50
MAX_TITLE_LEN = 60
MIN_TITLE_LEN = 20

# Medical misinformation red flags — never write these claims
PROHIBITED_CLAIMS = [
    "cure", "cures", "curative", "guaranteed to", "100% effective",
    "no side effects", "completely safe", "fda approved" , "clinically proven to cure",
    "miracle", "instant relief guaranteed",
]

# Action types allowed in production without extra approval
AUTO_APPROVE_TYPES = {"FLAG_FOR_REVIEW", "CREATE_CONTENT_BRIEF"}

# Action types that ALWAYS require human approval
REQUIRE_APPROVAL_TYPES = {
    "UPDATE_PAGE_TITLE",
    "UPDATE_META_DESCRIPTION",
    "FIX_CANONICAL",
    "UPDATE_SCHEMA",
    "OPTIMIZE_HEADING",
    "ADD_INTERNAL_LINK",
}


class GuardrailViolation(Exception):
    """Raised when an action fails a guardrail check."""
    pass


class Validator:

    # ── Pre-execution Guardrails ───────────────────────────────────────────

    def validate_action(self, action: dict) -> tuple[bool, str]:
        """
        Run all guardrails on an action before execution.
        Returns (is_safe, reason).
        """
        meta = action.get("metadata", action)
        action_type = meta.get("action_type", "")
        target_url = meta.get("target_url", "")
        impl_data = meta.get("implementation_data", {})
        if isinstance(impl_data, str):
            try:
                impl_data = json.loads(impl_data)
            except Exception:
                impl_data = {}

        checks = [
            self._check_target_url(target_url),
            self._check_new_value_length(action_type, impl_data),
            self._check_prohibited_content(impl_data),
            self._check_action_type_valid(action_type),
        ]

        for ok, reason in checks:
            if not ok:
                logger.warning(f"Guardrail FAILED [{action_type}]: {reason}")
                return False, reason

        return True, "All guardrails passed"

    def _check_target_url(self, url: str) -> tuple[bool, str]:
        """URL must be on the allowed domain or empty (for keyword-only actions)."""
        if not url:
            return True, "No URL check needed"
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.replace("www.", "")
            if ALLOWED_DOMAIN not in domain:
                return False, f"URL domain '{domain}' not in allowed domain '{ALLOWED_DOMAIN}'"
        except Exception as e:
            return False, f"Invalid URL: {e}"
        return True, "URL valid"

    def _check_new_value_length(
        self, action_type: str, impl_data: dict
    ) -> tuple[bool, str]:
        """Meta descriptions and titles must be within SEO-optimal length ranges."""
        new_val = impl_data.get("new_value", "")
        if not new_val:
            return True, "No new value to check"

        if action_type == "UPDATE_META_DESCRIPTION":
            if len(new_val) > MAX_META_DESC_LEN:
                return (
                    False,
                    f"Meta description too long: {len(new_val)} chars (max {MAX_META_DESC_LEN})",
                )
            if len(new_val) < MIN_META_DESC_LEN:
                return (
                    False,
                    f"Meta description too short: {len(new_val)} chars (min {MIN_META_DESC_LEN})",
                )

        elif action_type == "UPDATE_PAGE_TITLE":
            if len(new_val) > MAX_TITLE_LEN:
                return (
                    False,
                    f"Title too long: {len(new_val)} chars (max {MAX_TITLE_LEN})",
                )
            if len(new_val) < MIN_TITLE_LEN:
                return (
                    False,
                    f"Title too short: {len(new_val)} chars (min {MIN_TITLE_LEN})",
                )

        return True, "Length OK"

    def _check_prohibited_content(self, impl_data: dict) -> tuple[bool, str]:
        """Check for prohibited medical claims in generated content."""
        new_val = (impl_data.get("new_value", "") or "").lower()
        notes = (impl_data.get("notes", "") or "").lower()
        combined = new_val + " " + notes

        for claim in PROHIBITED_CLAIMS:
            if claim in combined:
                return (
                    False,
                    f"Prohibited medical claim detected: '{claim}'. "
                    "Remove this claim before implementing.",
                )
        return True, "Content guardrails passed"

    def _check_action_type_valid(self, action_type: str) -> tuple[bool, str]:
        """Action type must be in the known list."""
        all_known = REQUIRE_APPROVAL_TYPES | AUTO_APPROVE_TYPES
        if action_type not in all_known:
            return False, f"Unknown action type: '{action_type}'"
        return True, "Action type valid"

    # ── URL Accessibility Check ────────────────────────────────────────────

    def verify_url_accessible(self, url: str) -> tuple[bool, int]:
        """
        Verify a URL returns HTTP 200.
        Returns (is_accessible, status_code).
        """
        try:
            resp = requests.head(url, timeout=10, allow_redirects=True,
                                 headers={"User-Agent": "CI-SEO-Agent/1.0"})
            return resp.status_code == 200, resp.status_code
        except Exception as e:
            logger.warning(f"URL accessibility check failed for {url}: {e}")
            return False, 0

    # ── Post-execution Verification ────────────────────────────────────────

    def verify_meta_description(self, url: str, expected_desc: str) -> tuple[bool, str]:
        """
        Fetch the page and verify the meta description was actually updated.
        Checks rendered HTML meta tag.
        """
        try:
            resp = requests.get(
                url, timeout=15,
                headers={"User-Agent": "Googlebot/2.1"},
            )
            if not resp.ok:
                return False, f"Page returned {resp.status_code}"

            html = resp.text
            # Match <meta name="description" content="...">
            match = re.search(
                r'<meta\s+name=["\']description["\']\s+content=["\'](.*?)["\']',
                html,
                re.IGNORECASE | re.DOTALL,
            )
            if not match:
                return False, "No meta description tag found in rendered HTML"

            actual = match.group(1).strip()
            # Full-string comparison with whitespace normalization
            expected_norm = re.sub(r'\s+', ' ', expected_desc.strip().lower())
            actual_norm = re.sub(r'\s+', ' ', actual.strip().lower())
            if expected_norm == actual_norm or expected_norm in actual_norm:
                return True, f"Meta description verified: {actual[:80]}"
            return (
                False,
                f"Meta description mismatch.\n"
                f"Expected: {expected_desc[:80]}\n"
                f"Actual:   {actual[:80]}",
            )
        except Exception as e:
            return False, f"Verification error: {e}"

    def verify_page_title(self, url: str, expected_title: str) -> tuple[bool, str]:
        """Verify the page <title> tag was updated."""
        try:
            resp = requests.get(url, timeout=15, headers={"User-Agent": "Googlebot/2.1"})
            match = re.search(r"<title>(.*?)</title>", resp.text, re.IGNORECASE | re.DOTALL)
            if not match:
                return False, "No <title> tag found"
            actual = match.group(1).strip()
            expected_norm = re.sub(r'\s+', ' ', expected_title.strip().lower())
            actual_norm = re.sub(r'\s+', ' ', actual.strip().lower())
            if expected_norm == actual_norm or expected_norm in actual_norm:
                return True, f"Title verified: {actual[:60]}"
            return False, f"Title mismatch.\nExpected: {expected_title}\nActual:   {actual}"
        except Exception as e:
            return False, f"Verification error: {e}"

    # ── Backup / Rollback ──────────────────────────────────────────────────

    def backup_post_meta(self, post_id: int) -> dict:
        """Snapshot current Rank Math meta for rollback."""
        import subprocess
        cmd = [
            cfg.WP_CLI_PATH, "--allow-root", f"--path={cfg.WP_ROOT}",
            "eval",
            f"echo json_encode(["
            f"'rank_math_title' => get_post_meta({post_id}, 'rank_math_title', true),"
            f"'rank_math_description' => get_post_meta({post_id}, 'rank_math_description', true),"
            f"'rank_math_focus_keyword' => get_post_meta({post_id}, 'rank_math_focus_keyword', true),"
            f"'rank_math_canonical_url' => get_post_meta({post_id}, 'rank_math_canonical_url', true)"
            f"]);",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if result.returncode == 0:
            try:
                return json.loads(result.stdout.strip())
            except Exception:
                pass
        return {}

    def rollback_post_meta(self, post_id: int, backup: dict) -> bool:
        """Restore Rank Math meta from backup."""
        php_parts = []
        for key, val in backup.items():
            esc_val = val.replace("'", "\\'")
            php_parts.append(
                f"update_post_meta({post_id}, '{key}', '{esc_val}');"
            )
        if not php_parts:
            return True
        php_code = " ".join(php_parts)
        cmd = [
            cfg.WP_CLI_PATH, "--allow-root", f"--path={cfg.WP_ROOT}",
            "eval", php_code,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return result.returncode == 0


validator = Validator()
