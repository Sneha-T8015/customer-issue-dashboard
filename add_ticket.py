import sqlite3

conn = sqlite3.connect("support.db")
cursor = conn.cursor()

cursor.execute("""
INSERT INTO tickets (
    ticket_id, customer_id, issue_type, status, priority,
    created_at, resolved_at, assigned_to, description
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
""", (
    107, 1, "App Crash", "Open", "High",
    "2026-04-19 11:00:00", None, "Agent1", "Application crashes on login"
))

conn.commit()
conn.close()

print("New ticket added successfully.")