"""Find the ghosts in an Oracle database: things that are still there after whatever owned them is gone.

Every ghost comes from a query or a file listing, never from the model. The hunter account only reads
(CREATE SESSION + SELECT ANY DICTIONARY). Client machine names, OS users and SQL text are never selected.

A ghost the hunter may lay to rest carries two lists: `verify`, queries that must still return a row right
before anything is run (hours may pass between the hunt and the approval), and `exorcise`, the statements.
`requires` names ghosts that must have been laid to rest first in the same run.
"""

import os
import subprocess
from dataclasses import dataclass, field

import oracledb

IDLE_MINUTES = int(os.environ.get("GHOST_IDLE_MINUTES", "10"))


@dataclass
class Ghost:
    id: str            # stable, e.g. "lab-se2/ORCLPDB1/indoubt/10.29.199600"
    kind: str
    db: str
    pdb: str
    what: str          # one plain sentence, built from the query
    since: str = ""
    facts: dict = field(default_factory=dict)
    exorcise: list = field(default_factory=list)  # [(pdb, sql)] run by the exorcist after approval
    verify: list = field(default_factory=list)    # [(pdb, sql, binds)] each must return a row, or nothing is run
    requires: list = field(default_factory=list)  # ids of ghosts that must be laid to rest first, in the same run
    bury: str = ""     # what a person should do when the hunter won't touch it


# Each kind: the query (run inside a PDB as the hunter) and how a row becomes a Ghost.
TXN = "t.xidusn || '.' || t.xidslot || '.' || t.xidsqn"

SESSIONS = f"""
select s.sid, s.serial#, s.username, s.module, s.status, s.last_call_et, {TXN} txn,
       to_char(t.start_date, 'YYYY-MM-DD HH24:MI') started, t.used_urec,
       (select count(*) from v$session w where w.blocking_session = s.sid) blocked
  from v$session s join v$transaction t on t.ses_addr = s.saddr
 where s.type = 'USER' and s.con_id = sys_context('userenv', 'con_id')
   and ((s.status = 'INACTIVE' and s.last_call_et >= :idle) or s.status = 'KILLED')"""

# Right before a kill: the same session, the same transaction, still idle, still blocking someone.
STILL_BLOCKING = f"""
select 1 from v$session s join v$transaction t on t.ses_addr = s.saddr
 where s.sid = :sid and s.serial# = :serial and s.status = 'INACTIVE' and {TXN} = :txn
   and exists (select 1 from v$session w where w.blocking_session = s.sid)"""

INDOUBT = """
select local_tran_id, global_tran_id, state, mixed, to_char(fail_time, 'YYYY-MM-DD HH24:MI') failed
  from dba_2pc_pending"""

STILL_PENDING = "select 1 from dba_2pc_pending where local_tran_id = :tid and state = :state"

RECYCLED = """
select r.owner, r.original_name, r.object_name, r.type, r.droptime, r.space * p.value / 1048576 mb
  from dba_recyclebin r cross join (select to_number(value) value from v$parameter where name = 'db_block_size') p
 where r.can_purge = 'YES' and r.type = 'TABLE'"""

INVALID = """
select o.owner, o.object_name, o.object_type, to_char(o.last_ddl_time, 'YYYY-MM-DD HH24:MI') changed
  from dba_objects o join dba_users u on u.username = o.owner and u.oracle_maintained = 'N'
 where o.status = 'INVALID'"""

# A locked or expired account is often fine (a schema owner locked on purpose). It's a ghost when its
# scheduler jobs still run as it: work going on in the name of someone who is gone.
ORPHANS = """
select u.username, u.account_status, to_char(nvl(u.lock_date, u.expiry_date), 'YYYY-MM-DD') gone,
       (select count(*) from dba_objects o where o.owner = u.username) objects,
       (select count(*) from dba_scheduler_jobs j where j.owner = u.username and j.enabled = 'TRUE') jobs
  from dba_users u
 where u.oracle_maintained = 'N' and u.username not like 'C##%'
   and (u.account_status like '%LOCKED%' or u.account_status in ('EXPIRED', 'EXPIRED & LOCKED'))
   and exists (select 1 from dba_scheduler_jobs j where j.owner = u.username and j.enabled = 'TRUE')"""

KNOWN_FILES = """
select name from v$datafile union all select name from v$tempfile
union all select member from v$logfile union all select name from v$controlfile"""


def connect(target: dict, service: str, who: str = "hunter") -> oracledb.Connection:
    user = {"hunter": "c##ghost", "exorcist": "c##exorcist"}[who]
    var = f"GHOST_{who.upper()}_PASSWORD"
    if not os.environ.get(var):
        raise RuntimeError(f"{var} isn't set (put it in ~/.ghost-hunt.env)")
    return oracledb.connect(user=user, password=os.environ[var], dsn=f"{target['host']}/{service}", tcp_connect_timeout=10)


def rows(conn, sql, **binds) -> list[dict]:
    cur = conn.cursor()
    cur.execute(sql, binds)
    cols = [d[0].lower() for d in cur.description]
    return [dict(zip(cols, r)) for r in cur]


