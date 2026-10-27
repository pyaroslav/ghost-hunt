"""Read the ledger row the in-doubt ghost holds (lab-se2). Before the exorcism even a SELECT fails."""
import os
import oracledb

c = oracledb.connect(user="lab", password=os.environ["GHOST_LAB_PASSWORD"], dsn="127.0.0.1:15298/ORCLPDB1").cursor()
try:
    c.execute("select amt from gh_ledger where id = 1")
    print(f"gh_ledger id 1: amt = {c.fetchone()[0]}")
except oracledb.DatabaseError as e:
    print(str(e).splitlines()[0])
