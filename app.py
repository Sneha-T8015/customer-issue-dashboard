"""
app.py
------
Flask application entry-point.

Routes
------
  UI pages:
    GET  /                                 – dashboard with filters
    GET  /analytics                        – analytics charts
    GET  /agents                           – agents list
    POST /agents/create                    – create agent (form)
    POST /agents/<id>/edit                 – edit agent (form)
    POST /agents/<id>/delete               – delete agent
    GET  /issues/new                       – new ticket form
    POST /issues/new                       – create ticket (form)
    GET  /issues/<id>                      – ticket detail
    POST /issues/<id>/edit                 – edit ticket (form)
    POST /issues/<id>/delete               – delete ticket
    POST /issues/<id>/status               – update status (form)
    POST /issues/<id>/assign               – assign agent (form)
    POST /issues/<id>/comment              – add comment (form)
    POST /issues/<id>/comment/<cid>/delete – delete comment

  REST API:
    POST /api/tickets/create               – create with SLA + round-robin
    GET  /api/tickets/assigned/<agent_id>  – agent dashboard + SLA remaining
    GET  /api/tickets                      – list / filter
    GET  /api/tickets/<id>                 – single ticket
    PATCH /api/tickets/<id>                – update fields
    DELETE /api/tickets/<id>               – delete
    GET  /api/departments                  – list departments
    GET  /api/sla/<dept_id>                – SLA policies
    GET  /api/stats                        – aggregate counts
    GET  /api/agents                       – list agents
    POST /api/agents                       – create agent
    POST /api/init                         – re-seed
"""

import logging
import os
from datetime import datetime, timezone

from flask import Flask, jsonify, request, render_template, redirect, url_for, flash

