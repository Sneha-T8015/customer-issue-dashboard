"""
models.py
---------
Thin data-class helpers that normalise raw dictionaries into
the shapes the rest of the application expects.

Every ``from_*`` factory validates required keys are present and
returns a plain dict ready for Firestore writes.  Every ``to_dict``
converter serialises Firestore timestamp fields to ISO-8601 strings
so the JSON API is always safe to consume.
"""

from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    """Convenience wrapper around ``datetime.now(timezone.utc)``."""
    return datetime.now(timezone.utc)


def serialise(obj: Any) -> Any:
    """Recursively convert datetimes to ISO strings for JSON safety."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: serialise(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [serialise(i) for i in obj]
    return obj


def doc_to_dict(doc) -> dict | None:
    """Convert a Firestore DocumentSnapshot to a dict with ``id`` injected."""
    if doc is None or not doc.exists:
        return None
    data = doc.to_dict()
    data["id"] = doc.id
    return data


# ---------------------------------------------------------------------------
# Ticket model
# ---------------------------------------------------------------------------

class TicketModel:
    """
    Factory + serialiser for ticket documents.

    Firestore document shape::

        {
            "customer_email": str,
            "subject":        str,
            "description":    str,
            "department_id":  str,
            "priority":       str,        # P1 | P2 | P3 | P4
            "status":         str,        # Open | In Progress | Resolved | Closed
            "assigned_to":    str,        # agent_id or ""
            "assigned_name":  str,        # agent display name or ""
            "response_by":    datetime,
            "resolution_by":  datetime,
            "response_breached":  bool,
            "resolution_breached": bool,
            "created_at":     datetime,
            "updated_at":     datetime,
        }
    """

    REQUIRED_FIELDS = ("customer_email", "subject", "description",
                       "department_id", "priority")

    @classmethod
    def from_payload(cls, data: dict) -> dict:
        """
        Build a ticket dict from a raw JSON payload.

        Raises ``ValueError`` on missing / invalid fields.
        """
        missing = [f for f in cls.REQUIRED_FIELDS if not data.get(f)]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        priority = data["priority"].upper()
        if priority not in ("P1", "P2", "P3", "P4"):
            raise ValueError(f"Invalid priority '{priority}'. Must be P1, P2, P3, or P4")

        now = utcnow()
        return {
            "customer_email":      str(data["customer_email"]).strip().lower(),
            "subject":             str(data["subject"]).strip(),
            "description":         str(data["description"]).strip(),
            "department_id":       str(data["department_id"]).strip(),
            "priority":            priority,
            "status":              "Open",
            "assigned_to":         "",
            "assigned_name":       "",
            "response_by":         None,   # set by SLA calc
            "resolution_by":       None,   # set by SLA calc
            "response_breached":   False,
            "resolution_breached": False,
            "created_at":          now,
            "updated_at":          now,
        }

    @staticmethod
    def serialise(ticket: dict) -> dict:
        """Return a JSON-safe copy of a ticket dict."""
        return serialise(ticket)


# ---------------------------------------------------------------------------
# Agent model
# ---------------------------------------------------------------------------

class AgentModel:
    """
    Factory for agent documents.

    Firestore document shape::

        {
            "name":           str,
            "email":          str,
            "department_id":  str,
            "active":         bool,
            "created_at":     datetime,
        }
    """

    REQUIRED_FIELDS = ("name", "email", "department_id")

    @classmethod
    def from_payload(cls, data: dict) -> dict:
        missing = [f for f in cls.REQUIRED_FIELDS if not data.get(f)]
        if missing:
            raise ValueError(f"Missing required fields: {', '.join(missing)}")

        return {
            "name":          str(data["name"]).strip(),
            "email":         str(data["email"]).strip().lower(),
            "department_id": str(data["department_id"]).strip(),
            "active":        True,
            "created_at":    utcnow(),
        }

    @staticmethod
    def serialise(agent: dict) -> dict:
        return serialise(agent)
