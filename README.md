# Customer Issue Dashboard

A support-focused dashboard built using Python, SQLite, and Streamlit to track customer issues, ticket status, priority, and resolution trends.

## Features
- View total, open, resolved, and high-priority tickets
- Filter by status, priority, and assigned agent
- Search by customer name, ticket ID, and issue type
- Analyze support data using SQL queries

## Tech Stack
- Python
- SQLite
- Streamlit
- Pandas

## Run Locally
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python3 db_setup.py
python3 insert_data.py
streamlit run app.py
