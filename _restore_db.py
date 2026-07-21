import psycopg2, csv, sys, io

DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "metaboard"
DB_USER = "postgres"
DB_PASS = "5432"

conn = psycopg2.connect(host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS)
cur = conn.cursor()

with open("executive_reports_schema.sql", "r", encoding="utf-8") as f:
    cur.execute(f.read())
conn.commit()
print("Table created (or already exists)")

with open("executive_reports_backup.csv", "r", encoding="utf-8") as f:
    reader = csv.reader(f)
    headers = next(reader)
    cols_no_id = [h for h in headers if h != "id"]
    id_idx = headers.index("id")
    placeholders = ", ".join(["%s"] * len(cols_no_id))
    insert_sql = f"INSERT INTO executive_reports ({', '.join(cols_no_id)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"

    count = 0
    for row in reader:
        values = [row[headers.index(c)] if row[headers.index(c)] != "" else None for c in cols_no_id]
        cur.execute(insert_sql, values)
        count += 1

conn.commit()
print(f"Restored {count} reports")
conn.close()
