import os
import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app import _ticket_to_issue
from auth import seed_admin_user


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


class AdminSeedTests(unittest.TestCase):
    def test_seed_admin_user_updates_existing_admin_with_current_env_credentials(self):
        existing_doc = Mock()
        existing_doc.id = "agent-1"
        existing_doc.to_dict.return_value = {
            "email": "old@example.com",
            "name": "Old Admin",
            "department_id": "IT",
            "role": "agent",
        }

        fake_agents_collection = Mock()
        fake_agents_collection.where.return_value.limit.return_value.stream.return_value = [existing_doc]

        fake_db = Mock()
        fake_db.collection.return_value = fake_agents_collection

        fake_doc_ref = Mock()
        fake_db.collection.return_value.document.return_value = fake_doc_ref

        with patch.dict(os.environ, {"ADMIN_EMAIL": "admin@helpdesk.com", "ADMIN_PASSWORD": "admin123"}, clear=False):
            with patch("auth.hash_password", return_value="hashed-password") as mock_hash, patch("auth.create_user") as mock_create:
                seed_admin_user(fake_db)

        mock_hash.assert_called_once_with("admin123")
        fake_doc_ref.update.assert_called_once_with({
            "password_hash": "hashed-password",
            "role": "admin",
            "name": "Admin",
            "department_id": "IT",
            "email": "admin@helpdesk.com",
        })
        mock_create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
