"""The hunt as a Temporal workflow: find, narrate, deliver, then wait (for hours if need be) for a person to
approve an exorcism. Only approved ghosts are touched, and the databases are hunted again to prove they're gone.

If the worker dies while waiting, nothing is lost: the workflow is still waiting when it comes back, and the
approval signal can arrive before or after.
"""

import asyncio
import json
import os
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .report import fallback_story, found as render_found, rested

QUEUE = "ghost-hunt"
REPORTS = os.environ.get("GHOST_REPORTS", "reports")
RETRY = RetryPolicy(initial_interval=timedelta(seconds=10), backoff_coefficient=2, maximum_attempts=4)


@dataclass
class HuntIn:
    targets: list[dict]
    wait_hours: float = 12
    notify: bool = True


@dataclass
class DbIn:
    workflow_id: str
    target: dict


@dataclass
class StoryIn:
    workflow_id: str
    ghosts: list[dict]


@dataclass
class ExorciseIn:
    workflow_id: str
    target: dict
    steps: list
    checks: list = field(default_factory=list)


@dataclass
class DeliverIn:
    workflow_id: str
    name: str
    markdown: str
    headline: str
    notify: bool = True


@dataclass
class Approval:
    ghosts: list[int] = field(default_factory=list)


# ---- activities: the only code that touches databases, the model or the disk ----

def _wid() -> str:
    return activity.info().workflow_id


@activity.defn
def hunt_db(a: DbIn) -> dict:
    from . import hunt, trace
    trace.init()
    with trace.activity(a.workflow_id, "hunt_db", activity.info().attempt):
        with trace.span("gen_ai.execute_tool", "execute_tool hunt_database", **{"gen_ai.tool.name": "hunt_database",
                                                                               "ghost.database": a.target["name"]}) as s:
            found, notes = hunt.hunt(a.target)
            ghosts = [asdict(g) for g in found]
            s.set_data("ghost.count", len(ghosts))
            s.set_data("ghost.kinds", json.dumps(sorted({g["kind"] for g in ghosts})))
            return {"ghosts": ghosts, "notes": notes}


@activity.defn
def examine(ghosts: list[dict]) -> list[dict]:
    """Pair in-doubt transactions across databases, rank by harm, number them for approval."""
    from .hunt import Ghost, pair_indoubt
    from .narrate import harm
    gs = [Ghost(**g) for g in ghosts]
    pair_indoubt(gs)
    out = sorted((asdict(g) for g in gs), key=harm)
    for i, g in enumerate(out):
        g["n"] = i + 1
    return out


@activity.defn
def write_story(a: StoryIn) -> dict:
    """Gemma's two lines and epitaphs."""
    from . import narrate, trace
    trace.init()
    ghosts = a.ghosts
    with trace.activity(a.workflow_id, "write_story", activity.info().attempt):
        with trace.span("gen_ai.chat", f"chat {narrate.MODEL}", **{"gen_ai.request.model": narrate.MODEL,
                                                                  "gen_ai.provider.name": "ollama",
                                                                  "gen_ai.request.temperature": 0.4}) as s:
            story = narrate.narrate(ghosts)
            s.set_data("gen_ai.response.model", narrate.MODEL)
            s.set_data("gen_ai.usage.input_tokens", sum(c.get("input_tokens", 0) for c in story["calls"]))
            s.set_data("gen_ai.usage.output_tokens", sum(c.get("output_tokens", 0) for c in story["calls"]))
            s.set_data("ghost.retries", max(len(story["calls"]) - 1, 0))
            s.set_data("ghost.template_lines", sum(1 for m in story["by"].values() if m == "template"))
            return story


@activity.defn
def exorcise(a: ExorciseIn) -> dict:
    from . import exorcise as ex, trace
    trace.init()
    with trace.activity(a.workflow_id, "exorcise", activity.info().attempt):
        with trace.span("gen_ai.execute_tool", "execute_tool exorcise", **{"gen_ai.tool.name": "exorcise",
                                                                         "ghost.database": a.target["name"]}):
            return ex.exorcise(a.target, a.steps, a.checks)


@activity.defn
def deliver(a: DeliverIn) -> str:
    out = Path(REPORTS)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{a.name}.md"
    path.write_text(a.markdown)
    (out / "latest.md").write_text(a.markdown)
    if a.notify:
        subprocess.run(["notify-send", "-a", "Ghost Hunt", "-i", "dialog-warning", "Ghost Hunt", a.headline],
                       check=False, capture_output=True)
    return str(path)


# ---- the workflow ----

