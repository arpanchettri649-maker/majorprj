import sqlite3

conn = sqlite3.connect('instance/remedial.db')
conn.row_factory = sqlite3.Row

print("=== Tables ===")
tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
for t in tables:
    print(f"  {t['name']}")

print("\n=== Foreign Keys ===")
for t in tables:
    fks = conn.execute(f"PRAGMA foreign_key_list({t['name']})").fetchall()
    if fks:
        print(f"  {t['name']}:")
        for fk in fks:
            print(f"    {fk['from']} -> {fk['table']}.{fk['to']} (on_delete: {fk['on_delete']})")

conn.close()