from __future__ import annotations

import base64
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx

logger = logging.getLogger("unicart.email")
FROM_NAME = "UniCart"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _oauth_values() -> tuple[str, str, str]:
    return (
        os.getenv("GMAIL_API_CLIENT_ID", "").strip(),
        os.getenv("GMAIL_API_CLIENT_SECRET", "").strip(),
        os.getenv("GMAIL_API_REFRESH_TOKEN", "").strip(),
    )


def _send_via_gmail_api(to_email: str, subject: str, html_body: str) -> bool:
    gmail_user = os.getenv("GMAIL_USER", "").strip()
    client_id, client_secret, refresh_token = _oauth_values()

    if not gmail_user or not all((client_id, client_secret, refresh_token)):
        logger.error("[UniCart Email] Gmail API transport is not fully configured.")
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{FROM_NAME} <{gmail_user}>"
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with httpx.Client(timeout=8.0) as client:
            token_response = client.post(
                TOKEN_URL,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
            token_response.raise_for_status()

            access_token = token_response.json().get("access_token")
            if not access_token:
                logger.error("[UniCart Email] Gmail OAuth token response contained no access token.")
                return False

            raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
            send_response = client.post(
                SEND_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": raw_message},
            )
            send_response.raise_for_status()

        logger.info(
            "[UniCart Email] ✅ Sent via Gmail API → %s | %s",
            to_email,
            subject,
        )
        return True
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[UniCart Email] ❌ Gmail API HTTP %s while sending to %s",
            exc.response.status_code,
            to_email,
        )
        return False
    except (httpx.HTTPError, ValueError) as exc:
        logger.error(
            "[UniCart Email] ❌ Gmail API request failed for %s: %s",
            to_email,
            exc,
        )
        return False


def install_gmail_api_transport() -> bool:
    values = _oauth_values()

    if not any(values):
        return False

    if not all(values):
        logger.error(
            "[UniCart Email] Gmail API OAuth configuration is incomplete. "
            "Set GMAIL_API_CLIENT_ID, GMAIL_API_CLIENT_SECRET, and "
            "GMAIL_API_REFRESH_TOKEN together."
        )
        return False

    from app import email_service

    email_service._send = _send_via_gmail_api
    logger.info("[UniCart Email] Gmail API HTTPS transport enabled.")
    return True
