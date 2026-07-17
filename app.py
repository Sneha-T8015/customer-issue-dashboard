"""
app.py
------
Flask application entry-point.

Routes
------
  GET  /api/init                         – seed departments + SLA policies
  POST /api/tickets/create               – create ticket with auto SLA + round-robin
  GET  /api/tickets/assigned/<agent_id>  – agent dashboard with remaining SLA time
  GET  /api/tickets                      – list / filter tickets
  GET  /api/tickets/<ticket_id>          – single ticket detail
  PATCH /api/tickets/<ticket_id>         – update ticket fields
  DELETE /api/tickets/<ticket_id>        – delete a ticket
  GET  /api/departments                  – list departments
  GET  /api/sla/<department_id>          – SLA policies for a department
  GET  /api/stats                        – aggregate counts
  GET  /api/agents                       – list agents
  POST /api/agents                       – create agent

The SLA escalation background worker is started once at module import
time and runs every 60 seconds.
"""

import logging
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request

# --- Firebase & services ---------------------------------------------------
from firebase_config import init_firebase, get_db, seed_defaults, PRIORITY_CODES
from services import (
    DepartmentService,
    SLAPolicyService,
    TicketService,
    AgentService,
)
from sla import (
    get_sla_policy,
    compute_deadlines,
    start_escalation_worker,
)
from round_robin import pick_agent
from models import TicketModel, AgentModel, utcnow, serialise
from validators import (
    validate_ticket_create,
    validate_agent_create,
    ValidationError,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-fallback-key-change-me")


# ---------------------------------------------------------------------------
# Bootstrap: connect to Firestore, seed data, start background worker
# ---------------------------------------------------------------------------

@app.before_request
def _ensure_bootstrap():
    """
    Lazily run one-time bootstrap on the very first request so that
    ``import app`` does not require network access (useful in tests).
    """
    if getattr(app, "_bootstrapped", False):
        return
    try:
        db = init_firebase()
        seed_defaults(db)
        start_escalation_worker(interval_seconds=60)
        app._bootstrapped = True
    except Exception:
        logger.exception("Bootstrap failed – app may be non-functional")


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def server_error(e):
    logger.exception("Internal server error")
    return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  1.  POST /api/tickets/create
# ===================================================================

@app.route("/api/tickets/create", methods=["POST"])
def api_create_ticket():
    """
    Create a new support ticket.

    **Request JSON body**::

        {
            "customer_email": "user@example.com",
            "subject":        "Cannot log in",
            "description":    "I get a 403 error every time …",
            "department_id":  "IT",
            "priority":       "P1"
        }

    **Behaviour**
    1. Validate payload.
    2. Fetch the matching SLA policy for (department_id, priority).
    3. Compute exact ``response_by`` and ``resolution_by`` timestamps.
    4. Run round-robin assignment across agents in the department.
    5. Persist to Firestore and return the full ticket.
    """
    try:
        # ---- 1. Validate -------------------------------------------------
        try:
            payload = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "Request body must be valid JSON"}), 400

        try:
            clean = validate_ticket_create(payload)
        except ValidationError as exc:
            return jsonify({"error": "Validation failed", "details": exc.errors}), 400

        db = get_db()
        now = utcnow()

        # ---- 2. Fetch SLA policy -----------------------------------------
        policy = get_sla_policy(db, clean["department_id"], clean["priority"])
        if policy is None:
            return jsonify({
                "error": "No SLA policy configured",
                "details": {
                    "department_id": clean["department_id"],
                    "priority": clean["priority"],
                },
            }), 422

        # ---- 3. Compute deadlines ----------------------------------------
        try:
            response_by, resolution_by = compute_deadlines(policy, now)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 500

        # ---- 4. Round-robin assignment -----------------------------------
        agent = pick_agent(clean["department_id"])
        assigned_to = ""
        assigned_name = ""
        if agent:
            assigned_to = agent["id"]
            assigned_name = agent.get("name", "")

        # ---- 5. Build ticket dict ----------------------------------------
        ticket_data = TicketModel.from_payload(clean)
        ticket_data["response_by"] = response_by
        ticket_data["resolution_by"] = resolution_by
        ticket_data["assigned_to"] = assigned_to
        ticket_data["assigned_name"] = assigned_name

        # ---- 6. Persist --------------------------------------------------
        ticket = TicketService.create_ticket(ticket_data)

        logger.info(
            "Ticket created – id=%s priority=%s dept=%s assigned_to=%s "
            "response_by=%s resolution_by=%s",
            ticket["id"],
            clean["priority"],
            clean["department_id"],
            assigned_name or "unassigned",
            response_by.isoformat(),
            resolution_by.isoformat(),
        )

        return jsonify({
            "ticket": TicketModel.serialise(ticket),
            "sla": {
                "response_hours": policy.get("response_hours"),
                "resolution_hours": policy.get("resolution_hours"),
                "response_by": serialise(response_by),
                "resolution_by": serialise(resolution_by),
            },
        }), 201

    except Exception:
        logger.exception("POST /api/tickets/create failed")
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  2.  GET /api/tickets/assigned/<agent_id>
# ===================================================================

