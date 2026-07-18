"""
email_service.py
----------------
Gmail API integration for the Email-to-Ticket feature.

Polls the configured Gmail inbox for new unread messages and converts
them into helpdesk tickets.  Duplicate prevention is handled via the
``gmail_message_id`` field stored on each ticket.

Setup
-----
1. Create OAuth 2.0 credentials in the Google Cloud Console
   (APIs & Services > Credentials > Create OAuth client ID > Desktop app).
2. Download the client-secrets JSON and point ``GMAIL_CREDENTIALS_FILE`` to it.
3. On first run the app opens a browser for Google consent; the resulting
   refresh token is cached in ``GMAIL_TOKEN_FILE``.

Environment variables
---------------------
  GMAIL_CREDENTIALS_FILE  – path to OAuth client secrets JSON
  GMAIL_TOKEN_FILE        – path to cache the OAuth token (default: gmail_token.json)
  GMAIL_ADMIN_USER        – Gmail address to monitor (default: snehathangaraj5@gmail.com)
  GMAIL_POLL_INTERVAL     – seconds between polls (default: 300)
"""

import base64
import email as email_lib
import logging
import os
from email.mime.text import MIMEText
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Default department for email-originated tickets
_DEFAULT_DEPARTMENT = "IT"
_DEFAULT_PRIORITY = "P3"


# ---------------------------------------------------------------------------
# Gmail API helpers
# ---------------------------------------------------------------------------

