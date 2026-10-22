"""Copy the data in the old SQLite file (todo.db) into a PostgreSQL database.

1. Point DATABASE_URL at the empty PostgreSQL database and run:  flask --app server db upgrade
2. Then run:  python scripts/sqlite_to_postgres.py [path/to/todo.db]
It is optional: only needed if you want to keep accounts and tasks you made on SQLite.
"""
import os
import sqlite3
import sys

from sqlalchemy import Boolean, create_engine, inspect, text

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

url = os.environ.get("DATABASE_URL", "")
if not url.startswith(("postgres://", "postgresql://")):
    sys.exit("Set DATABASE_URL to your PostgreSQL connection string first.")
url = url.replace("postgres://", "postgresql://", 1)
src_path = sys.argv[1] if len(sys.argv) > 1 else "todo.db"
if not os.path.exists(src_path):
    sys.exit(f"Cannot find {src_path}")

src = sqlite3.connect(src_path)
src.row_factory = sqlite3.Row
engine = create_engine(url)
insp = inspect(engine)

# parents before children so foreign keys are satisfied
for table in ["user", "task", "subtask", "timetable_entry", "note"]:
    if not insp.has_table(table):
        continue
    try:
        rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
    except sqlite3.OperationalError:
        continue
    if not rows:
        continue
    target_cols = {c["name"]: c for c in insp.get_columns(table)}
    cols = [c for c in rows[0].keys() if c in target_cols]
    bools = {c for c in cols if isinstance(target_cols[c]["type"], Boolean)}
    sql = text(f'INSERT INTO "{table}" ({", ".join(chr(34) + c + chr(34) for c in cols)}) '
               f'VALUES ({", ".join(":" + c for c in cols)})')
    with engine.begin() as conn:
        for row in rows:
            conn.execute(sql, {c: (None if row[c] is None else bool(row[c]) if c in bools else row[c]) for c in cols})
        if "id" in cols:  # keep the auto-increment counter ahead of the copied ids
            conn.execute(text(f"SELECT setval(pg_get_serial_sequence('\"{table}\"', 'id'), "
                              f"COALESCE((SELECT MAX(id) FROM \"{table}\"), 1))"))
    print(f"copied {len(rows)} row(s) into {table}")
print("done")
