"""The workflow's promises, on Temporal's time-skipping test server with stand-in activities:
nothing is exorcised without approval, only what was approved is touched, a dead database becomes a line in
the report, and a hunt nobody answers ends quietly after the wait."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from ghosthunt.flow import QUEUE, Approval, DbIn, DeliverIn, ExorciseIn, GhostHunt, HuntIn, StoryIn, examine

TOUCHED: list[str] = []
REPORTS: list[str] = []
HAUNTED = {"lab-a": ["s1", "f1"]}


def ghost(gid, db):
    kind = {"s": "lingering", "f": "file"}[gid[0]]
    return {"id": gid, "kind": kind, "db": db, "pdb": "P", "what": f"ghost {gid}", "since": "",
            "facts": {"sid": 1, "blocking": 1} if kind == "lingering" else {"file": "/x/control02.ctl"},
            "exorcise": [["P", f"kill {gid}"]] if kind == "lingering" else [], "bury": "by hand"}


@activity.defn(name="hunt_db")
def hunt_db(a: DbIn) -> list[dict]:
    if a.target["name"] == "gone":
        raise RuntimeError("DPY-6005: cannot connect to database")
    return [ghost(g, a.target["name"]) for g in HAUNTED.get(a.target["name"], [])]


@activity.defn(name="write_story")
def write_story(a: StoryIn) -> dict:
    return {"summary": ["one", "two"], "epitaphs": {g["id"]: f"Here lies {g['id']}." for g in a.ghosts},
            "by": {"summary": "template"}, "calls": []}


@activity.defn(name="exorcise")
def exorcise(a: ExorciseIn) -> list[str]:
    for _, sql in a.steps:
        TOUCHED.append(sql)
        HAUNTED[a.target["name"]].remove(sql.split()[1])
    return ["ok"]


@activity.defn(name="deliver")
def deliver(a: DeliverIn) -> str:
    REPORTS.append(a.markdown)
    return a.name


async def run(env, approval=None, wait_hours=1.0):
    async with Worker(env.client, task_queue=QUEUE, workflows=[GhostHunt],
                      activities=[hunt_db, examine, write_story, exorcise, deliver], activity_executor=ThreadPoolExecutor(4)):
        h = await env.client.start_workflow(GhostHunt.run, HuntIn([{"name": "lab-a"}, {"name": "gone"}], wait_hours, False),
                                            id=str(uuid.uuid4()), task_queue=QUEUE)
        if approval is not None:
            while (await h.query(GhostHunt.status))["stage"] != "waiting for approval":
                await env.sleep(1)
            await h.signal(GhostHunt.approve, Approval(approval))
        return await h.result()


@pytest.mark.asyncio
async def test_only_approved_ghosts_are_touched_and_checked_again():
    TOUCHED.clear(); REPORTS.clear(); HAUNTED["lab-a"] = ["s1", "f1"]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, approval=[1, 2])
    assert TOUCHED == ["kill s1"]                      # the file has no exorcism, so it is never touched
    assert "Laid to rest" in out and "doesn't touch this kind" in out
    assert "couldn't hunt" in REPORTS[0] and "DPY-6005" in REPORTS[0]  # retried, then reported, not a crash
    assert "Nothing has been touched" in REPORTS[0]


@pytest.mark.asyncio
async def test_unanswered_hunt_touches_nothing():
    TOUCHED.clear(); REPORTS.clear(); HAUNTED["lab-a"] = ["s1", "f1"]
    async with await WorkflowEnvironment.start_time_skipping() as env:
        out = await run(env, approval=None, wait_hours=12)  # twelve hours pass in a moment on the test server
    assert TOUCHED == []
    assert "Nobody approved" in out