# --- Firebase & services ---------------------------------------------------
from firebase_config import init_firebase, get_db, seed_defaults, PRIORITY_CODES
from services import (
    DepartmentService,
    SLAPolicyService,
    TicketService,
    AgentService,
    CommentService,
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

STATUSES = ["Open", "In Progress", "Resolved", "Closed"]
DEPARTMENT_FALLBACKS = ["IT", "software", "billing", "learning_kit"]


# ---------------------------------------------------------------------------
# Bootstrap: connect to Firestore, seed data, start background worker
# ---------------------------------------------------------------------------

@app.before_request
def _ensure_bootstrap():
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
# Helpers – map Firestore ticket shape → template-friendly dict
# ---------------------------------------------------------------------------

def _ticket_to_issue(ticket):
    """Normalise a raw Firestore ticket dict into the flat shape the
    templates expect (issue.title, issue.customer_name, etc.)."""
    if not ticket:
        return None
    email = ticket.get("customer_email") or ""
    return {
        "id":               ticket.get("id"),
        "title":            ticket.get("subject") or ticket.get("title") or "Untitled",
        "customer_name":    ticket.get("customer_name") or (email.split("@", 1)[0] if email else "Customer"),
        "customer_email":   email,
        "category":         ticket.get("department_id") or ticket.get("category") or "General",
        "priority":         ticket.get("priority") or "P3",
        "status":           ticket.get("status") or "Open",
        "assigned_to":      ticket.get("assigned_name") or ticket.get("assigned_to") or "",
        "created_at":       ticket.get("created_at"),
        "updated_at":       ticket.get("updated_at"),
        "description":      ticket.get("description") or "",
        "resolution_notes": ticket.get("resolution_notes") or "",
    }


def _agent_to_ui(agent):
    if not agent:
        return None
    return {
        "id":     agent.get("id"),
        "name":   agent.get("name") or "Agent",
        "email":  agent.get("email") or "",
        "role":   agent.get("department_id") or "Agent",
        "active": agent.get("active", True),
    }


def _dept_options():
    try:
        opts = [d.get("id") or d.get("name") for d in DepartmentService.list_departments()]
        return opts if opts else DEPARTMENT_FALLBACKS
    except Exception:
        return DEPARTMENT_FALLBACKS


def _agent_list():
    try:
        return [_agent_to_ui(a) for a in AgentService.list_agents() if _agent_to_ui(a)]
    except Exception:
        return []


def _build_stats(issues):
    counts = {}
    for i in issues:
        s = i.get("status", "Open")
        counts[s] = counts.get(s, 0) + 1
    return {
        "total":       len(issues),
        "open":        counts.get("Open", 0),
        "in_progress": counts.get("In Progress", 0),
        "resolved":    counts.get("Resolved", 0),
        "closed":      counts.get("Closed", 0),
    }


def _serialize_dates(obj):
    """Recursively convert datetime objects to ISO strings."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_serialize_dates(i) for i in obj]
    return obj


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not found"}), 404
    flash("Page not found.", "danger")
    return redirect(url_for("index"))


@app.errorhandler(500)
def server_error(e):
    logger.exception("Internal server error")
    if request.path.startswith("/api/"):
        return jsonify({"error": "Internal server error"}), 500
    flash("An unexpected error occurred.", "danger")
    return redirect(url_for("index"))


# ===================================================================
#  UI – Dashboard
# ===================================================================

@app.route("/")
def index():
    status_filter = request.args.get("status", "")
    priority_filter = request.args.get("priority", "")
    category_filter = request.args.get("category", "")
    search = request.args.get("search", "").strip()

    try:
        kwargs = {}
        if status_filter:
            kwargs["status"] = status_filter
        if priority_filter:
            kwargs["priority"] = priority_filter
        if category_filter:
            kwargs["department_id"] = category_filter
        tickets = TicketService.list_tickets(limit=500, **kwargs)
        issues = [_ticket_to_issue(t) for t in tickets]

        # Client-side search filter (Firestore can't do full-text)
        if search:
            s = search.lower()
            issues = [
                i for i in issues
                if s in (i.get("title", "") + i.get("customer_name", "")
                         + i.get("customer_email", "") + i.get("description", "")).lower()
            ]

        stats = _build_stats(issues)
    except Exception:
        logger.exception("Dashboard load failed")
        issues, stats = [], {"total": 0, "open": 0, "in_progress": 0, "resolved": 0, "closed": 0}

    return render_template(
        "index.html",
        issues=issues,
        stats=stats,
        search=search,
        status_filter=status_filter,
        priority_filter=priority_filter,
        category_filter=category_filter,
        statuses=STATUSES,
        priorities=PRIORITY_CODES,
        categories=_dept_options(),
    )


# ===================================================================
#  UI – Analytics
# ===================================================================

@app.route("/analytics")
def analytics():
    try:
        tickets = TicketService.list_tickets(limit=1000)
        issues = [_ticket_to_issue(t) for t in tickets]
        stats = _build_stats(issues)
        by_status = [(s, c) for s, c in [
            ("Open", stats["open"]), ("In Progress", stats["in_progress"]),
            ("Resolved", stats["resolved"]), ("Closed", stats["closed"]),
        ] if c]
        by_priority = [(p, sum(1 for i in issues if i.get("priority") == p))
                       for p in PRIORITY_CODES]
        by_priority = [(p, c) for p, c in by_priority if c]
        cats = sorted({i.get("category", "General") for i in issues})
        by_category = [(c, sum(1 for i in issues if i.get("category") == c)) for c in cats]
    except Exception:
        logger.exception("Analytics failed")
        stats = {"total": 0, "open": 0, "in_progress": 0, "resolved": 0, "closed": 0}
        by_status = by_priority = by_category = []

    return render_template(
        "analytics.html",
        total=stats["total"],
        last_30_days=0,
        last_7_days=0,
        by_status=by_status,
        by_priority=by_priority,
        by_category=by_category,
    )


# ===================================================================
#  UI – Agents
# ===================================================================

@app.route("/agents")
def agents_list():
    return render_template("agents.html", agents=_agent_list())


@app.route("/agents/create", methods=["GET", "POST"])
def create_agent():
    if request.method == "POST":
        try:
            data = validate_agent_create(request.form.to_dict())
            agent_data = AgentModel.from_payload(data)
            AgentService.create_agent(agent_data)
            flash("Agent created successfully.", "success")
            return redirect(url_for("agents_list"))
        except ValidationError as exc:
            flash(exc.message, "danger")
        except Exception:
            logger.exception("Create agent failed")
            flash("Failed to create agent.", "danger")
    return render_template("agent_form.html", agent=None, departments=_dept_options())


@app.route("/agents/<agent_id>/edit", methods=["GET", "POST"])
def edit_agent(agent_id):
    agent = AgentService.get_agent(agent_id)
    if not agent:
        flash("Agent not found.", "danger")
        return redirect(url_for("agents_list"))
    ui = _agent_to_ui({**agent, "id": agent_id})
    if request.method == "POST":
        try:
            clean = validate_agent_create(request.form.to_dict())
            AgentService.update_agent(agent_id, {
                "name": clean["name"],
                "email": clean["email"],
                "department_id": clean["department_id"],
            })
            flash("Agent updated.", "success")
            return redirect(url_for("agents_list"))
        except ValidationError as exc:
            flash(exc.message, "danger")
        except Exception:
            logger.exception("Edit agent failed")
            flash("Failed to update agent.", "danger")
    return render_template("agent_form.html", agent=ui, departments=_dept_options())


@app.route("/agents/<agent_id>/delete", methods=["POST"])
def delete_agent(agent_id):
    try:
        AgentService.delete_agent(agent_id)
        flash("Agent deleted.", "danger")
    except Exception:
        logger.exception("Delete agent failed")
        flash("Failed to delete agent.", "danger")
    return redirect(url_for("agents_list"))


# ===================================================================
#  UI – Ticket CRUD (form-based)
# ===================================================================

@app.route("/issues/new", methods=["GET", "POST"])
def create_issue():
    if request.method == "POST":
        try:
            form = request.form.to_dict()
            # Build the payload the API validator expects
            payload = {
                "customer_email": form.get("customer_email", ""),
                "subject":        form.get("title", ""),
                "description":    form.get("description", ""),
                "department_id":  form.get("category", "IT"),
                "priority":       form.get("priority", "P3"),
            }
            clean = validate_ticket_create(payload)

            db = get_db()
            now = utcnow()

            # SLA
            policy = get_sla_policy(db, clean["department_id"], clean["priority"])
            if policy:
                response_by, resolution_by = compute_deadlines(policy, now)
            else:
                response_by = resolution_by = now

            # Round-robin
            agent = pick_agent(clean["department_id"])
            assigned_to = agent["id"] if agent else ""
            assigned_name = agent.get("name", "") if agent else ""

            ticket_data = TicketModel.from_payload(clean)
            ticket_data["response_by"] = response_by
            ticket_data["resolution_by"] = resolution_by
            ticket_data["assigned_to"] = assigned_to
            ticket_data["assigned_name"] = assigned_name
            # Carry optional form fields
            ticket_data["customer_name"] = form.get("customer_name", "")
            ticket_data["resolution_notes"] = form.get("resolution_notes", "")

            ticket = TicketService.create_ticket(ticket_data)
            flash("Ticket created successfully.", "success")
            return redirect(url_for("issue_detail", issue_id=ticket["id"]))

        except ValidationError as exc:
            flash(exc.message, "danger")
        except Exception:
            logger.exception("Create ticket failed")
            flash("Failed to create ticket.", "danger")

    return render_template(
        "issue_form.html",
        issue=None,
        statuses=STATUSES,
        priorities=PRIORITY_CODES,
        categories=_dept_options(),
        agents=_agent_list(),
    )


@app.route("/issues/<issue_id>")
def issue_detail(issue_id):
    try:
        ticket = TicketService.get_ticket(issue_id)
        if not ticket:
            flash("Ticket not found.", "danger")
            return redirect(url_for("index"))
        issue = _ticket_to_issue(ticket)
        comments = CommentService.list_comments(issue_id)
        return render_template(
            "issue_detail.html",
            issue=issue,
            comments=comments,
            statuses=STATUSES,
            agents=_agent_list(),
        )
    except Exception:
        logger.exception("Ticket detail failed")
        flash("Failed to load ticket.", "danger")
        return redirect(url_for("index"))


@app.route("/issues/<issue_id>/edit", methods=["GET", "POST"])
def edit_issue(issue_id):
    ticket = TicketService.get_ticket(issue_id)
    if not ticket:
        flash("Ticket not found.", "danger")
        return redirect(url_for("index"))
    issue = _ticket_to_issue(ticket)

    if request.method == "POST":
        try:
            form = request.form.to_dict()
            fields = {
                "subject":         form.get("title", issue["title"]),
                "description":     form.get("description", ""),
                "customer_name":   form.get("customer_name", ""),
                "customer_email":  form.get("customer_email", ""),
                "status":          form.get("status", issue["status"]),
                "priority":        form.get("priority", issue["priority"]),
                "department_id":   form.get("category", issue["category"]),
                "assigned_to":     form.get("assigned_to", ""),
                "resolution_notes": form.get("resolution_notes", ""),
            }
            TicketService.update_ticket(issue_id, fields)
            flash("Ticket updated.", "success")
            return redirect(url_for("issue_detail", issue_id=issue_id))
        except Exception:
            logger.exception("Edit ticket failed")
            flash("Failed to update ticket.", "danger")

    return render_template(
        "issue_form.html",
        issue=issue,
        statuses=STATUSES,
        priorities=PRIORITY_CODES,
        categories=_dept_options(),
        agents=_agent_list(),
    )


@app.route("/issues/<issue_id>/delete", methods=["POST"])
def delete_issue(issue_id):
    try:
        TicketService.delete_ticket(issue_id)
        flash("Ticket deleted.", "danger")
    except Exception:
        logger.exception("Delete ticket failed")
        flash("Failed to delete ticket.", "danger")
    return redirect(url_for("index"))


@app.route("/issues/<issue_id>/status", methods=["POST"])
def update_status(issue_id):
    new_status = request.form.get("status", "")
    if new_status not in STATUSES:
        flash("Invalid status.", "danger")
        return redirect(url_for("issue_detail", issue_id=issue_id))
    try:
        TicketService.update_ticket(issue_id, {"status": new_status})
        flash(f"Status updated to {new_status}.", "success")
    except Exception:
        logger.exception("Status update failed")
        flash("Failed to update status.", "danger")
    return redirect(url_for("issue_detail", issue_id=issue_id))


@app.route("/issues/<issue_id>/assign", methods=["POST"])
def assign_ticket(issue_id):
    agent_id = request.form.get("assigned_to", "")
    try:
        agent_name = ""
        if agent_id:
            agent = AgentService.get_agent(agent_id)
            agent_name = agent.get("name", "") if agent else ""
        TicketService.update_ticket(issue_id, {
            "assigned_to": agent_id,
            "assigned_name": agent_name,
        })
        flash(f"Assigned to {agent_name}." if agent_name else "Assignment cleared.", "success")
    except Exception:
        logger.exception("Assign failed")
        flash("Failed to assign ticket.", "danger")
    return redirect(url_for("issue_detail", issue_id=issue_id))


# ===================================================================
#  UI – Comments
# ===================================================================

@app.route("/issues/<issue_id>/comment", methods=["POST"])
def add_comment(issue_id):
    try:
        author = (request.form.get("author") or "").strip()
        body = (request.form.get("body") or "").strip()
        if not author or not body:
            flash("Author and comment body are required.", "danger")
            return redirect(url_for("issue_detail", issue_id=issue_id))
        CommentService.add_comment(issue_id, {
            "author": author,
            "author_email": (request.form.get("author_email") or "").strip(),
            "body": body,
            "is_internal": bool(request.form.get("is_internal")),
        })
        flash("Comment added.", "success")
    except Exception:
        logger.exception("Add comment failed")
        flash("Failed to add comment.", "danger")
    return redirect(url_for("issue_detail", issue_id=issue_id))


@app.route("/issues/<issue_id>/comment/<comment_id>/delete", methods=["POST"])
def delete_comment(issue_id, comment_id):
    try:
        CommentService.delete_comment(issue_id, comment_id)
        flash("Comment deleted.", "danger")
    except Exception:
        logger.exception("Delete comment failed")
        flash("Failed to delete comment.", "danger")
    return redirect(url_for("issue_detail", issue_id=issue_id))


# ===================================================================
#  REST API – Ticket creation with SLA + round-robin
# ===================================================================

@app.route("/api/tickets/create", methods=["POST"])
def api_create_ticket():
    try:
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

        policy = get_sla_policy(db, clean["department_id"], clean["priority"])
        if policy is None:
            return jsonify({"error": "No SLA policy configured", "details": {
                "department_id": clean["department_id"], "priority": clean["priority"],
            }}), 422

        response_by, resolution_by = compute_deadlines(policy, now)

        agent = pick_agent(clean["department_id"])
        assigned_to = agent["id"] if agent else ""
        assigned_name = agent.get("name", "") if agent else ""

        ticket_data = TicketModel.from_payload(clean)
        ticket_data["response_by"] = response_by
        ticket_data["resolution_by"] = resolution_by
        ticket_data["assigned_to"] = assigned_to
        ticket_data["assigned_name"] = assigned_name

        ticket = TicketService.create_ticket(ticket_data)
        return jsonify({
            "ticket": _serialize_dates(ticket),
            "sla": {
                "response_hours": policy.get("response_hours"),
                "resolution_hours": policy.get("resolution_hours"),
                "response_by": _serialize_dates(response_by),
                "resolution_by": _serialize_dates(resolution_by),
            },
        }), 201

    except Exception:
        logger.exception("POST /api/tickets/create failed")
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  REST API – Agent dashboard
# ===================================================================

@app.route("/api/tickets/assigned/<agent_id>", methods=["GET"])
def api_tickets_assigned(agent_id):
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
                "ticket": _serialize_dates(t),
                "sla_remaining": {
                    "response_minutes":    resp_mins,
                    "resolution_minutes":  res_mins,
                    "response_breached":   t.get("response_breached", False),
                    "resolution_breached": t.get("resolution_breached", False),
                },
            })

        return jsonify({
            "agent": _serialize_dates({**agent, "id": agent_id}),
            "tickets": enriched,
            "count": len(enriched),
        })
    except Exception:
        logger.exception("GET /api/tickets/assigned/%s failed", agent_id)
        return jsonify({"error": "Internal server error"}), 500


def _remaining_minutes(deadline, now):
    if deadline is None:
        return None
    if isinstance(deadline, datetime):
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return int((deadline - now).total_seconds() // 60)
    return None


# ===================================================================
#  REST API – List / Get / Update / Delete
# ===================================================================

@app.route("/api/tickets", methods=["GET"])
def api_list_tickets():
    try:
        tickets = TicketService.list_tickets(
            department_id=request.args.get("department_id", ""),
            status=request.args.get("status", ""),
            priority=request.args.get("priority", ""),
            assigned_to=request.args.get("assigned_to", ""),
        )
        return jsonify({"tickets": [_serialize_dates(t) for t in tickets], "count": len(tickets)})
    except Exception:
        logger.exception("GET /api/tickets failed")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/tickets/<ticket_id>", methods=["GET"])
def api_get_ticket(ticket_id):
    try:
        ticket = TicketService.get_ticket(ticket_id)
        if ticket is None:
            return jsonify({"error": "Ticket not found"}), 404
        return jsonify({"ticket": _serialize_dates(ticket)})
    except Exception:
        logger.exception("GET /api/tickets/%s failed", ticket_id)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/tickets/<ticket_id>", methods=["PATCH"])
def api_update_ticket(ticket_id):
    try:
        payload = request.get_json(force=True)
        allowed = {"status", "assigned_to", "assigned_name", "subject", "description", "priority"}
        fields = {k: v for k, v in payload.items() if k in allowed}
        if not fields:
            return jsonify({"error": "No valid fields provided"}), 400
        if "priority" in fields:
            fields["priority"] = fields["priority"].upper()
        ticket = TicketService.update_ticket(ticket_id, fields)
        if ticket is None:
            return jsonify({"error": "Ticket not found"}), 404
        return jsonify({"ticket": _serialize_dates(ticket)})
    except Exception:
        logger.exception("PATCH /api/tickets/%s failed", ticket_id)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/tickets/<ticket_id>", methods=["DELETE"])
def api_delete_ticket(ticket_id):
    try:
        if TicketService.delete_ticket(ticket_id):
            return jsonify({"message": "Ticket deleted"}), 200
        return jsonify({"error": "Ticket not found"}), 404
    except Exception:
        logger.exception("DELETE /api/tickets/%s failed", ticket_id)
        return jsonify({"error": "Internal server error"}), 500


# ===================================================================
#  REST API – Departments, SLA, Stats, Agents, Init
# ===================================================================

@app.route("/api/departments", methods=["GET"])
def api_list_departments():
    try:
        depts = DepartmentService.list_departments()
        return jsonify({"departments": depts, "count": len(depts)})
    except Exception:
        logger.exception("GET /api/departments failed")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/sla/<department_id>", methods=["GET"])
def api_sla_policies(department_id):
    try:
        policies = SLAPolicyService.list_policies(department_id)
        return jsonify({"department_id": department_id, "policies": [_serialize_dates(p) for p in policies], "count": len(policies)})
    except Exception:
        logger.exception("GET /api/sla/%s failed", department_id)
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/stats", methods=["GET"])
def api_stats():
    try:
        return jsonify(TicketService.get_stats())
    except Exception:
        logger.exception("GET /api/stats failed")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/agents", methods=["GET"])
def api_list_agents():
    try:
        agents = AgentService.list_agents(department_id=request.args.get("department_id", ""))
        return jsonify({"agents": [_serialize_dates(a) for a in agents], "count": len(agents)})
    except Exception:
        logger.exception("GET /api/agents failed")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/agents", methods=["POST"])
def api_create_agent():
    try:
        try:
            payload = request.get_json(force=True)
        except Exception:
            return jsonify({"error": "Request body must be valid JSON"}), 400
        try:
            clean = validate_agent_create(payload)
        except ValidationError as exc:
            return jsonify({"error": "Validation failed", "details": exc.errors}), 400
        agent = AgentService.create_agent(AgentModel.from_payload(clean))
        return jsonify({"agent": _serialize_dates(agent)}), 201
    except Exception:
        logger.exception("POST /api/agents failed")
        return jsonify({"error": "Internal server error"}), 500


@app.route("/api/init", methods=["POST"])
def api_init():
    try:
        seed_defaults(get_db())
        return jsonify({"message": "Seed complete"}), 200
    except Exception:
        logger.exception("POST /api/init failed")
        return jsonify({"error": "Seed failed"}), 500


# ---------------------------------------------------------------------------
# Local dev server
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
