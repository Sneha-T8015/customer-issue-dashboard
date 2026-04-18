import streamlit as st
import sqlite3
import pandas as pd

st.set_page_config(page_title="Customer Issue Dashboard", layout="wide")

def get_data(query, params=None):
    conn = sqlite3.connect("support.db")
    if params:
        df = pd.read_sql_query(query, conn, params=params)
    else:
        df = pd.read_sql_query(query, conn)
    conn.close()
    return df

st.title("Customer Issue Dashboard")

# Top metrics
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

# Filters
st.subheader("Filter Tickets")

status_options = get_data("SELECT DISTINCT status FROM tickets")["status"].dropna().tolist()
priority_options = get_data("SELECT DISTINCT priority FROM tickets")["priority"].dropna().tolist()
agent_options = get_data("SELECT DISTINCT assigned_to FROM tickets")["assigned_to"].dropna().tolist()

colf1, colf2, colf3 = st.columns(3)

selected_status = colf1.selectbox("Filter by Status", ["All"] + status_options)
selected_priority = colf2.selectbox("Filter by Priority", ["All"] + priority_options)
selected_agent = colf3.selectbox("Filter by Assigned Agent", ["All"] + agent_options)

# Search
st.subheader("Search Tickets")
search_text = st.text_input("Search by Customer Name, Ticket ID, or Issue Type")

# Base query with join
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

st.markdown("---")

# Summary tables
st.subheader("Tickets by Status")
status_df = get_data("""
SELECT status, COUNT(*) AS total
FROM tickets
GROUP BY status
""")
st.dataframe(status_df, use_container_width=True)

st.subheader("Tickets by Priority")
priority_df = get_data("""
SELECT priority, COUNT(*) AS total
FROM tickets
GROUP BY priority
""")
st.dataframe(priority_df, use_container_width=True)

st.subheader("Tickets by Agent")
agent_df = get_data("""
SELECT assigned_to, COUNT(*) AS total
FROM tickets
GROUP BY assigned_to
ORDER BY total DESC
""")
st.dataframe(agent_df, use_container_width=True)