@workflow.defn
class GhostHunt:
    def __init__(self) -> None:
        self.ghosts: list[dict] = []
        self.stage = "hunting"
        self.report = ""
        self.approval: Approval | None = None

    @workflow.signal
    def approve(self, a: Approval) -> None:
        self.approval = a

    @workflow.query
    def status(self) -> dict:
        return {"stage": self.stage, "report": self.report,
                "ghosts": [{"n": g["n"], "db": g["db"], "what": g["what"], "exorcise": bool(g["exorcise"])}
                           for g in self.ghosts]}

    async def _hunt(self, targets: list[dict]) -> tuple[list[dict], list[dict]]:
        """(ghosts, problems): a database that can't be hunted, or a part of one that couldn't be checked."""
        wid = workflow.info().workflow_id
        found, problems = [], []
        for t in targets:  # one at a time: two lab databases, and it keeps the timeline readable
            try:
                r = await workflow.execute_activity(hunt_db, DbIn(wid, t), start_to_close_timeout=timedelta(minutes=3),
                                                    retry_policy=RETRY)
                found += r["ghosts"]
                problems += [{"db": t["name"], "note": n} for n in r["notes"]]
            except ActivityError as e:
                problems.append({"db": t["name"], "error": str(e.cause or e)[:200]})
        return found, problems

    @workflow.run
    async def run(self, a: HuntIn) -> str:
        wid = workflow.info().workflow_id
        day = workflow.now().strftime("%Y-%m-%d_%H%M")
        by_name = {t["name"]: t for t in a.targets}
        found, problems = await self._hunt(a.targets)
        self.ghosts = await workflow.execute_activity(examine, found, start_to_close_timeout=timedelta(seconds=30))
        self.stage = "writing"
        try:
            story = await workflow.execute_activity(write_story, StoryIn(wid, self.ghosts),
                                                    start_to_close_timeout=timedelta(minutes=10), retry_policy=RETRY)
        except ActivityError as e:  # the model is the one part the hunt can do without
            story = fallback_story(self.ghosts, type(e.cause or e).__name__)
        md = render_found(self.ghosts, story, wid, problems)
        self.report = md
        n = len(self.ghosts)
        await workflow.execute_activity(deliver, DeliverIn(wid, f"{day}-found", md, f"{n} ghost{'s' if n != 1 else ''} found. "
                                                           "Nothing touched until you approve.", a.notify),
                                        start_to_close_timeout=timedelta(seconds=30))
        if not any(g["exorcise"] for g in self.ghosts):
            self.stage = "done"
            return md
        self.stage = "waiting for approval"
        try:
            await workflow.wait_condition(lambda: self.approval is not None, timeout=timedelta(hours=a.wait_hours))
        except asyncio.TimeoutError:  # the built-in TimeoutError only from 3.11
            pass
        picks = sorted(set(self.approval.ghosts)) if self.approval else []
        self.stage = "exorcising"
        outcomes: dict[str, dict] = {}
        for g in self.ghosts:  # in harm order, so a prepared transaction is forced before its partner is purged
            if g["n"] not in picks or not g["exorcise"]:
                continue
            first = [r for r in g.get("requires", []) if outcomes.get(r, {}).get("status") != "done"]
            if first:
                need = [str(o["n"]) for o in self.ghosts if o["id"] in first]
                outcomes[g["id"]] = {"status": "waiting", "log": [f"not touched: approve #{', #'.join(need)} with it, "
                                                                  "so its partner is forced first"]}
                continue
            try:
                r = await workflow.execute_activity(
                    exorcise, ExorciseIn(wid, by_name[g["db"]], g["exorcise"], g.get("verify", [])),
                    start_to_close_timeout=timedelta(minutes=2), retry_policy=RetryPolicy(maximum_attempts=2))
                outcomes[g["id"]] = {"status": "done" if r["done"] else "changed", "log": r["log"]}
            except ActivityError as e:
                outcomes[g["id"]] = {"status": "failed", "log": [f"failed: {str(e.cause or e)[:160]}"]}
        touched = {g["db"] for g in self.ghosts if outcomes.get(g["id"], {}).get("status") == "done"}
        after, again = await self._hunt([by_name[db] for db in touched]) if touched else ([], [])
        unchecked = {p["db"] for p in again if "error" in p}
        md2 = rested(self.ghosts, picks, outcomes, {g["id"]: g["kind"] for g in after}, unchecked, wid)
        rest = sum(1 for g in self.ghosts if outcomes.get(g["id"], {}).get("status") == "done"
                   and g["db"] not in unchecked and g["id"] not in {x["id"] for x in after if x["kind"] != "killed"})
        await workflow.execute_activity(deliver, DeliverIn(wid, f"{day}-rested", md2, f"{rest} ghost{'s' if rest != 1 else ''} laid to rest.",
                                                           a.notify), start_to_close_timeout=timedelta(seconds=30))
        self.report = md2
        self.stage = "done"
        return md2


def load_targets(path: str) -> list[dict]:
    return json.loads(Path(path).read_text())
