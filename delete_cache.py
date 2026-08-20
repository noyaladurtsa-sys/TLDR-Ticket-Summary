import psycopg2
conn = psycopg2.connect(dbname='metaboard', user='postgres', password='5432', host='localhost', port='5432')
cur = conn.cursor()
cur.execute("DELETE FROM executive_reports WHERE ticket_id = '127492'")
conn.commit()
print(f'Deleted {cur.rowcount} row(s)')
conn.close()
