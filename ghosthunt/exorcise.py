"""Lay one approved ghost to rest, as the exorcist account.

The exorcist has ALTER SYSTEM (to kill a session), FORCE ANY TRANSACTION, SELECT ANY DICTIONARY (for the checks
below), EXECUTE on DBMS_TRANSACTION and DELETE on the three pending-transaction tables purge_lost_db_entry
cleans. ALTER SYSTEM is a broad privilege; keep the account's password out of anyone else's hands.

Only two kinds can be exorcised: a session others are waiting on (kill it) and an in-doubt transaction whose
outcome is written on another database (force it the same way, then purge the entries). Before anything runs,
the ghost's checks are repeated: an approval can arrive hours after the hunt, and by then the session may have
committed and started on something else. If a check no longer matches, nothing is run.
"""

import oracledb

from .hunt import connect

# Errors that mean the ghost is already gone: the step counts as done, not failed.
GONE = ("ORA-00030",  # session does not exist (it ended, or the sid now has another serial#)
        "ORA-02058")  # no prepared transaction found with that id


def exorcise(target: dict, steps: list, checks: list = ()) -> dict:
    """{'done': bool, 'log': [what happened to each step]}. done is False when a check didn't match."""
    for pdb, sql, binds in checks:
        with connect(target, pdb, "exorcist") as conn:
            cur = conn.cursor()
            cur.execute(sql, binds)
            if cur.fetchone() is None:
                return {"done": False, "log": ["not touched: it changed since the hunt, so the approval no longer applies"]}
    log = []
    for pdb, sql in steps:
        with connect(target, pdb, "exorcist") as conn:
            try:
                conn.cursor().execute(sql)
                log.append(f"ok: {sql}")
            except oracledb.DatabaseError as e:
                msg = str(e).splitlines()[0]
                if not msg.startswith(GONE):
                    raise
                log.append(f"already gone: {sql} ({msg})")
    return {"done": True, "log": log}
