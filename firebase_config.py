"""
firebase_config.py
------------------
Firebase Admin SDK initialisation, Firestore client singleton,
and the seed helper that writes default departments + SLA policies
into Firestore on first run.

Firestore collections
---------------------
  departments  – id, name, description
  sla_policies – id, department_id, priority, response_hours, resolution_hours
  tickets      – (managed by services.py)
  agents       – (managed by services.py)
"""

import os
import json
import logging

import firebase_admin
from firebase_admin import credentials, firestore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Firestore client singleton
# ---------------------------------------------------------------------------
_db = None


def init_firebase():
    """
    Initialise the Firebase Admin SDK.

    Credential resolution order (first match wins):
      1. FIREBASE_CREDENTIALS_JSON env var   – inline JSON string
      2. GOOGLE_APPLICATION_CREDENTIALS env var – file path
      3. ./serviceAccountKey.json            – local key file
      4. Application Default Credentials (ADC)

    Returns the Firestore client.
    """
    global _db
    if _db is not None:
        return _db

    if not firebase_admin._apps:
        cred_json = os.environ.get("FIREBASE_CREDENTIALS_JSON")
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
        local_key = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "serviceAccountKey.json",
        )

        try:
            if cred_json:
                cred_dict = json.loads(cred_json)
                cred = credentials.Certificate(cred_dict)
                firebase_admin.initialize_app(cred)
                logger.info("Firebase initialised from FIREBASE_CREDENTIALS_JSON")

            elif cred_path and os.path.isfile(cred_path):
                cred = credentials.Certificate(cred_path)
                firebase_admin.initialize_app(cred)
                logger.info("Firebase initialised from %s", cred_path)

            elif os.path.isfile(local_key):
                cred = credentials.Certificate(local_key)
                firebase_admin.initialize_app(cred)
                logger.info("Firebase initialised from %s", local_key)

            else:
                # Fall back to ADC / emulator
                firebase_admin.initialize_app()
                logger.info("Firebase initialised with default credentials")

        except Exception:
            logger.exception("Failed to initialise Firebase Admin SDK")
            raise

    _db = firestore.client()
    return _db


def get_db():
    """Return the Firestore client, initialising if necessary."""
    if _db is None:
        return init_firebase()
    return _db


# ---------------------------------------------------------------------------
# Collection name constants
# ---------------------------------------------------------------------------
DEPARTMENTS_COLLECTION = "departments"
SLA_POLICIES_COLLECTION = "sla_policies"
TICKETS_COLLECTION = "tickets"
AGENTS_COLLECTION = "agents"

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------

# Default departments seeded on /api/init
DEFAULT_DEPARTMENTS = [
    {
        "id": "IT",
        "name": "IT",
        "description": "Infrastructure, networking, hardware and internal tooling support",
    },
    {
        "id": "software",
        "name": "Software",
        "description": "Software development, bug-fixes and feature requests",
    },
    {
        "id": "billing",
        "name": "Billing",
        "description": "Invoicing, payments, subscriptions and refund queries",
    },
    {
        "id": "learning_kit",
        "name": "Learning Kit",
        "description": "Onboarding kits, training materials and educational content",
    },
]

# Valid priority codes used throughout the system
PRIORITY_CODES = ["P1", "P2", "P3", "P4"]

# Default SLA policies – response_hours / resolution_hours per priority
DEFAULT_SLA_POLICIES = [
    # P1  – Critical  (fastest)
    {"department_id": "IT",          "priority": "P1", "response_hours": 1,  "resolution_hours": 4},
    {"department_id": "IT",          "priority": "P2", "response_hours": 4,  "resolution_hours": 24},
    {"department_id": "IT",          "priority": "P3", "response_hours": 8,  "resolution_hours": 48},
    {"department_id": "IT",          "priority": "P4", "response_hours": 24, "resolution_hours": 72},
    # Software
    {"department_id": "software",    "priority": "P1", "response_hours": 1,  "resolution_hours": 4},
    {"department_id": "software",    "priority": "P2", "response_hours": 4,  "resolution_hours": 24},
    {"department_id": "software",    "priority": "P3", "response_hours": 8,  "resolution_hours": 48},
    {"department_id": "software",    "priority": "P4", "response_hours": 24, "resolution_hours": 72},
    # Billing
    {"department_id": "billing",     "priority": "P1", "response_hours": 1,  "resolution_hours": 4},
    {"department_id": "billing",     "priority": "P2", "response_hours": 4,  "resolution_hours": 24},
    {"department_id": "billing",     "priority": "P3", "response_hours": 8,  "resolution_hours": 48},
    {"department_id": "billing",     "priority": "P4", "response_hours": 24, "resolution_hours": 72},
    # Learning Kit
    {"department_id": "learning_kit","priority": "P1", "response_hours": 1,  "resolution_hours": 4},
    {"department_id": "learning_kit","priority": "P2", "response_hours": 4,  "resolution_hours": 24},
    {"department_id": "learning_kit","priority": "P3", "response_hours": 8,  "resolution_hours": 48},
    {"department_id": "learning_kit","priority": "P4", "response_hours": 24, "resolution_hours": 72},
]

# Status values for tickets
TICKET_STATUSES = ["Open", "In Progress", "Resolved", "Closed"]


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------

def seed_defaults(db):
    """
    Idempotently write the default departments and SLA policies.

    * Departments are written with a *fixed document ID* so re-runs
      will upsert rather than duplicate.
    * SLA policies are matched on (department_id, priority) – existing
      records are left untouched; new ones are inserted.
    """
    # --- Departments -------------------------------------------------------
    dept_count = 0
    for dept in DEFAULT_DEPARTMENTS:
        ref = db.collection(DEPARTMENTS_COLLECTION).document(dept["id"])
        if not ref.get().exists:
            ref.set({
                "name": dept["name"],
                "description": dept["description"],
            })
            dept_count += 1

    if dept_count:
        logger.info("Seeded %d departments", dept_count)
    else:
        logger.info("All %d departments already exist – skipping", len(DEFAULT_DEPARTMENTS))

    # --- SLA policies ------------------------------------------------------
    existing = set()
    for doc in db.collection(SLA_POLICIES_COLLECTION).stream():
        d = doc.to_dict()
        existing.add((d.get("department_id"), d.get("priority")))

    policy_count = 0
    for policy in DEFAULT_SLA_POLICIES:
        key = (policy["department_id"], policy["priority"])
        if key not in existing:
            db.collection(SLA_POLICIES_COLLECTION).add(policy)
            policy_count += 1

    if policy_count:
        logger.info("Seeded %d SLA policies", policy_count)
    else:
        logger.info("All %d SLA policies already exist – skipping", len(DEFAULT_SLA_POLICIES))
