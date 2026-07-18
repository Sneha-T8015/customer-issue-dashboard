"""
auth.py
-------
Flask-Login integration, User model, role-based access decorators.

The application uses two roles:
  - **admin**  – full access to all tickets, agent management, analytics
  - **agent**  – can view/update only tickets assigned to them

User credentials are stored in the Firestore ``agents`` collection.
The ``role`` field defaults to ``"agent"``; the first agent seeded via
``ADMIN_EMAIL`` / ``ADMIN_PASSWORD`` env vars receives ``"admin"``.
"""

import logging
from functools import wraps

import bcrypt
from flask import redirect, url_for, flash, request, session
from flask_login import (
    LoginManager,
    UserMixin,
    login_user,
    logout_user,
    login_required as _flask_login_required,
    current_user,
)
from validators import _EMAIL_RE

logger = logging.getLogger(__name__)

login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please log in to continue."
login_manager.login_message_category = "warning"


# ---------------------------------------------------------------------------
# User class (backed by the Firestore ``agents`` collection)
# ---------------------------------------------------------------------------

class User(UserMixin):
    """Lightweight user object stored in the Flask session."""

    def __init__(self, user_id, email, name, role, agent_id=None):
        self.id = user_id
        self.email = email
        self.name = name
        self.role = role
        self.agent_id = agent_id  # references agents collection doc ID

    @property
    def is_admin(self):
        return self.role == "admin"

    @property
    def is_agent(self):
        return self.role == "agent"


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Return a bcrypt hash of *plain*."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def check_password(plain: str, hashed: str) -> bool:
    """Check *plain* against a bcrypt *hashed* value."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Firestore user helpers
# ---------------------------------------------------------------------------

def _get_agents_collection():
    from firebase_config import get_db
    return get_db().collection("agents")


def find_user_by_email(email: str):
    """Look up an agent by email and return a ``User`` or ``None``."""
    email = (email or "").strip().lower()
    if not email:
        return None
    docs = (
        _get_agents_collection()
        .where("email", "==", email)
        .limit(1)
        .stream()
    )
    for doc in docs:
        data = doc.to_dict()
        return User(
            user_id=doc.id,
            email=data.get("email", ""),
            name=data.get("name", ""),
            role=data.get("role", "agent"),
            agent_id=doc.id,
        )
    return None


def find_user_by_id(user_id: str):
    """Fetch an agent doc by ID and return a ``User`` or ``None``."""
    from models import doc_to_dict
    doc = _get_agents_collection().document(user_id).get()
    data = doc.to_dict() if doc.exists else None
    if not data:
        return None
    return User(
        user_id=doc.id,
        email=data.get("email", ""),
        name=data.get("name", ""),
        role=data.get("role", "agent"),
        agent_id=doc.id,
    )


def create_user(email: str, password: str, name: str,
                department_id: str = "IT", role: str = "agent"):
    """Create a new agent/user in Firestore.  Returns the new User object."""
    from models import utcnow
    db = _get_agents_collection()
    user_data = {
        "email": email.strip().lower(),
        "name": name.strip(),
        "department_id": department_id.strip(),
        "password_hash": hash_password(password),
        "role": role,
        "active": True,
        "created_at": utcnow(),
    }
    ref = db.add(user_data)
    user_id = ref[1].id
    logger.info("User created: %s (role=%s)", email, role)
    return User(
        user_id=user_id,
        email=email,
        name=name,
        role=role,
        agent_id=user_id,
    )


def set_password(user_id: str, new_password: str):
    """Update the password hash for an existing user."""
    _get_agents_collection().document(user_id).update({
        "password_hash": hash_password(new_password),
    })


def authenticate_user(email: str, password: str):
    """Verify credentials and return a ``User`` or ``None``."""
    user = find_user_by_email(email)
    if user is None:
        return None
    doc = _get_agents_collection().document(user.id).get()
    data = doc.to_dict() if doc.exists else None
    if not data:
        return None
    hashed = data.get("password_hash", "")
    if not hashed or not check_password(password, hashed):
        return None
    if not data.get("active", True):
        return None
    return user


# ---------------------------------------------------------------------------
# Flask-Login user loader
# ---------------------------------------------------------------------------

@login_manager.user_loader
def _load_user(user_id):
    return find_user_by_id(user_id)


# ---------------------------------------------------------------------------
# Role-based access decorators
# ---------------------------------------------------------------------------

def login_required(f):
    """Decorator that enforces authentication (any role)."""
    @_flask_login_required
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated


def admin_required(f):
    """Decorator that enforces admin role."""
    @_flask_login_required
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated


def agent_or_admin(f):
    """Decorator that allows both admin and agent roles (agents may see
    limited data via the RBAC logic in app.py)."""
    @_flask_login_required
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    decorated.__name__ = f.__name__
    return decorated


# ---------------------------------------------------------------------------
# Auth blueprint-style route registrars (called from app.py)
# ---------------------------------------------------------------------------

def _email_taken(email: str) -> bool:
    """Return True if an agent with *email* already exists."""
    email = (email or "").strip().lower()
    if not email:
        return False
    docs = (
        _get_agents_collection()
        .where("email", "==", email)
        .limit(1)
        .stream()
    )
    for _ in docs:
        return True
    return False


def register_auth_routes(app):
    """Register login / signup / logout routes on the Flask app."""

    from flask import render_template

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        if request.method == "POST":
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            remember = bool(request.form.get("remember"))

            user = authenticate_user(email, password)
            if user is not None:
                login_user(user, remember=remember)
                flash(f"Welcome back, {user.name}!", "success")
                next_url = request.args.get("next") or url_for("index")
                return redirect(next_url)
            flash("Invalid email or password.", "danger")

        return render_template("login.html")

    @app.route("/signup", methods=["GET", "POST"])
    def signup():
        if current_user.is_authenticated:
            return redirect(url_for("index"))

        from firebase_config import DEFAULT_DEPARTMENTS

        if request.method == "POST":
            name = (request.form.get("name") or "").strip()
            email = (request.form.get("email") or "").strip().lower()
            password = request.form.get("password") or ""
            confirm = request.form.get("confirm_password") or ""
            department_id = (request.form.get("department_id") or "IT").strip()

            # ---- validation ----
            errors = {}
            if not name:
                errors["name"] = "Name is required"
            if not email:
                errors["email"] = "Email is required"
            elif not _EMAIL_RE.match(email):
                errors["email"] = "Enter a valid email address"
            elif _email_taken(email):
                errors["email"] = "An account with this email already exists"
            if not password:
                errors["password"] = "Password is required"
            elif len(password) < 6:
                errors["password"] = "Password must be at least 6 characters"
            if password != confirm:
                errors["confirm_password"] = "Passwords do not match"

            if errors:
                for field, msg in errors.items():
                    flash(f"{field.replace('_', ' ').title()}: {msg}", "danger")
                return render_template(
                    "signup.html",
                    name=name,
                    email=email,
                    departments=[d["id"] for d in DEFAULT_DEPARTMENTS],
                    selected_dept=department_id,
                )

            # ---- create user ----
            user = create_user(
                email=email,
                password=password,
                name=name,
                department_id=department_id,
                role="agent",
            )
            login_user(user)
            flash("Account created! Welcome to Helpdesk.", "success")
            return redirect(url_for("index"))

        return render_template(
            "signup.html",
            name="",
            email="",
            departments=[d["id"] for d in DEFAULT_DEPARTMENTS],
            selected_dept="IT",
        )

    @app.route("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been logged out.", "info")
        return redirect(url_for("login"))

    @app.route("/change-password", methods=["GET", "POST"])
    @login_required
    def change_password():
        if request.method == "POST":
            current_password = request.form.get("current_password") or ""
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            # Verify current password
            doc = _get_agents_collection().document(current_user.id).get()
            data = doc.to_dict() if doc.exists else None
            if not data or not check_password(current_password, data.get("password_hash", "")):
                flash("Current password is incorrect.", "danger")
                return render_template("change_password.html")

            if len(new_password) < 6:
                flash("New password must be at least 6 characters.", "danger")
                return render_template("change_password.html")

            if new_password != confirm_password:
                flash("New passwords do not match.", "danger")
                return render_template("change_password.html")

            set_password(current_user.id, new_password)
            flash("Password changed successfully.", "success")
            return redirect(url_for("index"))

        return render_template("change_password.html")


def seed_admin_user(db):
    """Ensure the default admin user exists (idempotent).

    Reads ADMIN_EMAIL and ADMIN_PASSWORD from environment variables.
    If the agent with that email already exists and has a ``password_hash``,
    the seed is skipped.  Otherwise the agent is created (or updated) with
    admin role and the specified password.
    """
    import os
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@helpdesk.com").strip().lower()
    admin_password = os.environ.get("ADMIN_PASSWORD", "admin123").strip()

    existing = None
    docs = db.collection("agents").where("email", "==", admin_email).limit(1).stream()
    for doc in docs:
        existing = (doc.id, doc.to_dict())

    if existing:
        agent_id, data = existing
        if data.get("password_hash"):
            logger.info("Admin user %s already exists – skipping seed", admin_email)
            return
        # Existing agent without password – upgrade to admin
        db.collection("agents").document(agent_id).update({
            "password_hash": hash_password(admin_password),
            "role": "admin",
        })
        logger.info("Updated existing agent %s with admin role and password", admin_email)
    else:
        create_user(
            email=admin_email,
            password=admin_password,
            name="Admin",
            department_id="IT",
            role="admin",
        )
        logger.info("Seeded admin user: %s", admin_email)
