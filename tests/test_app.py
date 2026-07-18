import unittest
from datetime import datetime, timezone

from app import _ticket_to_issue


class TicketTransformTests(unittest.TestCase):
    def test_ticket_to_issue_handles_missing_values(self):
        ticket = {
            "id": "abc123",
            "subject": "Printer issue",
            "customer_email": "user@example.com",
            "department_id": "IT",
            "priority": "P2",
            "status": "Open",
            "assigned_name": "Ada",
            "created_at": None,
            "updated_at": None,
        }
        issue = _ticket_to_issue(ticket)
        self.assertEqual(issue["title"], "Printer issue")
        self.assertEqual(issue["customer_name"], "user")
        self.assertEqual(issue["category"], "IT")
        self.assertEqual(issue["assigned_to"], "Ada")

    def test_ticket_to_issue_converts_datetime_values_to_strings(self):
        created_at = datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)
        ticket = {
            "id": "abc123",
            "subject": "Printer issue",
            "customer_email": "user@example.com",
            "department_id": "IT",
            "priority": "P2",
            "status": "Open",
            "assigned_name": "Ada",
            "created_at": created_at,
            "updated_at": created_at,
        }
        issue = _ticket_to_issue(ticket)
        self.assertIsInstance(issue["created_at"], str)
        self.assertEqual(issue["created_at"], created_at.isoformat())
        self.assertEqual(issue["updated_at"], created_at.isoformat())


if __name__ == "__main__":
    unittest.main()