def _id(*parts) -> str:
    return "/".join(str(p) for p in parts)


def sessions(db, pdb, conn) -> list[Ghost]:
    out = []
    for r in rows(conn, SESSIONS, idle=IDLE_MINUTES * 60):
        killed = r["status"] == "KILLED"
        mins = r["last_call_et"] // 60
        kill = f"alter system kill session '{r['sid']},{r['serial#']}' immediate"
        what = (f"Session {r['sid']} ({r['username']}) was killed and is still rolling back {r['used_urec']} undo records"
                if killed else
                f"Session {r['sid']} ({r['username']}) has been idle {mins} min with an open transaction"
                + (f" that {r['blocked']} other session{'s' if r['blocked'] != 1 else ''} {'are' if r['blocked'] != 1 else 'is'} waiting on" if r["blocked"] else ""))
        g = Ghost(_id(db, pdb, "session", f"{r['sid']},{r['serial#']}"), "killed" if killed else "lingering", db, pdb, what,
                  since=r["started"] or "",
                  facts={"sid": r["sid"], "serial": r["serial#"], "user": r["username"], "module": r["module"] or "",
                         "idle_min": mins, "undo_records": r["used_urec"], "blocking": r["blocked"]})
        if killed:
            g.bury = "Nothing to do: it leaves when the rollback ends."
        elif r["blocked"]:
            g.exorcise = [(pdb, kill)]
            g.verify = [(pdb, STILL_BLOCKING, {"sid": r["sid"], "serial": r["serial#"], "txn": r["txn"]})]
        else:
            g.bury = f"It isn't blocking anyone yet. Ask its owner to commit or roll back. SQL: {kill};"
        out.append(g)
    return out


def indoubt(db, pdb, conn) -> list[Ghost]:
    return [Ghost(_id(db, pdb, "indoubt", r["local_tran_id"]), "indoubt", db, pdb,
                  f"Distributed transaction {r['local_tran_id']} is {r['state']} and nobody is coming back for it",
                  since=r["failed"] or "",
                  facts={"local_tran_id": r["local_tran_id"], "global_tran_id": r["global_tran_id"], "state": r["state"],
                         "mixed": r["mixed"]})
            for r in rows(conn, INDOUBT)]


def recycled(db, pdb, conn) -> list[Ghost]:
    return [Ghost(_id(db, pdb, "recyclebin", r["object_name"]), "recyclebin", db, pdb,
                  f"Table {r['owner']}.{r['original_name']} was dropped on {r['droptime'][:10]} and still holds {r['mb']:.1f} MB",
                  since=r["droptime"].replace(":", " ", 1)[:16],
                  facts={"owner": r["owner"], "table": r["original_name"], "mb": round(r["mb"], 1)},
                  bury=f'Purge it if nobody wants it back, or flash it back. SQL: purge table {r["owner"]}."{r["object_name"]}";')
            for r in rows(conn, RECYCLED)]


def compile_sql(kind: str, name: str) -> str:
    if kind.endswith(" BODY"):  # PACKAGE BODY, TYPE BODY
        return f"alter {kind[:-5].lower()} {name} compile body;"
    return f"alter {kind.lower()} {name} compile;"


def invalid(db, pdb, conn) -> list[Ghost]:
    return [Ghost(_id(db, pdb, "invalid", r["object_type"].replace(" ", "_"), f"{r['owner']}.{r['object_name']}"),
                  "invalid", db, pdb,
                  f"{r['object_type'].title()} {r['owner']}.{r['object_name']} is invalid: something it depends on changed or is gone",
                  since=r["changed"] or "",
                  facts={"owner": r["owner"], "object": r["object_name"], "type": r["object_type"]},
                  bury=f"Recompile it; if that fails, it reads something that's gone, and can be dropped. "
                       f"SQL: {compile_sql(r['object_type'], r['owner'] + '.' + r['object_name'])}")
            for r in rows(conn, INVALID)]


def orphans(db, pdb, conn) -> list[Ghost]:
    return [Ghost(_id(db, pdb, "orphan", r["username"]), "orphan", db, pdb,
                  f"User {r['username']} is {r['account_status'].lower()} but {r['jobs']} scheduler "
                  f"job{'s' if r['jobs'] != 1 else ''} still run{'' if r['jobs'] != 1 else 's'} as it",
                  since=r["gone"] or "",
                  facts={"user": r["username"], "status": r["account_status"], "objects": r["objects"], "jobs": r["jobs"]},
                  bury="Disable its jobs, then hand the objects to a new owner or drop the user.")
            for r in rows(conn, ORPHANS)]


