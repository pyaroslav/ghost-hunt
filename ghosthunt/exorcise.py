"""Lay one approved ghost to rest, as the exorcist account (ALTER SYSTEM + FORCE ANY TRANSACTION, nothing else).

Only two kinds can be exorcised: a lingering session (kill it) and an in-doubt transaction (force it the way
the other database went, then purge the entry). Everything else is reported with the SQL for a person to run.
"""

import oracledb

from .hunt import connect

# Errors that mean the ghost is already gone: the exorcism counts as done, not failed.
GONE = ("ORA-00030",  # session does not exist
        "ORA-02058",  # no prepared transaction found with that id
        "ORA-06512")  # raised from purge_lost_db_entry after the entry is gone; checked together with the hunt afterwards


def exorcise(target: dict, steps: list[list[str]]) -> list[str]:
    """Run the ghost's steps in order; return what happened to each."""
    done = []
    for pdb, sql in steps:
        with connect(target, pdb, "exorcist") as conn:
            try:
                conn.cursor().execute(sql)
                done.append(f"ok: {sql}")
            except oracledb.DatabaseError as e:
                msg = str(e).splitlines()[0]
                if msg.startswith(GONE[:2]):
                    done.append(f"already gone: {sql} ({msg})")
                else:
                    raise
    return done
