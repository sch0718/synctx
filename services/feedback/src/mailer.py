import smtplib
from email.message import EmailMessage
import os
import logging
from synctx_common.config import OutboundMailConfig

logger = logging.getLogger(__name__)

def send_feedback_email(config: OutboundMailConfig, subject: str, body: str):
    msg = EmailMessage()
    msg.set_content(body)
    msg['Subject'] = subject
    msg['From'] = config.from_
    msg['To'] = config.to

    pwd = os.getenv(config.credential_env)
    
    try:
        with smtplib.SMTP(config.smtp_host, config.smtp_port) as s:
            # s.starttls() # Enable in real environment
            if pwd:
                s.login(config.from_, pwd)
            s.send_message(msg)
        logger.info(f"Successfully sent feedback email to {config.to}")
    except Exception as e:
        logger.error(f"Failed to send email: {e}")
        raise
