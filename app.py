from datetime import datetime, date, timezone, timedelta
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-fallback-key-change-me")

database_url = os.environ.get("DATABASE_URL", "sqlite:///issues.db")
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql+psycopg2://", 1)

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db = SQLAlchemy(app)


class Issue(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False)
    customer_name = db.Column(db.String(100), nullable=False)
    customer_email = db.Column(db.String(150), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="Open")
    priority = db.Column(db.String(10), nullable=False, default="Medium")
    category = db.Column(db.String(50), nullable=False, default="General")
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    resolution_notes = db.Column(db.Text, default="")

    STATUSES = ["Open", "In Progress", "Resolved", "Closed"]
    PRIORITIES = ["Low", "Medium", "High", "Critical"]
    CATEGORIES = [
        "General", "Billing", "Technical", "Bug Report",
        "Feature Request", "Account", "Shipping", "Other"
    ]


@app.context_processor
def inject_now():
    return {"now": datetime.now(timezone.utc)}


@app.route("/")
def index():
    status_filter = request.args.get("status", "")
    priority_filter = request.args.get("priority", "")
    category_filter = request.args.get("category", "")
    search = request.args.get("search", "").strip()

    query = Issue.query

    if status_filter:
        query = query.filter(Issue.status == status_filter)
    if priority_filter:
        query = query.filter(Issue.priority == priority_filter)
    if category_filter:
        query = query.filter(Issue.category == category_filter)
    if search:
        query = query.filter(
            db.or_(
                Issue.title.ilike(f"%{search}%"),
                Issue.description.ilike(f"%{search}%"),
                Issue.customer_name.ilike(f"%{search}%"),
                Issue.customer_email.ilike(f"%{search}%"),
            )
        )

    issues = query.order_by(Issue.created_at.desc()).all()

    total = Issue.query.count()
    open_count = Issue.query.filter_by(status="Open").count()
    in_progress_count = Issue.query.filter_by(status="In Progress").count()
    resolved_count = Issue.query.filter_by(status="Resolved").count()
    closed_count = Issue.query.filter_by(status="Closed").count()

    stats = {
        "total": total,
        "open": open_count,
        "in_progress": in_progress_count,
        "resolved": resolved_count,
        "closed": closed_count,
    }

    return render_template(
        "index.html",
        issues=issues,
        stats=stats,
        status_filter=status_filter,
        priority_filter=priority_filter,
        category_filter=category_filter,
        search=search,
        statuses=Issue.STATUSES,
        priorities=Issue.PRIORITIES,
        categories=Issue.CATEGORIES,
    )


@app.route("/issue/new", methods=["GET", "POST"])
def create_issue():
    if request.method == "POST":
        issue = Issue(
            title=request.form["title"],
            description=request.form["description"],
            customer_name=request.form["customer_name"],
            customer_email=request.form["customer_email"],
            status=request.form.get("status", "Open"),
            priority=request.form.get("priority", "Medium"),
            category=request.form.get("category", "General"),
            resolution_notes=request.form.get("resolution_notes", ""),
        )
        db.session.add(issue)
        db.session.commit()
        flash("Issue created successfully.", "success")
        return redirect(url_for("issue_detail", issue_id=issue.id))

    return render_template(
        "issue_form.html",
        issue=None,
        statuses=Issue.STATUSES,
        priorities=Issue.PRIORITIES,
        categories=Issue.CATEGORIES,
    )


@app.route("/issue/<int:issue_id>")
def issue_detail(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    return render_template("issue_detail.html", issue=issue)


@app.route("/issue/<int:issue_id>/edit", methods=["GET", "POST"])
def edit_issue(issue_id):
    issue = Issue.query.get_or_404(issue_id)

    if request.method == "POST":
        issue.title = request.form["title"]
        issue.description = request.form["description"]
        issue.customer_name = request.form["customer_name"]
        issue.customer_email = request.form["customer_email"]
        issue.status = request.form["status"]
        issue.priority = request.form["priority"]
        issue.category = request.form["category"]
        issue.resolution_notes = request.form.get("resolution_notes", "")
        issue.updated_at = datetime.now(timezone.utc)
        db.session.commit()
        flash("Issue updated successfully.", "success")
        return redirect(url_for("issue_detail", issue_id=issue.id))

    return render_template(
        "issue_form.html",
        issue=issue,
        statuses=Issue.STATUSES,
        priorities=Issue.PRIORITIES,
        categories=Issue.CATEGORIES,
    )


@app.route("/issue/<int:issue_id>/delete", methods=["POST"])
def delete_issue(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    db.session.delete(issue)
    db.session.commit()
    flash("Issue deleted.", "danger")
    return redirect(url_for("index"))


@app.route("/analytics")
def analytics():
    total = Issue.query.count()
    by_status = [list(r) for r in db.session.query(Issue.status, db.func.count(Issue.id)).group_by(Issue.status).all()]
    by_priority = [list(r) for r in db.session.query(Issue.priority, db.func.count(Issue.id)).group_by(Issue.priority).all()]
    by_category = [list(r) for r in db.session.query(Issue.category, db.func.count(Issue.id)).group_by(Issue.category).all()]

    today = date.today()
    last_30_days = Issue.query.filter(Issue.created_at >= datetime.now(timezone.utc) - timedelta(days=30)).count()
    last_7_days = Issue.query.filter(Issue.created_at >= datetime.now(timezone.utc) - timedelta(days=7)).count()

    return render_template(
        "analytics.html",
        total=total,
        by_status=by_status,
        by_priority=by_priority,
        by_category=by_category,
        last_30_days=last_30_days,
        last_7_days=last_7_days,
    )


@app.route("/api/issues")
def api_issues():
    issues = Issue.query.order_by(Issue.created_at.desc()).all()
    return jsonify([{
        "id": i.id,
        "title": i.title,
        "customer_name": i.customer_name,
        "status": i.status,
        "priority": i.priority,
        "category": i.category,
        "created_at": i.created_at.isoformat(),
    } for i in issues])


with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
