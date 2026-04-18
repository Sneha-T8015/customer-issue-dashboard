import streamlit as st
import sqlite3
import pandas as pd

st.set_page_config(page_title="Customer Issue Dashboard", layout="wide")

def init_db():
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

    cursor.execute("SELECT COUNT(*) FROM customers")
    customer_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM tickets")
    ticket_count = cursor.fetchone()[0]

    if customer_count == 0:
        customers = [
            (1, "Rahul Sharma", "rahul@example.com", "ABC Pvt Ltd"),
            (2, "Sneha T", "sneha@example.com", "XYZ Technologies"),
            (3, "Anjali Rao", "anjali@example.com", "TechNova")
        ]
        cursor.executemany("""
            INSERT INTO customers (customer_id, customer_name, email, company)
            VALUES (?, ?, ?, ?)
        """, customers)

    if ticket_count == 0:
        tickets = [
            (101, 1, "Login Issue", "Open", "High", "2026-04-10 10:00:00", None, "Agent1", "Unable to log in"),
            (102, 2, "Payment Failure", "Resolved", "Medium", "2026-04-09 09:00:00", "2026-04-10 11:30:00", "Agent2", "Payment failed during checkout"),
            (103, 3, "Account Locked", "In Progress", "High", "2026-04-12 14:20:00", None, "Agent1", "Account locked after multiple attempts"),
            (104, 1, "Slow Dashboard", "Resolved", "Low", "2026-04-08 08:15:00", "2026-04-08 12:00:00", "Agent3", "Dashboard loading slowly"),
            (105, 2, "Email Notification Error", "Open", "Medium", "2026-04-13 16:40:00", None, "Agent2", "Not receiving ticket updates")
        ]
        cursor.executemany("""
            INSERT INTO tickets (
                ticket_id, customer_id, issue_type, status, priority,
                created_at, resolved_at, assigned_to, description
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, tickets)

    conn.commit()
    conn.close()

def get_data(query, params=None):
    conn = sqlite3.connect("support.db")
    if params:
        df = pd.read_sql_query(query, conn, params=params)
    else:
        df = pd.read_sql_query(query, conn)
    conn.close()
    return df

# IMPORTANT: call this before querying the database
init_db()

st.title("Customer Issue Dashboard")

total_tickets = get_data("SELECT COUNT(*) AS count FROM tickets").iloc[0]["count"]
open_tickets = get_data("SELECT COUNT(*) AS count FROM tickets WHERE status = 'Open'").iloc[0]["count"]
resolved_tickets = get_data("SELECT COUNT(*) AS count FROM tickets WHERE status = 'Resolved'").iloc[0]["count"]
high_priority = get_data("SELECT COUNT(*) AS count FROM tickets WHERE priority = 'High'").iloc[0]["count"]

col1, col2, col3, col4 = st.columns(4)
col1.metric("Total Tickets", total_tickets)
col2.metric("Open Tickets", open_tickets)
col3.metric("Resolved Tickets", resolved_tickets)
col4.metric("High Priority Tickets", high_priority)

st.markdown("---")

status_options = get_data("SELECT DISTINCT status FROM tickets")["status"].dropna().tolist()
priority_options = get_data("SELECT DISTINCT priority FROM tickets")["priority"].dropna().tolist()
agent_options = get_data("SELECT DISTINCT assigned_to FROM tickets")["assigned_to"].dropna().tolist()

colf1, colf2, colf3 = st.columns(3)
selected_status = colf1.selectbox("Filter by Status", ["All"] + status_options)
selected_priority = colf2.selectbox("Filter by Priority", ["All"] + priority_options)
selected_agent = colf3.selectbox("Filter by Assigned Agent", ["All"] + agent_options)

st.subheader("Search Tickets")
search_text = st.text_input("Search by Customer Name, Ticket ID, or Issue Type")

query = """
SELECT
    t.ticket_id,
    c.customer_name,
    t.issue_type,
    t.status,
    t.priority,
    t.created_at,
    t.resolved_at,
    t.assigned_to,
    t.description
FROM tickets t
JOIN customers c ON t.customer_id = c.customer_id
WHERE 1=1
"""

params = []

if selected_status != "All":
    query += " AND t.status = ?"
    params.append(selected_status)

if selected_priority != "All":
    query += " AND t.priority = ?"
    params.append(selected_priority)

if selected_agent != "All":
    query += " AND t.assigned_to = ?"
    params.append(selected_agent)

if search_text:
    query += """
    AND (
        c.customer_name LIKE ?
        OR CAST(t.ticket_id AS TEXT) LIKE ?
        OR t.issue_type LIKE ?
    )
    """
    search_value = f"%{search_text}%"
    params.extend([search_value, search_value, search_value])

query += " ORDER BY t.created_at DESC"

filtered_df = get_data(query, params)
st.subheader("Filtered Tickets")
st.dataframe(filtered_df, use_container_width=True)
