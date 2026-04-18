import sqlite3

conn = sqlite3.connect("support.db")
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY,
    customer_name TEXT NOT NULL,
    email TEXT,
    company TEXT
)
""")

cursor.execute("""
CREATE TABLE IF NOT EXISTS tickets (
    ticket_id INTEGER PRIMARY KEY,
    customer_id INTEGER,
    issue_type TEXT,
    status TEXT,
    priority TEXT,
    created_at TEXT,
    resolved_at TEXT,
    assigned_to TEXT,
    description TEXT,
    FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
)
""")

conn.commit()
conn.close()

print("Database and tables created successfully.")