def _build_gmail_service():
    """Build and return a Gmail API service object.

    Supports two auth methods:
      - OAuth2 user consent (GMAIL_CREDENTIALS_FILE + GMAIL_TOKEN_FILE)
      - Service account (GMAIL_SERVICE_ACCOUNT_KEY)

    Returns ``None`` when credentials are missing.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning(
            "Gmail API libraries not installed. "
            "Install with:  pip install google-api-python-client "
            "google-auth-oauthlib google-auth-httplib2"
        )
        return None

    creds = None
    token_path = os.environ.get("GMAIL_TOKEN_FILE", "gmail_token.json")
    creds_file = os.environ.get("GMAIL_CREDENTIALS_FILE", "gmail_credentials.json")
    sa_key = os.environ.get("GMAIL_SERVICE_ACCOUNT_KEY", "")

    # Method 1: OAuth2 token cache
    if os.path.isfile(token_path):
        try:
            creds = Credentials.from_authorized_user_file(token_path, _SCOPES)
        except Exception:
            logger.exception("Failed to load Gmail token from %s", token_path)
            creds = None

    # Method 2: Refresh or do initial consent
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(token_path, "w") as f:
                f.write(creds.to_json())
            logger.info("Gmail OAuth token refreshed")
        except Exception:
            logger.exception("Failed to refresh Gmail token")
            creds = None

    if not creds or not creds.valid:
        if os.path.isfile(creds_file):
            try:
                flow = InstalledAppFlow.from_client_secrets_file(creds_file, _SCOPES)
                creds = flow.run_local_server(port=0)
                with open(token_path, "w") as f:
                    f.write(creds.to_json())
                logger.info("Gmail OAuth consent completed – token saved to %s", token_path)
            except Exception:
                logger.exception("Gmail OAuth consent flow failed")
                return None
        elif sa_key and os.path.isfile(sa_key):
            try:
                from google.oauth2 import service_account
                creds = service_account.Credentials.from_service_account_file(
                    sa_key, scopes=_SCOPES
                )
                logger.info("Gmail service account credentials loaded")
            except Exception:
                logger.exception("Failed to load Gmail service account key")
                return None
        else:
            logger.info(
                "Gmail credentials not found (checked %s and %s). "
                "Email-to-ticket feature is disabled.",
                token_path, creds_file,
            )
            return None

    admin_user = os.environ.get("GMAIL_ADMIN_USER", "snehathangaraj5@gmail.com")
    return build("gmail", "v1", credentials=creds, static_discovery=False)


def _decode_body(payload: dict) -> str:
    """Recursively extract the plain-text body from a Gmail message payload."""
    body = ""
    mime_type = payload.get("mimeType", "")
    parts = payload.get("parts", [])

    if mime_type == "text/plain" and payload.get("body", {}).get("data"):
        body = base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")
    elif parts:
        for part in parts:
            part_body = _decode_body(part)
            if part_body:
                body = part_body
                break
    return body


def _extract_attachments(payload: dict) -> list[dict]:
    """Extract attachment metadata from a Gmail message payload."""
    attachments = []
    parts = payload.get("parts", [])

    for part in parts:
        filename = part.get("filename", "")
        if filename and part.get("body", {}).get("attachmentId"):
            attachments.append({
                "filename": filename,
                "mimeType": part.get("mimeType", "application/octet-stream"),
                "size": part.get("body", {}).get("size", 0),
                "attachmentId": part["body"]["attachmentId"],
            })
        # Recurse into nested parts
        attachments.extend(_extract_attachments(part))

    return attachments


def _parse_email_date(date_str: str) -> Optional[datetime]:
    """Parse an RFC 2822 date string into a UTC datetime."""
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _extract_sender_email(from_header: str) -> str:
    """Extract just the email address from a From header."""
    from email.utils import parseaddr
    _, addr = parseaddr(from_header)
    return addr.lower() if addr else from_header.lower()


# ---------------------------------------------------------------------------
# Ticket creation from email
# ---------------------------------------------------------------------------

def _create_ticket_from_email(message: dict, admin_user: str):
    """Convert a single Gmail message into a helpdesk ticket.

    Returns the created ticket dict, or None on failure.
    """
    from firebase_config import get_db, TICKETS_COLLECTION
    from models import utcnow, TicketModel
    from services import TicketService

    headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}

    gmail_msg_id = message.get("id", "")
    sender = _extract_sender_email(headers.get("from", ""))
    subject = headers.get("subject", "(No Subject)")
    date_str = headers.get("date", "")
    body = _decode_body(message.get("payload", {}))
    attachments = _extract_attachments(message.get("payload", {}))
    email_date = _parse_email_date(date_str) or utcnow()

    # Duplicate prevention – check gmail_message_id
    db = get_db()
    existing = (
        db.collection(TICKETS_COLLECTION)
        .where("gmail_message_id", "==", gmail_msg_id)
        .limit(1)
        .stream()
    )
    for _ in existing:
        logger.info("Skipping duplicate email %s (already ticket exists)", gmail_msg_id)
        return None

    # Build attachment summaries for the description
    attachment_info = ""
    if attachments:
        attachment_info = "\n\nAttachments:\n" + "\n".join(
            f"  - {a['filename']} ({a['mimeType']}, {a['size']} bytes)"
            for a in attachments
        )

    ticket_data = {
        "customer_email": sender,
        "customer_name": sender.split("@", 1)[0] if sender else "Email Customer",
        "subject": subject[:300],
        "description": (body.strip() or "(Empty email body)")[:10000] + attachment_info,
        "department_id": _DEFAULT_DEPARTMENT,
        "priority": _DEFAULT_PRIORITY,
        "source": "email",
        "gmail_message_id": gmail_msg_id,
        "email_date": email_date,
        "attachment_count": len(attachments),
    }

    try:
        ticket = TicketService.create_ticket(ticket_data)
        ticket_id = ticket.get("id")

        # Auto-assign via round-robin
        try:
            from round_robin import pick_agent
            agent = pick_agent(_DEFAULT_DEPARTMENT)
            if agent and ticket_id:
                TicketService.update_ticket(ticket_id, {
                    "assigned_to":   agent["id"],
                    "assigned_name": agent.get("name", ""),
                })
                logger.info("Auto-assigned email ticket %s to agent %s", ticket_id, agent.get("name"))
        except Exception:
            logger.exception("Round-robin assignment failed for email ticket %s", ticket_id)

        logger.info("Ticket created from email %s: %s", gmail_msg_id, ticket_id)
        return ticket
    except Exception:
        logger.exception("Failed to create ticket from email %s", gmail_msg_id)
        return None


# ---------------------------------------------------------------------------
# Polling
# ---------------------------------------------------------------------------

def check_new_emails():
    """Poll Gmail for new unread messages and create tickets.

    Returns the number of new tickets created.
    """
    service = _build_gmail_service()
    if service is None:
        return 0

    admin_user = os.environ.get("GMAIL_ADMIN_USER", "snehathangaraj5@gmail.com")
    created_count = 0

    try:
        results = service.users().messages().list(
            userId=admin_user,
            q="is:unread",
            maxResults=20,
        ).execute()

        messages = results.get("messages", [])
        if not messages:
            logger.debug("No new unread emails found")
            return 0

        logger.info("Found %d unread emails – processing", len(messages))

        for msg_ref in messages:
            try:
                msg = service.users().messages().get(
                    userId=admin_user,
                    id=msg_ref["id"],
                    format="full",
                ).execute()

                ticket = _create_ticket_from_email(msg, admin_user)
                if ticket:
                    created_count += 1

                    # Mark as read after successful ticket creation
                    service.users().messages().modify(
                        userId=admin_user,
                        id=msg_ref["id"],
                        body={"removeLabelIds": ["UNREAD"]},
                    ).execute()
            except Exception:
                logger.exception("Failed to process email %s", msg_ref.get("id", "?"))

    except Exception:
        logger.exception("Gmail polling failed")

    if created_count:
        logger.info("Created %d ticket(s) from emails", created_count)

    return created_count


# ---------------------------------------------------------------------------
# Background scheduler
# ---------------------------------------------------------------------------

_email_scheduler = None


def start_email_poller(interval_seconds: int = None):
    """Start the background email polling scheduler.

    Safe to call multiple times – only one scheduler runs.
    """
    global _email_scheduler
    if _email_scheduler is not None:
        logger.info("Email poller already running – skipping")
        return _email_scheduler

    if interval_seconds is None:
        interval_seconds = int(os.environ.get("GMAIL_POLL_INTERVAL", "300"))

    # Check if Gmail credentials are configured
    service = _build_gmail_service()
    if service is None:
        logger.info("Email-to-ticket disabled – no Gmail credentials configured")
        return None

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.error("APScheduler not installed – email poller cannot start")
        return None

    _email_scheduler = BackgroundScheduler()
    _email_scheduler.add_job(
        check_new_emails,
        "interval",
        seconds=interval_seconds,
        id="email_poller",
        replace_existing=True,
        max_instances=1,
    )
    _email_scheduler.start()
    logger.info("Email poller started – checking every %ds", interval_seconds)
    return _email_scheduler


def stop_email_poller():
    """Gracefully shut down the email poller."""
    global _email_scheduler
    if _email_scheduler is not None:
        _email_scheduler.shutdown(wait=False)
        _email_scheduler = None
        logger.info("Email poller stopped")
