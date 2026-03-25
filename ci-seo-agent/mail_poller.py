"""
CI SEO Agent — Email Reply Handler
Polls Gmail API for approval/rejection replies from surya@truematrix.io.

Setup (one-time, Google Workspace admin required):
  1. Go to: admin.google.com → Security → API Controls → Domain-wide Delegation
  2. Add Client ID: 112640322294284542051  (erpnext service account)
  3. Scopes: https://www.googleapis.com/auth/gmail.readonly
  4. Create seo-agent@indogenmed.org mailbox in Google Workspace
  5. Set GMAIL_POLL_EMAIL=seo-agent@indogenmed.org in .env

Until domain delegation is set up, approvals work via:
  - Email link (GET /approve/{id}?secret=...) — already in email
  - API call (POST /approve/{id} with X-API-Secret header)
  - Local pipe (seo-agent@indogenmed.org → postfix → this script)
"""
import email
import logging
import mailbox
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger("ci.mail_poller")

APPROVAL_KEYWORDS = {"approve", "approved", "yes", "confirm", "go ahead", "proceed", "ok", "okay"}
REJECTION_KEYWORDS = {"reject", "rejected", "no", "cancel", "stop", "deny", "denied", "hold"}
SENDER_WHITELIST = {"surya@truematrix.io", "suryaprakash@truematrix.io"}