def forgotten_files(target: dict, conn) -> list[Ghost]:
    """Database files on disk that the database no longer lists. Only for a target that says how to list its files:
    a container (docker exec) or "files": "local" when the database runs on this machine. Only the CDB's own
    directory is listed, so another database's files on the same host aren't mistaken for ghosts."""
    known = {r["name"] for r in rows(conn, KNOWN_FILES)}
    root = target.get("oradata", f"/opt/oracle/oradata/{target['cdb'].upper()}")
    cmd = ["find", root, "-type", "f", "(", "-name", "*.dbf", "-o", "-name", "*.ctl", "-o", "-name", "*.log", ")",
           "-printf", "%p\t%s\t%TY-%Tm-%Td %TH:%TM\n"]
    if target.get("container"):
        cmd = ["docker", "exec", target["container"], *cmd]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True).stdout
    ghosts = []
    for line in out.splitlines():
        path, size, mtime = line.split("\t")
        if path not in known:
            ghosts.append(Ghost(_id(target["name"], "CDB$ROOT", "file", path), "file", target["name"], "CDB$ROOT",
                                f"{path.rsplit('/', 1)[1]} ({int(size) / 1048576:.0f} MB) is on disk but the database stopped using it",
                                since=mtime, facts={"file": path, "mb": round(int(size) / 1048576), "last_written": mtime},
                                bury="Move it aside, keep it a week, then delete it. Never delete a file the database still lists."))
    return ghosts


PDB_HUNTS = [sessions, indoubt, recycled, invalid, orphans]


def hunt(target: dict) -> tuple[list[Ghost], list[str]]:
    """All ghosts in one CDB: each PDB in target['pdbs'], plus files from the root. Returns (ghosts, notes):
    a note is something that couldn't be checked, which shouldn't sink the rest of the hunt."""
    ghosts, notes = [], []
    with connect(target, target["cdb"]) as root:
        if target.get("container") or target.get("files") == "local":
            try:
                ghosts += forgotten_files(target, root)
            except (subprocess.SubprocessError, OSError) as e:
                notes.append(f"Couldn't list the data files, so forgotten files weren't checked ({type(e).__name__}).")
    for pdb in target["pdbs"]:
        with connect(target, pdb) as conn:
            for h in PDB_HUNTS:
                ghosts += h(target["name"], pdb, conn)
    return ghosts, notes


SETTLED = {"committed": "commit", "forced commit": "commit", "forced rollback": "rollback"}


def pair_indoubt(ghosts: list[Ghost]) -> None:
    """An in-doubt transaction has a partner on the other database. Read both sides to decide the exorcism.

    Only an outcome that is already written somewhere is followed: if a side committed, force the prepared sides
    to commit; if a side was rolled back, force a rollback. Anything else is a guess and is refused: the commit
    point site never sits in 'prepared', so if every visible side is prepared, the side that decided isn't in the
    hunt. A settled side's entry is the written outcome, so it's only purged after its prepared partners have been
    forced, in the same run, and never when it's the only side visible. Mixed outcomes are left to a person."""
    groups: dict[str, list[Ghost]] = {}
    for g in ghosts:
        if g.kind == "indoubt":
            groups.setdefault(g.facts["global_tran_id"], []).append(g)
    for gtid, side in groups.items():
        states = {f"{g.db}/{g.pdb}": g.facts["state"] for g in side}
        outcome = {SETTLED[s] for s in states.values() if s in SETTLED}
        verb = next(iter(outcome)) if len(outcome) == 1 else None
        prepared = [g for g in side if g.facts["state"] == "prepared"]
        for g in side:
            me = f"{g.db}/{g.pdb}"
            g.facts["other_sides"] = {k: s for k, s in states.items() if k != me} or {"unknown": "no record"}
            tid, state = g.facts["local_tran_id"], g.facts["state"]
            purge = (g.pdb, f"begin dbms_transaction.purge_lost_db_entry('{tid}'); commit; end;")
            check = (g.pdb, STILL_PENDING, {"tid": tid, "state": state})
            if str(g.facts.get("mixed", "")).lower() == "yes":
                g.facts["decision"] = "none: Oracle marked the outcome mixed, so the sides disagree"
                g.bury = f"Repair the data by hand, then: exec dbms_transaction.purge_mixed('{tid}')"
            elif len(outcome) > 1:
                g.facts["decision"] = "none: the sides were settled in different directions"
                g.bury = "Compare the data on both sides by hand before touching either entry."
            elif state == "prepared" and verb:
                g.exorcise = [(g.pdb, f"{verb} force '{tid}'"), purge]
                g.verify = [check]
                g.facts["decision"] = f"{verb} force: the other side {'committed' if verb == 'commit' else 'was rolled back'}"
            elif state == "prepared":
                g.facts["decision"] = ("none: the database that decided isn't in the hunt, so there's no way to tell "
                                       "which way it went")
                g.bury = f"Look up {gtid} on the other database, then commit force or rollback force '{tid}' to match it."
            elif state in SETTLED and len(side) > 1:
                g.exorcise, g.verify = [purge], [check]
                g.requires = [p.id for p in prepared]
                g.facts["decision"] = ("purge, after its partner is forced: this side is the written outcome"
                                       if prepared else f"purge: this side is already {state}")
            else:
                g.facts["decision"] = f"none: this side is {state} and its partner isn't in the hunt"
                g.bury = (f"Check that the other database no longer lists {gtid}, then: "
                          f"exec dbms_transaction.purge_lost_db_entry('{tid}')")
