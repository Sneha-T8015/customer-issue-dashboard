import sqlite3

conn = sqlite3.connect("support.db")
cursor = conn.cursor()

print("\n1. Total tickets")
cursor.execute("SELECT COUNT(*) FROM tickets")
print(cursor.fetchone()[0])

print("\n2. Open tickets")
cursor.execute("SELECT COUNT(*) FROM tickets WHERE status = 'Open'")
print(cursor.fetchone()[0])

print("\n3. High priority unresolved tickets")
cursor.execute("""
SELECT COUNT(*) 
FROM tickets 
WHERE priority = 'High' AND status != 'Resolved'
""")
print(cursor.fetchone()[0])

print("\n4. Tickets by status")
cursor.execute("""
SELECT status, COUNT(*) 
FROM tickets
GROUP BY status
""")
for row in cursor.fetchall():
    print(row)

print("\n5. Tickets by customer")
cursor.execute("""
SELECT c.customer_name, COUNT(t.ticket_id) as total_tickets
FROM customers c
JOIN tickets t ON c.customer_id = t.customer_id
GROUP BY c.customer_name
ORDER BY total_tickets DESC
""")


cursor.execute("""
SELECT priority, COUNT(*) 
FROM tickets
GROUP BY priority
""")
print(cursor.fetchall())
for row in cursor.fetchall():
    print(row)

conn.close()