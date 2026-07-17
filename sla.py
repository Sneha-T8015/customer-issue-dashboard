"""
sla.py
------
SLA policy look-up, deadline calculation, breach detection,
and the background escalation worker.

How it works
------------
1. ``get_sla_policy(db, department_id, priority)`` fetches the matching
   row from the ``sla_policies`` collection.
2. ``compute_deadlines(policy, created_at)`` returns exact UTC
   ``response_by`` and ``resolution_by`` datetimes.
3. ``check_breaches(db)`` is called every 60 seconds by the APScheduler
   background thread.  It queries every unresolved ticket whose
   ``response_by`` or ``resolution_by`` is in the past and flips the
   corresponding breach flag to ``True``.
"""

import logging
from datetime import timedelta, timezone
from typing import Optional

from firebase_admin import firestore

from firebase_config import (
    get_db,
    SLA_POLICIES_COLLECTION,
    TICKETS_COLLECTION,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SLA policy helpers
# ---------------------------------------------------------------------------

def get_sla_policy(
    db,
    department_id: str,
    priority: str,
) -> Optional[dict]:
    """
    Look up the SLA policy row for a given department + priority.

    Returns a dict ``{"response_hours": int, "resolution_hours": int}``
    or ``None`` when no matching policy is found.
    """
    try:
        query = (
            db.collection(SLA_POLICIES_COLLECTION)
            .where("department_id", "==", department_id)
            .where("priority", "==", priority)
            .limit(1)
        )
        docs = list(query.stream())
        if not docs:
            logger.warning(
                "No SLA policy found for dept=%s priority=%s", department_id, priority,
            )
            return None
        return docs[0].to_dict()
    except Exception:
        logger.exception("Error fetching SLA policy for dept=%s priority=%s",
                         department_id, priority)
        return None


def compute_deadlines(
    policy: dict,
    created_at: datetime,
) -> tuple[datetime, datetime]:
    """
    Given an SLA policy and a ticket creation time, return the exact
    UTC ``response_by`` and ``resolution_by`` datetimes.

    Raises ``ValueError`` if the policy is missing required keys.
    """
    resp_hrs = policy.get("response_hours")
    res_hrs = policy.get("resolution_hours")

    if resp_hrs is None or res_hrs is None:
        raise ValueError(
            "SLA policy must define response_hours and resolution_hours"
        )

    response_by = created_at + timedelta(hours=float(resp_hrs))
    resolution_by = created_at + timedelta(hours=float(res_hrs))
    return response_by, resolution_by


# ---------------------------------------------------------------------------
# Breach detection
# ---------------------------------------------------------------------------

def check_breaches() -> dict:
    """
    Scan every unresolved ticket and mark SLA breaches.

    Returns a summary dict ``{"checked": int, "response_breaches": int,
    "resolution_breaches": int}`` for logging / test purposes.
    """
    try:
        db = get_db()
    except Exception:
        logger.exception("Failed to get Firestore client during breach check")
        return {"checked": 0, "response_breaches": 0, "resolution_breaches": 0}

    now = datetime.now(timezone.utc)
    summary = {"checked": 0, "response_breaches": 0, "resolution_breaches": 0}

    try:
        # Only pull tickets that are still open / in-progress.
        # Firestore `in` filter limited to 30 values – 2 is fine.
        open_tickets = (
            db.collection(TICKETS_COLLECTION)
            .where("status", "in", ["Open", "In Progress"])
            .stream()
        )

        batch = db.batch()
        writes_in_batch = 0

        for doc in open_tickets:
            summary["checked"] += 1
            ticket = doc.to_dict()
            ticket_id = doc.id
            ref = db.collection(TICKETS_COLLECTION).document(ticket_id)

            update: dict = {}

            # --- Response SLA breach check -----------------------------------
            resp_by = ticket.get("response_by")
            if (
                resp_by is not None
                and not ticket.get("response_breached", False)
                and isinstance(resp_by, datetime)
            ):
                if resp_by.tzinfo is None:
                    resp_by = resp_by.replace(tzinfo=timezone.utc)
                if now > resp_by:
                    update["response_breached"] = True
                    summary["response_breaches"] += 1
                    _log_escalation(
                        ticket_id=ticket,
                        breach_type="RESPONSE",
                        deadline=resp_by,
                        now=now,
                    )

            # --- Resolution SLA breach check ---------------------------------
            res_by = ticket.get("resolution_by")
            if (
                res_by is not None
                and not ticket.get("resolution_breached", False)
                and isinstance(res_by, datetime)
            ):
                if res_by.tzinfo is None:
                    res_by = res_by.replace(tzinfo=timezone.utc)
                if now > res_by:
                    update["resolution_breached"] = True
                    summary["resolution_breaches"] += 1
                    _log_escalation(
                        ticket_id=ticket,
                        breach_type="RESOLUTION",
                        deadline=res_by,
                        now=now,
                    )

            if update:
                update["updated_at"] = now
                batch.update(ref, update)
                writes_in_batch += 1

                # Firestore batches support max 500 ops – commit and start
                # a new batch when we approach the limit.
                if writes_in_batch >= 450:
                    batch.commit()
                    batch = db.batch()
                    writes_in_batch = 0

        # Final commit for remaining writes
        if writes_in_batch > 0:
            batch.commit()

        logger.info(
            "SLA breach scan complete – checked=%d response_breaches=%d "
            "resolution_breaches=%d",
            summary["checked"],
            summary["response_breaches"],
            summary["resolution_breaches"],
        )

    except Exception:
        logger.exception("Error during SLA breach scan")

    return summary


def _log_escalation(
    ticket: dict,
    breach_type: str,
    deadline: datetime,
    now: datetime,
) -> None:
    """
    Print a mock escalation alert to the console.

    In production this would call PagerDuty / email / Slack webhook.
    """
    overdue_mins = int((now - deadline).total_seconds() / 60)
    logger.warning(
        "SLA ESCALATION ALERT  |  Ticket: %s  |  Type: %s  |  "
        "Dept: %s  |  Priority: %s  |  Assigned: %s (%s)  |  "
        "Deadline: %s  |  Overdue by: %d min  |  "
        "Supervisor notification queued",
        ticket.get("subject", "?"),
        breach_type,
        ticket.get("department_id", "?"),
        ticket.get("priority", "?"),
        ticket.get("assigned_name", "Unassigned"),
        ticket.get("assigned_to", "n/a"),
        deadline.isoformat(),
        overdue_mins,
    )


# ---------------------------------------------------------------------------
# Background scheduler bootstrap
# ---------------------------------------------------------------------------

_scheduler = None


def start_escalation_worker(interval_seconds: int = 60):
    """
    Start the APScheduler ``BackgroundScheduler`` that runs
    :func:`check_breaches` every *interval_seconds*.

    Safe to call multiple times – the scheduler is only created once.
    """
    global _scheduler
    if _scheduler is not None:
        logger.info("Escalation worker already running – skipping")
        return _scheduler

    try:
        from apscheduler.schedulers.background import BackgroundScheduler
    except ImportError:
        logger.error(
            "APScheduler is not installed.  "
            "Install it with:  pip install APScheduler"
        )
        return None

    _scheduler = BackgroundScheduler()
    _scheduler.add_job(
        check_breaches,
        "interval",
        seconds=interval_seconds,
        id="sla_breach_checker",
        replace_existing=True,
        max_instances=1,
    )
    _scheduler.start()
    logger.info(
        "SLA escalation worker started – polling every %ds", interval_seconds,
    )
    return _scheduler


def stop_escalation_worker():
    """Gracefully shut down the background scheduler (if running)."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("SLA escalation worker stopped")
