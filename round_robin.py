"""
round_robin.py
--------------
Automated round-robin ticket assignment for agents within a department.

Strategy: **Least-loaded round robin**
  1. Query all *active* agents in the given ``department_id``.
  2. For each agent, count their currently *open* / *in-progress* tickets.
  3. Pick the agent with the **lowest** active-ticket count.
  4. Break ties deterministically by ``agent_id`` (alphabetical sort).

This guarantees an even distribution without needing persistent state
like a rotating pointer.
"""

import logging
from typing import Optional

from firebase_admin import firestore

from firebase_config import get_db, AGENTS_COLLECTION, TICKETS_COLLECTION

logger = logging.getLogger(__name__)


def _count_active_tickets(db, agent_id: str, agent_name: str) -> int:
    """
    Count the number of tickets currently assigned to *agent_id* whose
    status is **Open** or **In Progress**.

    We match on both ``assigned_to`` (agent_id) for robustness – the
    system always stores the agent_id in ``assigned_to``.
    """
    try:
        # Query by assigned_to == agent_id AND status in [Open, In Progress]
        # Note: Firestore `in` filter is limited to 30 values – 2 is fine.
        count = 0
        docs = (
            db.collection(TICKETS_COLLECTION)
            .where("assigned_to", "==", agent_id)
            .where("status", "in", ["Open", "In Progress"])
            .stream()
        )
        for _ in docs:
            count += 1
        return count
    except Exception:
        logger.exception("Error counting active tickets for agent %s", agent_id)
        return 0


def pick_agent(department_id: str) -> Optional[dict]:
    """
    Return the best-fit agent for a new ticket in *department_id*.

    Returns a dict ``{"id": ..., "name": ..., ...}`` or ``None`` when
    no active agents are available for that department.
    """
    try:
        db = get_db()
    except Exception:
        logger.exception("Failed to get Firestore client for round-robin")
        return None

    # 1. Fetch all active agents in this department
    try:
        agents = []
        docs = (
            db.collection(AGENTS_COLLECTION)
            .where("department_id", "==", department_id)
            .where("active", "==", True)
            .stream()
        )
        for doc in docs:
            agent = doc.to_dict()
            agent["id"] = doc.id
            agents.append(agent)
    except Exception:
        logger.exception("Error fetching agents for dept=%s", department_id)
        return None

    if not agents:
        logger.warning("No active agents found for department %s", department_id)
        return None

    # 2. Count active tickets for each agent
    scored: list[tuple[int, str, dict]] = []
    for agent in agents:
        count = _count_active_tickets(db, agent["id"], agent.get("name", ""))
        scored.append((count, agent["id"], agent))

    # 3. Sort by (active_count ASC, agent_id ASC) for deterministic tie-break
    scored.sort(key=lambda x: (x[0], x[1]))

    winner = scored[0]
    logger.info(
        "Round-robin selected agent %s (%s) for dept=%s – active tickets: %d",
        winner[2].get("name"),
        winner[1],
        department_id,
        winner[0],
    )
    return winner[2]
