from ghosthunt.hunt import Ghost, pair_indoubt
from ghosthunt.narrate import check_epitaph, check_summary, compact, harm
from ghosthunt.trace import scrub, scrub_error


def indoubt(db, tid, state, gtid="SRC.abc.1.2.3"):
    return Ghost(f"{db}/P/indoubt/{tid}", "indoubt", db, "P", "x",
                 facts={"local_tran_id": tid, "global_tran_id": gtid, "state": state})


def test_prepared_side_is_forced_the_way_the_other_side_went():
    a, b = indoubt("se2", "3.4.5", "prepared"), indoubt("ai", "9.9.9", "committed")
    pair_indoubt([a, b])
    assert a.exorcise[0][1] == "commit force '3.4.5'"
    assert "purge_lost_db_entry('3.4.5')" in a.exorcise[1][1]
    assert b.exorcise == [("P", "begin dbms_transaction.purge_lost_db_entry('9.9.9'); commit; end;")]


def test_no_commit_anywhere_means_rollback():
    a, b = indoubt("se2", "3.4.5", "prepared"), indoubt("ai", "9.9.9", "prepared")
    pair_indoubt([a, b])
    assert a.exorcise[0][1] == "rollback force '3.4.5'"


def test_one_sided_prepared_transaction_is_never_guessed():
    a = indoubt("se2", "3.4.5", "prepared")
    pair_indoubt([a])
    assert a.exorcise == [] and "other database" in a.bury


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
    err = scrub_error({"exception": {"values": [{"value": 'ORA-00942: table or view "LAB"."GH_SECRET" does not exist'}]}})
    assert err["exception"]["values"][0]["value"] == "ORA-00942"