class MailPoller:
    """
    Multi-strategy email reply poller.
    Strategy 1: Gmail API via service account with domain-wide delegation
    Strategy 2: Local UNIX mailbox (/var/mail/seo-agent)
    Strategy 3: Postfix pipe stdin (called from mail alias)
    """

    def __init__(self):
        from config import cfg
        self.cfg = cfg
        self.gmail_service = None
        self.poll_email = os.getenv("GMAIL_POLL_EMAIL", "seo-agent@indogenmed.org")
        self.gmail_mode = os.getenv("GMAIL_API_ENABLED", "auto").strip().lower()
        self._gmail_init_failed = False
        self._gmail_skip_logged = False
        self._processed_ids: set = set()  # Track processed message IDs

    def _local_delivery_available(self) -> bool:
        """Prefer local mailbox/pipe delivery when it is configured on this host."""
        if Path("/var/mail/seo-agent").exists():
            return True

        for alias_path in ("/etc/aliases", "/etc/postfix/aliases"):
            try:
                if not os.path.exists(alias_path):
                    continue
                with open(alias_path, "r", encoding="utf-8", errors="ignore") as fh:
                    if "seo-agent:" in fh.read():
                        return True
            except Exception:
                continue

        return False

    def _gmail_poll_enabled(self) -> bool:
        if self.gmail_mode in {"1", "true", "yes", "on", "force"}:
            return True
        if self.gmail_mode in {"0", "false", "no", "off", "disabled"}:
            return False
        return not self._local_delivery_available()

    # ── Strategy 1: Gmail API ─────────────────────────────────────────────

    def _init_gmail(self) -> bool:
        """
        Initialize Gmail API service using service account with domain-wide delegation.
        Returns True if successful.
        """
        if self._gmail_init_failed:
            return False

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            from pathlib import Path

            sa_file = Path(self.cfg.GSC_SERVICE_ACCOUNT_FILE)
            if not sa_file.exists():
                logger.warning("Gmail: service account file not found")
                return False

            GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
            creds = service_account.Credentials.from_service_account_file(
                str(sa_file), scopes=GMAIL_SCOPES
            )
            # Delegate to the seo-agent mailbox
            delegated = creds.with_subject(self.poll_email)
            self.gmail_service = build("gmail", "v1", credentials=delegated)
            # Quick test
            self.gmail_service.users().getProfile(userId="me").execute()
            logger.info(f"Gmail API connected for {self.poll_email}")
            return True

        except Exception as e:
            self._gmail_init_failed = True
            logger.warning(f"Gmail API init failed (domain delegation not configured?): {e}")
            return False

    def poll_gmail(self, since_hours: int = 24) -> list[dict]:
        """
        Poll Gmail inbox for messages from approved senders.
        Returns list of parsed replies.
        """
        if not self.gmail_service:
            if not self._init_gmail():
                return []

        try:
            after_ts = int((datetime.utcnow() - timedelta(hours=since_hours)).timestamp())
            query = (
                f"from:({' OR '.join(SENDER_WHITELIST)}) "
                f"subject:(CI SEO Agent OR APPROVE OR REJECT) "
                f"after:{after_ts}"
            )

            results = (
                self.gmail_service.users()
                .messages()
                .list(userId="me", q=query, maxResults=20)
                .execute()
            )

            messages = results.get("messages", [])
            parsed = []

            for msg_stub in messages:
                msg_id = msg_stub["id"]
                if msg_id in self._processed_ids:
                    continue

                msg = (
                    self.gmail_service.users()
                    .messages()
                    .get(userId="me", id=msg_id, format="full")
                    .execute()
                )

                parsed_msg = self._parse_gmail_message(msg)
                if parsed_msg:
                    parsed.append(parsed_msg)
                    self._processed_ids.add(msg_id)

            return parsed

        except Exception as e:
            logger.error(f"Gmail poll error: {e}")
            return []

    def _parse_gmail_message(self, msg: dict) -> Optional[dict]:
        """Parse a Gmail API message object into a structured dict."""
        headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
        from_addr = headers.get("from", "").lower()
        subject = headers.get("subject", "")

        # Whitelist check
        if not any(s in from_addr for s in SENDER_WHITELIST):
            return None

        # Get body
        body = self._extract_gmail_body(msg.get("payload", {}))
        if not body:
            return None

        decision, report_id = self._parse_decision(subject + " " + body)
        if not decision:
            return None

        return {
            "source": "gmail",
            "from": from_addr,
            "subject": subject,
            "body_preview": body[:200],
            "decision": decision,
            "report_id": report_id,
            "timestamp": datetime.utcnow().isoformat(),
        }

    def _extract_gmail_body(self, payload: dict, depth: int = 0) -> str:
        """Recursively extract text body from Gmail payload."""
        if depth > 5:
            return ""
        mime_type = payload.get("mimeType", "")
        if mime_type in ("text/plain", "text/html"):
            data = payload.get("body", {}).get("data", "")
            if data:
                import base64
                try:
                    return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
                except Exception:
                    return ""
        # Recurse into parts
        for part in payload.get("parts", []):
            result = self._extract_gmail_body(part, depth + 1)
            if result:
                return result
        return ""

    # ── Strategy 2: Local UNIX Mailbox ───────────────────────────────────

    def poll_local_mailbox(self) -> list[dict]:
        """
        Read /var/mail/seo-agent (mbox format) for approval replies.
        Returns parsed replies and clears the mailbox.
        """
        mbox_path = "/var/mail/seo-agent"
        if not os.path.exists(mbox_path):
            return []

        parsed = []
        try:
            mb = mailbox.mbox(mbox_path)
            mb.lock()

            for key, msg in mb.items():
                from_addr = (msg.get("From") or msg.get("from") or "").lower()
                subject = msg.get("Subject") or msg.get("subject") or ""

                # Whitelist check
                if not any(s in from_addr for s in SENDER_WHITELIST):
                    continue

                # Get body
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                            break
                else:
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode("utf-8", errors="replace")

                decision, report_id = self._parse_decision(subject + " " + body)
                if decision:
                    parsed.append({
                        "source": "local_mailbox",
                        "from": from_addr,
                        "subject": subject,
                        "body_preview": body[:200],
                        "decision": decision,
                        "report_id": report_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    })

            if parsed:
                # Clear processed messages
                mb.clear()
                mb.flush()

            mb.unlock()
            mb.close()

        except Exception as e:
            logger.error(f"Local mailbox poll error: {e}")

        return parsed

    # ── Strategy 3: Stdin Pipe (called by postfix alias) ──────────────────

    @staticmethod
    def process_piped_email(raw_email: str) -> Optional[dict]:
        """
        Parse a raw email piped from postfix.
        Called as: /home/agents/ci-seo-agent/mail_pipe.py (reads stdin).
        """
        try:
            msg = email.message_from_string(raw_email)
            from_addr = (msg.get("From") or "").lower()
            subject = msg.get("Subject") or ""

            if not any(s in from_addr for s in SENDER_WHITELIST):
                logger.warning(f"Ignoring email from non-whitelisted sender: {from_addr}")
                return None

            body = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                        break
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    body = payload.decode("utf-8", errors="replace")

            decision, report_id = MailPoller._parse_decision_static(subject + " " + body)
            if not decision:
                return None

            return {
                "source": "postfix_pipe",
                "from": from_addr,
                "subject": subject,
                "body_preview": body[:200],
                "decision": decision,
                "report_id": report_id,
            }
        except Exception as e:
            logger.error(f"Pipe parse error: {e}")
            return None

    # ── Decision Parsing ──────────────────────────────────────────────────

    def _parse_decision(self, text: str) -> tuple[Optional[str], Optional[str]]:
        return MailPoller._parse_decision_static(text)

    @staticmethod
    def _parse_decision_static(text: str) -> tuple[Optional[str], Optional[str]]:
        """
        Parse APPROVE/REJECT decision and optional report_id from email text.
        Returns (decision, report_id) or (None, None).
        """
        text_lower = text.lower()

        # Extract report_id if present
        report_id = None
        id_match = re.search(r"report[_\s]?(\w{8,})", text, re.IGNORECASE)
        if id_match:
            report_id = f"report_{id_match.group(1)}" if not id_match.group(0).startswith("report_") else id_match.group(0)

        # Determine decision
        for kw in APPROVAL_KEYWORDS:
            if kw in text_lower:
                return "approve", report_id

        for kw in REJECTION_KEYWORDS:
            if kw in text_lower:
                return "reject", report_id

        return None, None

    # ── Main Poll Cycle ───────────────────────────────────────────────────

    def poll_all(self) -> list[dict]:
        """Run all polling strategies and return combined results."""
        results = []
        results.extend(self.poll_local_mailbox())

        if self._gmail_poll_enabled():
            results.extend(self.poll_gmail())
        elif not self._gmail_skip_logged:
            logger.info(
                "Skipping Gmail API polling because local mailbox/pipe delivery is configured. "
                "Set GMAIL_API_ENABLED=true to force delegated Gmail polling."
            )
            self._gmail_skip_logged = True

        return results


