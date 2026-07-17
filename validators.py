"""
validators.py
-------------
Lightweight request-payload validation for every REST endpoint.

Each ``validate_*`` function returns the cleaned / normalised dict on
success and raises ``ValidationError`` on failure.
"""

import re
from typing import Any

from firebase_config import PRIORITY_CODES


class ValidationError(Exception):
    """Raised when input validation fails."""

    def __init__(self, errors: dict):
        """
        Parameters
        ----------
        errors : dict
            Mapping of ``field_name → error_message``.
        """
        self.errors = errors
        self.message = "; ".join(f"{k}: {v}" for k, v in errors.items())
        super().__init__(self.message)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


# ---------------------------------------------------------------------------
# Ticket intake  –  POST /api/tickets/create
# ---------------------------------------------------------------------------

def validate_ticket_create(data: dict) -> dict:
    """
    Validate and sanitise the payload for ticket creation.

    Expected keys:
        customer_email, subject, description, department_id, priority

    Returns cleaned dict on success.
    """
    errors: dict[str, str] = {}

    # --- customer_email ----------------------------------------------------
    email = (data.get("customer_email") or "").strip().lower()
    if not email:
        errors["customer_email"] = "customer_email is required"
    elif not _is_valid_email(email):
        errors["customer_email"] = "customer_email is not a valid email address"

    # --- subject -----------------------------------------------------------
    subject = (data.get("subject") or "").strip()
    if not subject:
        errors["subject"] = "subject is required"
    elif len(subject) > 300:
        errors["subject"] = "subject must be 300 characters or fewer"

    # --- description -------------------------------------------------------
    description = (data.get("description") or "").strip()
    if not description:
        errors["description"] = "description is required"
    elif len(description) > 10_000:
        errors["description"] = "description must be 10 000 characters or fewer"

    # --- department_id -----------------------------------------------------
    department_id = (data.get("department_id") or "").strip()
    if not department_id:
        errors["department_id"] = "department_id is required"

    # --- priority ----------------------------------------------------------
    priority = (data.get("priority") or "").strip().upper()
    if not priority:
        errors["priority"] = "priority is required"
    elif priority not in PRIORITY_CODES:
        errors["priority"] = (
            f"priority must be one of {PRIORITY_CODES}"
        )

    if errors:
        raise ValidationError(errors)

    return {
        "customer_email": email,
        "subject": subject,
        "description": description,
        "department_id": department_id,
        "priority": priority,
    }


# ---------------------------------------------------------------------------
# Agent creation
# ---------------------------------------------------------------------------

def validate_agent_create(data: dict) -> dict:
    """Validate the payload for ``POST /api/agents``."""
    errors: dict[str, str] = {}

    name = (data.get("name") or "").strip()
    if not name:
        errors["name"] = "name is required"

    email = (data.get("email") or "").strip().lower()
    if not email:
        errors["email"] = "email is required"
    elif not _is_valid_email(email):
        errors["email"] = "email is not a valid email address"

    department_id = (data.get("department_id") or "").strip()
    if not department_id:
        errors["department_id"] = "department_id is required"

    if errors:
        raise ValidationError(errors)

    return {
        "name": name,
        "email": email,
        "department_id": department_id,
    }
