import psycopg2, csv, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

conn = psycopg2.connect(host="localhost", port=5432, dbname="metaboard", user="postgres", password="5432")
cur = conn.cursor()

cur.execute("SELECT * FROM executive_reports ORDER BY saved_at")
cols = [d[0] for d in cur.description]
rows = cur.fetchall()

with open("executive_reports_backup.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(cols)
    w.writerows(rows)
print(f"Exported {len(rows)} reports to executive_reports_backup.csv")

cur.execute(
    "SELECT column_name, data_type, character_maximum_length "
    "FROM information_schema.columns "
    "WHERE table_name='executive_reports' ORDER BY ordinal_position"
)
schema = cur.fetchall()

with open("executive_reports_schema.sql", "w", encoding="utf-8") as f:
    f.write("CREATE TABLE IF NOT EXISTS executive_reports (\n")
    lines = []
    for col_name, col_type, max_len in schema:
        if col_name == "id":
            lines.append("    id SERIAL PRIMARY KEY")
        elif col_type == "integer":
            lines.append(f"    {col_name} INTEGER DEFAULT 0")
        elif col_type == "timestamp with time zone":
            lines.append(f"    {col_name} TIMESTAMPTZ DEFAULT NOW()")
        else:
            lines.append(f"    {col_name} TEXT")
    f.write(",\n".join(lines))
    f.write("\n);\n")
print("Schema saved to executive_reports_schema.sql")

conn.close()
