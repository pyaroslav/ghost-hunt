from ghosthunt import hunt
from ghosthunt.hunt import Ghost, compile_sql, pair_indoubt
from ghosthunt.narrate import check_epitaph, check_summary, compact, harm
from ghosthunt.trace import scrub, scrub_error


def indoubt(db, tid, state, gtid="SRC.abc.1.2.3", mixed="no"):
    return Ghost(f"{db}/P/indoubt/{tid}", "indoubt", db, "P", "x",
                 facts={"local_tran_id": tid, "global_tran_id": gtid, "state": state, "mixed": mixed})


def test_prepared_side_is_forced_the_way_the_other_side_went():
    a, b = indoubt("se2", "3.4.5", "prepared"), indoubt("ai", "9.9.9", "committed")
    pair_indoubt([a, b])
    assert a.exorcise[0][1] == "commit force '3.4.5'"
    assert "purge_lost_db_entry('3.4.5')" in a.exorcise[1][1]
    assert b.exorcise == [("P", "begin dbms_transaction.purge_lost_db_entry('9.9.9'); commit; end;")]
    assert b.requires == [a.id]               # the written outcome goes only after its partner is forced
    assert a.verify[0][2] == {"tid": "3.4.5", "state": "prepared"}


def test_a_lone_committed_entry_is_never_purged():
    b = indoubt("ai", "9.9.9", "committed")
    pair_indoubt([b])
    assert b.exorcise == [] and "purge_lost_db_entry('9.9.9')" in b.bury


def test_mixed_or_contradictory_outcomes_are_left_to_a_person():
    a, b = indoubt("se2", "3.4.5", "prepared", mixed="yes"), indoubt("ai", "9.9.9", "committed")
    pair_indoubt([a, b])
    assert a.exorcise == [] and "purge_mixed" in a.bury
    a, b, c = indoubt("se2", "3.4.5", "prepared"), indoubt("ai", "9.9.9", "forced commit"), indoubt("x", "1.1.1", "forced rollback")
    pair_indoubt([a, b, c])
    assert a.exorcise == b.exorcise == c.exorcise == []


def test_no_outcome_anywhere_is_never_guessed():
    a, b = indoubt("se2", "3.4.5", "prepared"), indoubt("ai", "9.9.9", "prepared")
    pair_indoubt([a, b])
    assert a.exorcise == [] and b.exorcise == [] and "isn't in the hunt" in a.facts["decision"]


def test_rolled_back_side_means_rollback():
    a, b = indoubt("se2", "3.4.5", "prepared"), indoubt("ai", "9.9.9", "forced rollback")
    pair_indoubt([a, b])
    assert a.exorcise[0][1] == "rollback force '3.4.5'"


def test_one_sided_prepared_transaction_is_never_guessed():
    a = indoubt("se2", "3.4.5", "prepared")
    pair_indoubt([a])
    assert a.exorcise == [] and "other database" in a.bury


class FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        conn = self

        class C:
            description = [(k,) for k in conn._rows[0]] if conn._rows else []

            def execute(self, *a):
                pass

            def __iter__(self):
                return iter([tuple(r.values()) for r in conn._rows])
        return C()


def session_row(blocked):
    return {"SID": 217, "SERIAL#": 5412, "USERNAME": "LAB", "MODULE": "billing", "STATUS": "INACTIVE",
            "LAST_CALL_ET": 900, "TXN": "3.4.5", "STARTED": "2026-10-27 22:37", "USED_UREC": 1, "BLOCKED": blocked}


def test_only_a_session_someone_waits_on_can_be_killed_and_only_if_it_still_is():
    idle = hunt.sessions("db", "P", FakeConn([session_row(0)]))[0]
    assert idle.exorcise == [] and "isn't blocking anyone" in idle.bury
    blocker = hunt.sessions("db", "P", FakeConn([session_row(1)]))[0]
    assert blocker.exorcise == [("P", "alter system kill session '217,5412' immediate")]
    assert blocker.verify[0][2] == {"sid": 217, "serial": 5412, "txn": "3.4.5"}  # same session, same transaction


