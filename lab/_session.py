"""One lab client for lab/plant.py: 'holder' locks a row and goes quiet; 'waiter' tries to update it and waits."""

import os
import sys
import time

import oracledb

dsn, role = sys.argv[1], sys.argv[2]
c = oracledb.connect(user="lab", password=os.environ["GHOST_LAB_PASSWORD"], dsn=dsn)
cur = c.cursor()
cur.execute("begin dbms_application_info.set_module(:m, null); end;", m=f"billing-{role}")
if role == "holder":
    cur.execute("update gh_ledger set amt = amt where id = 1")  # never committed
    time.sleep(10**7)
else:
    cur.execute("update gh_ledger set amt = amt + 0 where id = 1")  # blocks until the holder goes away
    c.rollback()
