import sqlite3

conn = sqlite3.connect("support.db")
cursor = conn.cursor()

customers = [
    (1, "Rahul Sharma", "rahul@example.com", "ABC Pvt Ltd"),
    (2, "Sneha T", "sneha@example.com", "XYZ Technologies"),
    (3, "Anjali Rao", "anjali@example.com", "TechNova")
]

tickets = [
    (101, 1, "Login Issue", "Open", "High", "2026-04-10 10:00:00", None, "Agent1", "Unable to log in"),
    (102, 2, "Payment Failure", "Resolved", "Medium", "2026-04-09 09:00:00", "2026-04-10 11:30:00", "Agent2", "Payment failed during checkout"),
    (103, 3, "Account Locked", "In Progress", "High", "2026-04-12 14:20:00", None, "Agent1", "Account locked after multiple attempts"),
    (104, 1, "Slow Dashboard", "Resolved", "Low", "2026-04-08 08:15:00", "2026-04-08 12:00:00", "Agent3", "Dashboard loading slowly"),
    (105, 2, "Email Notification Error", "Open", "Medium", "2026-04-13 16:40:00", None, "Agent2", "Not receiving ticket updates")
  
    (101, 1, "Login Issue", "Open", "High", "2026-04-10 10:00:00", None, "Agent1", "Unable to log in"),
    (102, 2, "Payment Failure", "Resolved", "Medium", "2026-04-09 09:00:00", "2026-04-10 11:30:00", "Agent2", "Payment failed during checkout"),
    (106, 3, "Password Reset", "Open", "Low", "2026-04-18 09:15:00", None, "Agent3", "Password reset link not working")
]

cursor.executemany("INSERT OR REPLACE INTO customers VALUES (?, ?, ?, ?)", customers)
cursor.executemany("INSERT OR REPLACE INTO tickets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", tickets)

conn.commit()
conn.close()

print("Sample data inserted successfully.")