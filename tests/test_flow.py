"""The workflow's promises, on Temporal's time-skipping test server with stand-in activities:
nothing is exorcised without approval, only what was approved is touched, a ghost that changed since the hunt is
left alone, the written outcome of an in-doubt transaction is never purged before its partner is forced, a dead
database becomes a line in the report, a missing model doesn't stop the report, and a hunt nobody answers ends
quietly after the wait."""

import re
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ghosthunt.flow import QUEUE, Approval, DbIn, DeliverIn, ExorciseIn, GhostHunt, HuntIn, StoryIn, examine

TOUCHED: list[str] = []
REPORTS: list[str] = []
HAUNTED: dict[str, list[str]] = {}
CHANGED: set[str] = set()     # ghosts whose check no longer matches at exorcism time
GONE_AFTER: set[str] = set()  # databases that can't be reached once the exorcisms have run
MODEL_DOWN = [False]


def ghost(gid, db):
    """s = session others wait on, f = forgotten file, p = prepared side, c = committed side of the same transaction."""
    base = {"id": gid, "db": db, "pdb": "P", "what": f"ghost {gid}", "since": "", "exorcise": [], "verify": [],
            "requires": [], "bury": "by hand"}
    if gid[0] == "s":
        return base | {"kind": "lingering", "facts": {"sid": 1, "blocking": 1}, "exorcise": [["P", f"kill {gid}"]]}
    if gid[0] == "f":
        return base | {"kind": "file", "facts": {"file": "/x/control02.ctl"}}
    state = "prepared" if gid[0] == "p" else "committed"
    return base | {"kind": "indoubt", "facts": {"local_tran_id": gid, "global_tran_id": "G.1", "state": state}}


@activity.defn(name="hunt_db")
def hunt_db(a: DbIn) -> dict:
    if a.target["name"] == "gone" or (a.target["name"] in GONE_AFTER and TOUCHED):
        raise RuntimeError("DPY-6005: cannot connect to database")
    return {"ghosts": [ghost(g, a.target["name"]) for g in HAUNTED.get(a.target["name"], [])], "notes": []}


@activity.defn(name="write_story")
def write_story(a: StoryIn) -> dict:
    if MODEL_DOWN[0]:
        raise ConnectionError("Ollama is not running")
    return {"summary": ["one", "two"], "epitaphs": {g["id"]: f"Here lies {g['id']}." for g in a.ghosts},
            "by": {"summary": "template"}, "calls": []}


@activity.defn(name="exorcise")
def exorcise(a: ExorciseIn) -> dict:
    sql = a.steps[0][1]
    gid = m.group(1) if (m := re.search(r"'(\w+)'", sql)) else sql.split()[-1]
    if gid in CHANGED:
        return {"done": False, "log": ["not touched: it changed since the hunt"]}
    for _, sql in a.steps:
        TOUCHED.append(sql)
    HAUNTED[a.target["name"]].remove(gid)
    return {"done": True, "log": ["ok"]}


@activity.defn(name="deliver")
def deliver(a: DeliverIn) -> str:
    REPORTS.append(a.markdown)
    return a.name


def reset(**haunted):
    TOUCHED.clear(); REPORTS.clear(); CHANGED.clear(); GONE_AFTER.clear(); MODEL_DOWN[0] = False
    HAUNTED.clear(); HAUNTED.update({k: list(v) for k, v in haunted.items()})


async def run(env, approval=None, wait_hours=1.0, dbs=("lab-a", "gone")):
    async with Worker(env.client, task_queue=QUEUE, workflows=[GhostHunt],
                      activities=[hunt_db, examine, write_story, exorcise, deliver], activity_executor=ThreadPoolExecutor(4)):
        h = await env.client.start_workflow(GhostHunt.run, HuntIn([{"name": d} for d in dbs], wait_hours, False),
                                            id=str(uuid.uuid4()), task_queue=QUEUE)
        if approval is not None:
            while (await h.query(GhostHunt.status))["stage"] != "waiting for approval":
                await env.sleep(1)
            await h.signal(GhostHunt.approve, Approval(approval))
        return await h.result()


@pytest.mark.asyncio
async def test_only_approved_ghosts_are_touched_and_checked_again():
    reset(**{"lab-a": ["s1", "f1"]})
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, approval=[1, 2, 99])
    assert TOUCHED == ["kill s1"]                      # the file has no exorcism, so it is never touched
    assert "Laid to rest" in out and "doesn't touch this kind" in out
    assert "No ghost #99 in this hunt" in out
    assert "couldn't hunt" in REPORTS[0] and "DPY-6005" in REPORTS[0]  # retried, then reported, not a crash
    assert "Nothing has been touched" in REPORTS[0]


@pytest.mark.asyncio
async def test_a_ghost_that_changed_since_the_hunt_is_left_alone():
    reset(**{"lab-a": ["s1"]})
    CHANGED.add("s1")
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, approval=[1])
    assert TOUCHED == [] and "it changed since the hunt" in out and "Laid to rest" not in out


@pytest.mark.asyncio
async def test_the_written_outcome_is_not_purged_before_its_partner():
    reset(**{"lab-a": ["p1"], "lab-b": ["c1"]})
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, approval=[2], dbs=("lab-a", "lab-b"))  # only the committed side: #1 is the prepared one
    assert TOUCHED == [] and "so its partner is forced first" in out
    reset(**{"lab-a": ["p1"], "lab-b": ["c1"]})
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, approval=[1, 2], dbs=("lab-a", "lab-b"))
    assert [t.split()[0] for t in TOUCHED] == ["commit", "begin", "begin"]  # force, purge, then the partner's purge
    assert out.count("Laid to rest") == 2


@pytest.mark.asyncio
async def test_a_check_that_cannot_run_is_not_reported_as_success():
    reset(**{"lab-a": ["s1"]})
    GONE_AFTER.add("lab-a")
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, approval=[1], dbs=("lab-a",))
    assert "couldn't be hunted again" in out and "Laid to rest" not in out


@pytest.mark.asyncio
async def test_the_report_goes_out_without_the_model():
    reset(**{"lab-a": ["f1"]})
    MODEL_DOWN[0] = True
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, dbs=("lab-a",))
    assert "wasn't reachable" in out and "control02" not in out and "ghost f1" in out


@pytest.mark.asyncio
async def test_unanswered_hunt_touches_nothing():
    reset(**{"lab-a": ["s1", "f1"]})
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, approval=None, wait_hours=12)  # twelve hours pass in a moment on the test server
    assert TOUCHED == []
    assert "Nobody approved" in out