@app.route("/api/tickets/assigned/<agent_id>", methods=["GET"])
def api_tickets_assigned(agent_id):
    """
    Return all active (Open / In Progress) tickets assigned to an agent,
    enriched with the remaining time (in minutes) before each SLA
    deadline is breached.

    **Response**::

        {
            "agent": { ... },
            "tickets": [
                {
                    "ticket": { ... },
                    "sla_remaining": {
                        "response_minutes": 42,
                        "resolution_minutes": 210,
                        "response_breached": false,
                        "resolution_breached": false,
                    }
                },
                ...
            ]
        }
    """
    try:
        agent = AgentService.get_agent(agent_id)
        if agent is None:
            return jsonify({"error": "Agent not found"}), 404

        tickets = TicketService.list_active_for_agent(agent_id)
        now = utcnow()

        enriched = []
        for t in tickets:
            resp_mins = _remaining_minutes(t.get("response_by"), now)
            res_mins = _remaining_minutes(t.get("resolution_by"), now)

            enriched.append({
                "ticket": TicketModel.serialise(t),
                "sla_remaining": {
                    "response_minutes":   resp_mins,
                    "resolution_minutes": res_mins,
                    "response_breached":  t.get("response_breached", False),
                    "resolution_breached": t.get("resolution_breached", False),
                },
            })

        return jsonify({
            "agent": AgentModel.serialise(agent),
            "tickets": enriched,
            "count": len(enriched),
        })

    except Exception:
        logger.exception("GET /api/tickets/assigned/%s failed", agent_id)
        return jsonify({"error": "Internal server error"}), 500


