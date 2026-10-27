"""Plant one of each ghost in the two lab databases, so a hunt has something to find.

    python lab/plant.py            # plant everything
    python lab/plant.py indoubt    # just one kind

Lab only: it disables distributed recovery (so RECO doesn't resolve the in-doubt transaction by itself),
creates and locks a user, and leaves two client processes running (pids in lab/.sessions).
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import oracledb

HERE = Path(__file__).parent
SE2 = {"container": "w2-se2", "sid": "ORCLCDB", "pdb": "ORCLPDB1", "dsn": "127.0.0.1:15298/ORCLPDB1"}
AI = {"container": "w2-awr-probe", "sid": "FREE", "pdb": "FREEPDB1", "dsn": "127.0.0.1:15299/FREEPDB1"}
PW = os.environ["GHOST_LAB_PASSWORD"]


def sysdba(db, sql):
    """Run SQL as SYSDBA inside the container's PDB."""
    script = f"set feed off\nwhenever sqlerror continue\nalter session set container={db['pdb']};\n{sql}\nexit\n"
    out = subprocess.run(["docker", "exec", "-i", "-e", f"ORACLE_SID={db['sid']}", db["container"], "bash", "-lc",
                          "sqlplus -s / as sysdba"], input=script, capture_output=True, text=True).stdout
    return [l for l in out.splitlines() if l.startswith(("ORA-", "SP2-")) and "ORA-00942" not in l and "ORA-01918" not in l]


def lab(db):
    return oracledb.connect(user="lab", password=PW, dsn=db["dsn"])


def run(c, *stmts):
    cur = c.cursor()
    for s in stmts:
        try:
            cur.execute(s)
        except oracledb.DatabaseError as e:
            if not any(code in str(e) for code in ("ORA-00942", "ORA-02024", "ORA-00955")):
                raise


def indoubt():
    """A transfer between the databases that crashes after prepare: committed on lab-26ai, prepared on lab-se2."""
    for db in (SE2, AI):
        subprocess.run(["docker", "exec", "-i", "-e", f"ORACLE_SID={db['sid']}", db["container"], "bash", "-lc",
                        "sqlplus -s / as sysdba"], input="alter system disable distributed recovery;\n", capture_output=True, text=True)
    ai_ip = subprocess.run(["docker", "inspect", "-f", "{{.NetworkSettings.Networks.bridge.IPAddress}}", AI["container"]],
                           capture_output=True, text=True).stdout.strip()
    with lab(AI) as c:
        run(c, "create table gh_ledger (id number primary key, amt number)")
        run(c, "merge into gh_ledger g using (select 1 id from dual) s on (g.id = s.id) when not matched then insert values (1, 100)", "commit")
    with lab(SE2) as c:
        run(c, "create table gh_ledger (id number primary key, amt number)")
        run(c, "merge into gh_ledger g using (select 1 id from dual) s on (g.id = s.id) when not matched then insert values (1, 100)", "commit")
        run(c, "drop database link ledger_26ai",
            f"create database link ledger_26ai connect to lab identified by \"{PW}\" using '{ai_ip}:1521/FREEPDB1'")
        cur = c.cursor()
        cur.execute("update gh_ledger set amt = amt - 40 where id = 1")
        cur.execute("update gh_ledger@ledger_26ai set amt = amt + 40 where id = 1")
        try:
            cur.execute("commit comment 'ORA-2PC-CRASH-TEST-7'")  # Oracle's own test hook: the remote side dies after prepare
        except oracledb.DatabaseError as e:
            print("  ", str(e).splitlines()[0])


def lingering():
    """An idle session that updated a row and never committed, and a second session stuck behind it."""
    pids = []
    for role in ("holder", "waiter"):
        p = subprocess.Popen([sys.executable, str(HERE / "_session.py"), AI["dsn"], role],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        pids.append(p.pid)
        time.sleep(3)
    (HERE / ".sessions").write_text(" ".join(map(str, pids)))
    print("   sessions pids", pids)


def recycled():
    with lab(SE2) as c:
        run(c, "create table gh_audit_2019 as select level id, rpad('x', 900, 'x') pad from dual connect by level <= 6000",
            "drop table gh_audit_2019")


def invalid():
    sysdba(SE2, "grant create view to lab;")
    with lab(SE2) as c:
        run(c, "create table gh_rates (ccy varchar2(3), rate number)",
            "create or replace view gh_rates_today as select ccy, rate from gh_rates",
            "drop table gh_rates purge")


def orphan():
    """A contractor's account, locked when they left; their nightly job is still enabled."""
    print("  ", sysdba(SE2, f"""
create user gh_contractor identified by "{PW}x" quota 10m on users;
grant create session, create table, create job to gh_contractor;
create table gh_contractor.export_queue (id number, payload varchar2(100));
begin dbms_scheduler.create_job(job_name => 'GH_CONTRACTOR.NIGHTLY_EXPORT', job_type => 'PLSQL_BLOCK',
  job_action => 'begin null; end;', repeat_interval => 'FREQ=DAILY;BYHOUR=2', enabled => true); end;
/
alter user gh_contractor account lock;"""))


KINDS = {"indoubt": indoubt, "lingering": lingering, "recyclebin": recycled, "invalid": invalid, "orphan": orphan}

if __name__ == "__main__":
    for name in sys.argv[1:] or KINDS:
        print("planting", name)
        KINDS[name]()
