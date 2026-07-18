"""
services.py
-----------
Firestore service layer.

All Firestore reads and writes are isolated behind static methods so
that the Flask routes never touch the SDK directly.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from firebase_admin import firestore

from firebase_config import (
    get_db,
    DEPARTMENTS_COLLECTION,
    SLA_POLICIES_COLLECTION,
    TICKETS_COLLECTION,
    AGENTS_COLLECTION,
)
from models import utcnow, doc_to_dict

logger = logging.getLogger(__name__)


# =========================================================================
# Department Service
# =========================================================================

class DepartmentService:
    @staticmethod
    def list_departments() -> list[dict]:
        db = get_db()
        docs = db.collection(DEPARTMENTS_COLLECTION).order_by("name").stream()
        return [doc_to_dict(d) for d in docs]

    @staticmethod
    def get_department(dept_id: str) -> Optional[dict]:
        db = get_db()
        return doc_to_dict(
            db.collection(DEPARTMENTS_COLLECTION).document(dept_id).get()
        )


# =========================================================================
# SLA Policy Service
# =========================================================================

class SLAPolicyService:
    @staticmethod
    def list_policies(department_id: str = "") -> list[dict]:
        db = get_db()
        query = db.collection(SLA_POLICIES_COLLECTION)
        if department_id:
            query = query.where("department_id", "==", department_id)
        return [doc_to_dict(d) for d in query.stream()]

    @staticmethod
    def get_policy(department_id: str, priority: str) -> Optional[dict]:
        db = get_db()
        docs = (
            db.collection(SLA_POLICIES_COLLECTION)
            .where("department_id", "==", department_id)
            .where("priority", "==", priority)
            .limit(1)
            .stream()
        )
        for doc in docs:
            return doc_to_dict(doc)
        return None


# =========================================================================
# Ticket Service
# =========================================================================

class TicketService:
    @staticmethod
    def create_ticket(data: dict) -> dict:
        db = get_db()
        ref = db.collection(TICKETS_COLLECTION).add(data)
        data["id"] = ref[1].id
        logger.info("Ticket created: %s", data["id"])
        return data

    @staticmethod
    def get_ticket(ticket_id: str) -> Optional[dict]:
        db = get_db()
        return doc_to_dict(
            db.collection(TICKETS_COLLECTION).document(ticket_id).get()
        )

    @staticmethod
    def update_ticket(ticket_id: str, fields: dict) -> Optional[dict]:
        db = get_db()
        ref = db.collection(TICKETS_COLLECTION).document(ticket_id)
        if not ref.get().exists:
            return None
        fields["updated_at"] = utcnow()
        ref.update(fields)
        logger.info("Ticket %s updated", ticket_id)
        return doc_to_dict(ref.get())

    @staticmethod
    def delete_ticket(ticket_id: str) -> bool:
        db = get_db()
        ref = db.collection(TICKETS_COLLECTION).document(ticket_id)
        if not ref.get().exists:
            return False
        ref.delete()
        logger.info("Ticket deleted: %s", ticket_id)
        return True

    @staticmethod
    def list_tickets(
        department_id: str = "",
        status: str = "",
        priority: str = "",
        assigned_to: str = "",
        limit: int = 500,
    ) -> list[dict]:
        db = get_db()
        # Fetch all tickets then filter in Python to avoid Firestore
        # composite index requirements on multi-field queries.
        docs = db.collection(TICKETS_COLLECTION).stream()
        results = []
        for doc in docs:
            data = doc_to_dict(doc)
            if not data:
                continue
            if department_id and data.get("department_id") != department_id:
                continue
            if status and data.get("status") != status:
                continue
            if priority and data.get("priority") != priority:
                continue
            if assigned_to and data.get("assigned_to") != assigned_to:
                continue
            results.append(data)

        def _sort_key(ticket: dict):
            value = ticket.get("created_at") or ticket.get("updated_at")
            if isinstance(value, str):
                try:
                    return datetime.fromisoformat(value)
                except Exception:
                    return datetime.min.replace(tzinfo=timezone.utc)
            if isinstance(value, datetime):
                return value
            return datetime.min.replace(tzinfo=timezone.utc)

        results.sort(key=_sort_key, reverse=True)
        return results[:limit]

    @staticmethod
    def list_active_for_agent(agent_id: str) -> list[dict]:
        db = get_db()
        docs = (
            db.collection(TICKETS_COLLECTION)
            .where("assigned_to", "==", agent_id)
            .stream()
        )
        results = []
        for doc in docs:
            data = doc_to_dict(doc)
            if data and data.get("status") in ("Open", "In Progress"):
                results.append(data)
        results.sort(key=lambda x: x.get("created_at", ""), reverse=False)
        return results

    @staticmethod
    def get_stats() -> dict:
        db = get_db()
        all_tickets = list(db.collection(TICKETS_COLLECTION).stream())
        counts: dict[str, int] = {}
        for t in all_tickets:
            s = t.to_dict().get("status", "Unknown")
            counts[s] = counts.get(s, 0) + 1
        return {
            "total":       len(all_tickets),
            "open":        counts.get("Open", 0),
            "in_progress": counts.get("In Progress", 0),
            "resolved":    counts.get("Resolved", 0),
            "closed":      counts.get("Closed", 0),
        }


# =========================================================================
# Agent Service
# =========================================================================

class AgentService:
    @staticmethod
    def create_agent(data: dict) -> dict:
        db = get_db()
        ref = db.collection(AGENTS_COLLECTION).add(data)
        data["id"] = ref[1].id
        logger.info("Agent created: %s", data["id"])
        return data

    @staticmethod
    def get_agent(agent_id: str) -> Optional[dict]:
        db = get_db()
        return doc_to_dict(
            db.collection(AGENTS_COLLECTION).document(agent_id).get()
        )

    @staticmethod
    def list_agents(department_id: str = "") -> list[dict]:
        db = get_db()
        query = db.collection(AGENTS_COLLECTION)
        if department_id:
            query = query.where("department_id", "==", department_id)
        return [doc_to_dict(d) for d in query.order_by("name").stream()]

    @staticmethod
    def update_agent(agent_id: str, fields: dict) -> Optional[dict]:
        db = get_db()
        ref = db.collection(AGENTS_COLLECTION).document(agent_id)
        if not ref.get().exists:
            return None
        fields["updated_at"] = utcnow()
        ref.update(fields)
        return doc_to_dict(ref.get())

    @staticmethod
    def delete_agent(agent_id: str) -> bool:
        db = get_db()
        ref = db.collection(AGENTS_COLLECTION).document(agent_id)
        if not ref.get().exists:
            return False
        ref.delete()
        logger.info("Agent deleted: %s", agent_id)
        return True


# =========================================================================
# Comment Service  (sub-collection under each ticket)
# =========================================================================

class CommentService:
    @staticmethod
    def add_comment(ticket_id: str, data: dict) -> dict:
        db = get_db()
        now = utcnow()
        comment = {
            "author":       data["author"],
            "author_email": data.get("author_email", ""),
            "body":         data["body"],
            "is_internal":  data.get("is_internal", False),
            "created_at":   now,
        }
        ref = db.collection(TICKETS_COLLECTION).document(ticket_id).collection("comments").add(comment)
        comment["id"] = ref[1].id
        # Touch updated_at on parent ticket
        db.collection(TICKETS_COLLECTION).document(ticket_id).update({"updated_at": now})
        logger.info("Comment added to %s: %s", ticket_id, comment["id"])
        return comment

    @staticmethod
    def list_comments(ticket_id: str) -> list[dict]:
        db = get_db()
        docs = (
            db.collection(TICKETS_COLLECTION)
            .document(ticket_id)
            .collection("comments")
            .order_by("created_at", direction=firestore.Query.ASCENDING)
            .stream()
        )
        return [doc_to_dict(d) for d in docs]

    @staticmethod
    def delete_comment(ticket_id: str, comment_id: str) -> bool:
        db = get_db()
        ref = (
            db.collection(TICKETS_COLLECTION)
            .document(ticket_id)
            .collection("comments")
            .document(comment_id)
        )
        if not ref.get().exists:
            return False
        ref.delete()
        logger.info("Comment deleted from %s: %s", ticket_id, comment_id)
        return True