def _remaining_minutes(deadline, now: datetime) -> int | None:
    """Return whole minutes remaining until *deadline*, or None."""
    if deadline is None:
        return None
    if isinstance(deadline, datetime):
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        delta = deadline - now
        return int(delta.total_seconds() // 60)
    return None


# ===================================================================
#  3.  GET /api/tickets  (list / filter)
# ===================================================================

@app.route("/api/tickets", methods=["GET"])
def api_list_tickets():
    """List tickets with optional filters (query-string params)."""
    try:
        tickets = TicketService.list_tickets(
            department_id=request.args.get("department_id", ""),
            status=request.args.get("status", ""),
            priority=request.args.get("priority", ""),
            assigned_to=request.args.get("assigned_to", ""),
        )
        return jsonify({
            "tickets": [TicketModel.serialise(t) for t in tickets],
            "count": len(tickets),
        })
    except Exception:
        logger.exception("GET /api/tickets failed")
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  4.  GET /api/tickets/<ticket_id>
# ===================================================================

@app.route("/api/tickets/<ticket_id>", methods=["GET"])
def api_get_ticket(ticket_id):
    """Fetch a single ticket by its Firestore document ID."""
    try:
        ticket = TicketService.get_ticket(ticket_id)
        if ticket is None:
            return jsonify({"error": "Ticket not found"}), 404
        return jsonify({"ticket": TicketModel.serialise(ticket)})
    except Exception:
        logger.exception("GET /api/tickets/%s failed", ticket_id)
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  5.  PATCH /api/tickets/<ticket_id>
# ===================================================================

@app.route("/api/tickets/<ticket_id>", methods=["PATCH"])
def api_update_ticket(ticket_id):
    """Update one or more fields on an existing ticket."""
    try:
        payload = request.get_json(force=True)
        allowed = {
            "status", "assigned_to", "assigned_name",
            "subject", "description", "priority",
        }
        fields = {k: v for k, v in payload.items() if k in allowed}
        if not fields:
            return jsonify({"error": "No valid fields provided"}), 400

        # Validate priority if being changed
        if "priority" in fields:
            p = fields["priority"].upper()
            if p not in PRIORITY_CODES:
                return jsonify({"error": f"Invalid priority. Use {PRIORITY_CODES}"}), 400
            fields["priority"] = p

        ticket = TicketService.update_ticket(ticket_id, fields)
        if ticket is None:
            return jsonify({"error": "Ticket not found"}), 404
        return jsonify({"ticket": TicketModel.serialise(ticket)})

    except Exception:
        logger.exception("PATCH /api/tickets/%s failed", ticket_id)
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  6.  DELETE /api/tickets/<ticket_id>
# ===================================================================

@app.route("/api/tickets/<ticket_id>", methods=["DELETE"])
def api_delete_ticket(ticket_id):
    """Delete a ticket."""
    try:
        deleted = TicketService.delete_ticket(ticket_id)
        if not deleted:
            return jsonify({"error": "Ticket not found"}), 404
        return jsonify({"message": "Ticket deleted"}), 200
    except Exception:
        logger.exception("DELETE /api/tickets/%s failed", ticket_id)
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  7.  GET /api/departments
# ===================================================================

@app.route("/api/departments", methods=["GET"])
def api_list_departments():
    """List all departments."""
    try:
        depts = DepartmentService.list_departments()
        return jsonify({"departments": depts, "count": len(depts)})
    except Exception:
        logger.exception("GET /api/departments failed")
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  8.  GET /api/sla/<department_id>
# ===================================================================

@app.route("/api/sla/<department_id>", methods=["GET"])
def api_sla_policies(department_id):
    """Return all SLA policies for a given department."""
    try:
        policies = SLAPolicyService.list_policies(department_id)
        return jsonify({
            "department_id": department_id,
            "policies": [serialise(p) for p in policies],
            "count": len(policies),
        })
    except Exception:
        logger.exception("GET /api/sla/%s failed", department_id)
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  9.  GET /api/stats
# ===================================================================

@app.route("/api/stats", methods=["GET"])
def api_stats():
    """Aggregate ticket counts by status."""
    try:
        return jsonify(TicketService.get_stats())
    except Exception:
        logger.exception("GET /api/stats failed")
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  10. Agent CRUD
# ===================================================================

@app.route("/api/agents", methods=["GET"])
def api_list_agents():
    """List agents, optionally filtered by department_id."""
    try:
        dept = request.args.get("department_id", "")
        agents = AgentService.list_agents(department_id=dept)
        return jsonify({
            "agents": [AgentModel.serialise(a) for a in agents],
            "count": len(agents),
        })
    except Exception:
        logger.exception("GET /api/agents failed")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/agents", methods=["POST"])
def api_create_agent():
    """Create a new agent."""
    try:
        try:
            payload = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "Request body must be valid JSON"}), 400

        try:
            clean = validate_agent_create(payload)
        except ValidationError as exc:
            return jsonify({"error": "Validation failed", "details": exc.errors}), 400

        agent_data = AgentModel.from_payload(clean)
        agent = AgentService.create_agent(agent_data)
        return jsonify({"agent": AgentModel.serialise(agent)}), 201

    except Exception:
        logger.exception("POST /api/agents failed")
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  11. POST /api/init  – re-seed on demand
# ===================================================================

@app.route("/api/init", methods=["POST"])
def api_init():
    """Re-run the seed process (departments + SLA policies)."""
    try:
        db = get_db()
        seed_defaults(db)
        return jsonify({"message": "Seed complete"}), 200
    except Exception:
        logger.exception("POST /api/init failed")
        return jsonify({"error": "Seed failed"}), 500


# ---------------------------------------------------------------------------
# Local dev server
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 5000)),
        debug=False,
    )