# ── Singleton ─────────────────────────────────────────────────────────────

mail_poller = MailPoller()


# ── Postfix pipe entry point ──────────────────────────────────────────────

def main_pipe():
    """Entry point when called as postfix alias pipe: reads stdin."""
    import requests
    from config import cfg

    logging.basicConfig(level=logging.INFO)
    raw = sys.stdin.read()
    logger.info(f"Received piped email ({len(raw)} bytes)")

    result = MailPoller.process_piped_email(raw)
    if not result:
        logger.info("No actionable decision found in piped email")
        sys.exit(0)

    decision = result["decision"]
    report_id = result["report_id"]
    from_addr = result["from"]

    logger.info(f"Decision: {decision} | Report: {report_id} | From: {from_addr}")

    if not report_id:
        # Use the latest pending report
        try:
            status = requests.get(f"http://localhost:{cfg.API_PORT}/status", timeout=5).json()
            report_id = status["state"].get("pending_approval_report_id", "latest")
        except Exception:
            report_id = "latest"

    try:
        if decision == "approve":
            resp = requests.get(
                f"http://localhost:{cfg.API_PORT}/approve/{report_id}",
                params={"secret": cfg.API_SECRET},
                timeout=10,
            )
            logger.info(f"Approve API response: {resp.status_code}")
        else:
            resp = requests.get(
                f"http://localhost:{cfg.API_PORT}/reject/{report_id}",
                params={"secret": cfg.API_SECRET},
                timeout=10,
            )
            logger.info(f"Reject API response: {resp.status_code}")
    except Exception as e:
        logger.error(f"API call failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main_pipe()
