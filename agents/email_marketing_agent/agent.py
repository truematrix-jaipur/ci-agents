import sys
import os
import logging
import json
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Append project root
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
from core.base_agent import BaseAgent
from config.settings import config

logger = logging.getLogger(__name__)

class EmailMarketingAgent(BaseAgent):
    AGENT_ROLE = "email_marketing_agent"
    SYSTEM_PROMPT = """You are an expert Email Marketing Strategist.
    You manage campaigns, lists, and segments in the email marketing tool.
    
    You do not assume list growth. You always verify the latest subscriber 
    counts and bounce rates."""

    def handle_task(self, task_data):
        logger.info(f"Email Marketing Agent {self.agent_id} handling task: {task_data}")
        task_type = task_data.get("task", {}).get("type")

        if task_type == "send_newsletter":
            return self._send_newsletter(task_data)
        else:
            return super().handle_task(task_data)

    def _send_newsletter(self, task_data):
        task = task_data.get("task", {})
        subject = task.get("subject", "Newsletter")
        body = task.get("body", "")
        recipients = task.get("recipients", [])
        if not recipients:
            return {"status": "error", "message": "recipients list is required"}
        if not (config.SMTP_HOST and config.SMTP_USER and config.SMTP_PASS):
            return {"status": "error", "message": "SMTP is not configured"}

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = config.SMTP_USER
            msg["To"] = ", ".join(recipients)
            msg.attach(MIMEText(body, "html" if "<" in body and ">" in body else "plain"))

            with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=20) as server:
                server.starttls()
                server.login(config.SMTP_USER, config.SMTP_PASS)
                server.sendmail(config.SMTP_USER, recipients, msg.as_string())

            self.log_execution(
                task=task_data,
                thought_process="Validated SMTP config and prepared newsletter payload.",
                action_taken=f"Newsletter sent to {len(recipients)} recipients.",
            )
            return {"status": "success", "message": "Newsletter broadcast sent.", "sent_count": len(recipients)}
        except Exception as e:
            logger.error(f"Newsletter send failed: {e}")
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    agent = EmailMarketingAgent()
    agent.run()
