"""ghost-hunt: find what's still haunting your Oracle databases, and lay it to rest only when you say so.

    ghost-hunt now                      # hunt and print the report, no Temporal, touches nothing
    ghost-hunt server                   # local Temporal (one binary, SQLite file, UI on :8234)
    ghost-hunt worker                   # runs the steps; leave it running
    ghost-hunt hunt                     # start a hunt; prints the report and waits for your approval
    ghost-hunt approve <id> 1 3         # lay ghosts 1 and 3 to rest (or: all)
    ghost-hunt status <id>              # where a hunt is
"""

import argparse
import asyncio
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ADDRESS = os.environ.get("TEMPORAL_ADDRESS", "127.0.0.1:7234")


def env():
    """Passwords and the Sentry DSN live in ~/.ghost-hunt.env, never in the repo."""
    f = Path(os.environ.get("GHOST_ENV", Path.home() / ".ghost-hunt.env"))
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


async def client():
    from temporalio.client import Client
    return await Client.connect(ADDRESS)


def server(a):
    exe = a.temporal or next(iter(sorted((ROOT / ".temporal-bin").glob("temporal*"))), None) or "temporal"
    (ROOT / ".temporal").mkdir(exist_ok=True)
    os.execvp(str(exe), [str(exe), "server", "start-dev", "--db-filename", str(ROOT / ".temporal" / "ghost-hunt.db"),
                         "--ui-port", "8234", "--port", "7234", "--log-level", "warn"])


async def worker(a):
    from temporalio.worker import Worker
    from .flow import QUEUE, GhostHunt, deliver, examine, exorcise, hunt_db, write_story
    w = Worker(await client(), task_queue=QUEUE, workflows=[GhostHunt],
               activities=[hunt_db, examine, write_story, exorcise, deliver],
               activity_executor=ThreadPoolExecutor(8), identity=f"ghost-hunt-worker-{os.getpid()}")
    print(f"worker on {ADDRESS}, queue {QUEUE}", flush=True)
    await w.run()


async def hunt(a):
    from temporalio.client import WorkflowExecutionStatus
    from .flow import QUEUE, GhostHunt, HuntIn, load_targets
    c = await client()
    wid = f"hunt-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}"
    h = await c.start_workflow(GhostHunt.run, HuntIn(load_targets(a.targets), a.wait_hours, not a.quiet), id=wid, task_queue=QUEUE)
    print(f"hunting: {wid}", file=sys.stderr)
    while True:
        if (await h.describe()).status != WorkflowExecutionStatus.RUNNING:
            print(await h.result())  # raises with the reason if the workflow failed
            return
        s = await h.query(GhostHunt.status)
        if s["report"]:
            print(s["report"])  # this hunt's own report, whatever directory the worker writes to
            return
        await asyncio.sleep(1)


async def approve(a):
    """A signal is stored by the Temporal server, so numbered approvals work even while the worker is down."""
    from temporalio.service import RPCError
    from .flow import Approval, GhostHunt
    h = (await client()).get_workflow_handle(a.workflow_id)
    try:
        s = await h.query(GhostHunt.status)
    except RPCError:
        s = None  # no worker right now: the signal still waits in Temporal
    if s and s["stage"] != "waiting for approval":
        sys.exit(f"this hunt is {s['stage']}, not waiting for an approval")
    if a.ghosts == ["all"]:
        if s is None:
            sys.exit("'all' needs a running worker to list the ghosts; give their numbers instead")
        picks = [g["n"] for g in s["ghosts"] if g["exorcise"]]
    else:
        try:
            picks = sorted({int(x) for x in a.ghosts})
        except ValueError:
            sys.exit("give ghost numbers from the report, or: all")
        if s:
            numbers = {g["n"]: g for g in s["ghosts"]}
            if bad := [n for n in picks if n not in numbers]:
                sys.exit(f"no ghost {', '.join(map(str, bad))} in this hunt")
            if bare := [n for n in picks if not numbers[n]["exorcise"]]:
                print(f"note: {', '.join(map(str, bare))} can't be exorcised by the hunter; the report says what to do",
                      file=sys.stderr)
    if not picks:
        sys.exit("nothing to approve")
    await h.signal(GhostHunt.approve, Approval(picks))
    print(f"approved {picks}; the exorcism runs as soon as a worker is up", file=sys.stderr)
    if not a.no_wait:
        print(await h.result())


async def status(a):
    from temporalio.service import RPCError
    from .flow import GhostHunt
    h = (await client()).get_workflow_handle(a.workflow_id)
    try:
        s = await h.query(GhostHunt.status)
    except RPCError:
        d = await h.describe()
        print(f"no worker is running; the hunt is safe in Temporal ({d.status.name.lower()}). Start `ghost-hunt worker`.")
        return
    print(f"stage: {s['stage']}")
    for g in s["ghosts"]:
        print(f"  {g['n']:>2}. [{g['db']}] {g['what']}{'' if g['exorcise'] else '  (bury it yourself)'}")


def now(a):
    from . import hunt as h, narrate, report
    from .flow import load_targets
    ghosts, problems = [], []
    for t in load_targets(a.targets):
        try:
            found, notes = h.hunt(t)
            ghosts += found
            problems += [{"db": t["name"], "note": n} for n in notes]
        except Exception as e:  # noqa: BLE001 - one dead database shouldn't stop the hunt
            problems.append({"db": t["name"], "error": str(e).splitlines()[0]})
    h.pair_indoubt(ghosts)
    gs = sorted((asdict(g) for g in ghosts), key=narrate.harm)
    for i, g in enumerate(gs):
        g["n"] = i + 1
    if a.json:
        print(json.dumps(gs, indent=1, default=str))
        return
    print(report.found(gs, narrate.narrate(gs), "", problems))


def main():
    env()
    ap = argparse.ArgumentParser(prog="ghost-hunt", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("server"); p.add_argument("--temporal", help="path to the temporal CLI")
    sub.add_parser("worker")
    for name in ("now", "hunt"):
        p = sub.add_parser(name)
        p.add_argument("--targets", default=str(ROOT / "targets.json"))
        if name == "hunt":
            p.add_argument("--wait-hours", type=float, default=12, help="how long to wait for an approval")
            p.add_argument("--quiet", action="store_true", help="no desktop notification")
        else:
            p.add_argument("--json", action="store_true")
    p = sub.add_parser("approve"); p.add_argument("workflow_id"); p.add_argument("ghosts", nargs="+")
    p.add_argument("--no-wait", action="store_true", help="send the approval and return")
    p = sub.add_parser("status"); p.add_argument("workflow_id")
    a = ap.parse_args()
    if a.cmd == "server":
        return server(a)
    if a.cmd == "now":
        return now(a)
    from temporalio.service import RPCError
    try:
        asyncio.run({"worker": worker, "hunt": hunt, "approve": approve, "status": status}[a.cmd](a))
    except RPCError as e:  # e.g. no Temporal server, an unknown hunt id, a hunt that already finished
        sys.exit(f"temporal: {e.message}")


if __name__ == "__main__":
    main()