def test_package_bodies_compile_with_their_own_syntax():
    assert compile_sql("PACKAGE BODY", "LAB.P") == "alter package LAB.P compile body;"
    assert compile_sql("VIEW", "LAB.V") == "alter view LAB.V compile;"


def test_harm_puts_blockers_and_locked_rows_first():
    gs = [{"kind": "recyclebin", "facts": {}}, {"kind": "file", "facts": {}},
          {"kind": "indoubt", "facts": {"state": "prepared"}}, {"kind": "lingering", "facts": {"blocking": 2}}]
    assert [g["kind"] for g in sorted(gs, key=harm)] == ["lingering", "indoubt", "file", "recyclebin"]


GHOST = {"id": "x", "db": "lab-se2", "kind": "recyclebin", "what": "Table LAB.GH_AUDIT_2019 was dropped on 2026-10-27 and still holds 7.0 MB",
         "since": "2026-10-27 21:28", "facts": {"table": "GH_AUDIT_2019", "mb": 7.0}}


def test_epitaph_must_name_its_ghost_and_invent_nothing():
    e = compact([GHOST])["G1"]
    assert check_epitaph("Here lies GH_AUDIT_2019, dropped on 2026-10-27. It still holds 7.0 MB.", e) == []
    assert "doesn't name GH_AUDIT_2019" in check_epitaph("Here lies a table.", e)[0]
    assert "numbers not in the facts: 9" in check_epitaph("Here lies GH_AUDIT_2019, 9 MB.", e)[0]
    assert "names not in the facts: LAB.PAYROLL" in check_epitaph("Here lies GH_AUDIT_2019, once LAB.PAYROLL.", e)[0]


def test_summary_names_every_database_and_no_labels():
    d = compact([GHOST, GHOST | {"id": "y", "db": "lab-26ai"}])
    assert check_summary(["lab-se2 and lab-26ai each keep a dropped table.", "Purge GH_AUDIT_2019."], d) == []
    assert any("lab-26ai" in p for p in check_summary(["lab-se2 keeps a table.", "Purge it."], d))
    assert any("G labels" in p for p in check_summary(["lab-se2 and lab-26ai: G1.", "Purge G2."], d))


def test_sentry_gets_metadata_only():
    ev = {"contexts": {"trace": {"data": {"gen_ai.request.model": "gemma4:26b", "sql": "commit force '1.2.3'"}}},
          "spans": [{"op": "gen_ai.chat", "data": {"gen_ai.usage.input_tokens": 900, "gen_ai.input.messages": "Here lies GH_AUDIT_2019"}},
                    {"op": "subprocess", "description": "docker exec w2-se2 find /opt/oracle/oradata", "data": {}}],
          "server_name": "my-laptop", "request": {"url": "x"}}
    out = scrub(ev)
    assert out["contexts"]["trace"]["data"] == {"gen_ai.request.model": "gemma4:26b"}
    assert out["spans"] == [{"op": "gen_ai.chat", "data": {"gen_ai.usage.input_tokens": 900}}]
    assert "server_name" not in out and "request" not in out
    err = scrub_error({"exception": {"values": [{"value": 'ORA-00942: table or view "LAB"."GH_SECRET" does not exist',
                                                 "stacktrace": {"frames": [{"abs_path": "/home/someone/ghost-hunt/ghosthunt/hunt.py",
                                                                            "filename": "/home/someone/ghost-hunt/ghosthunt/hunt.py",
                                                                            "module": "ghosthunt.hunt", "function": "rows"}]}}]}})
    ex = err["exception"]["values"][0]
    assert ex["value"] == "ORA-00942"
    assert "someone" not in str(ex) and ex["stacktrace"]["frames"][0]["function"] == "rows"


def test_sentry_standard_library_instrumentation_is_off(monkeypatch):
    import sentry_sdk
    from ghosthunt import trace
    monkeypatch.setenv("SENTRY_DSN", "https://key@o0.ingest.sentry.io/0")
    monkeypatch.setattr(trace, "_on", False)
    trace.init()
    try:
        assert "stdlib" not in sentry_sdk.get_client().integrations
    finally:
        sentry_sdk.get_client().close()
        monkeypatch.setattr(trace, "_on", False)
