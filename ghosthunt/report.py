"""The ghost report as Markdown: what was found, Gemma's epitaphs, and how to lay each ghost to rest."""

ICON = {"lingering": "🕯️", "killed": "🕯️", "indoubt": "⛓️", "file": "📜", "recyclebin": "⚰️", "invalid": "🪦", "orphan": "🗝️"}


def found(ghosts: list[dict], story: dict, workflow_id: str = "", unreadable: list[dict] = ()) -> str:
    out = ["# Ghost Hunt", "", *[f"> {ln}" for ln in story["summary"]], ""]
    if story.get("unavailable"):
        out += [f"_(The model wasn't reachable ({story['unavailable']}), so every line is from a template.)_", ""]
    elif story["by"].get("summary") == "template" and ghosts:
        out += ["_(Gemma couldn't write a faithful summary; these two lines are from a template.)_", ""]
    for g in ghosts:  # in the order they should be dealt with; the numbers are what you approve
        when = f" Since {g['since']}." if g["since"] else ""
        out.append(f"### {g['n']}. {ICON[g['kind']]} {g['what']}")
        out.append(f"`{g['db']}` · `{g['pdb']}`.{when}")
        out.append("")
        out.append(f"_{story['epitaphs'][g['id']]}_")
        out.append("")
        if "decision" in g["facts"]:
            out += [f"The other side: {', '.join(f'{k} {v}' for k, v in g['facts']['other_sides'].items())}. "
                    f"Decision: {g['facts']['decision']}.", ""]
        if g["exorcise"]:
            out.append("Exorcism, if you approve it:")
            out += ["```sql", *[sql if sql.endswith("end;") else f"{sql};" for _, sql in g["exorcise"]], "```"]
        else:
            text, _, sql = g["bury"].partition("SQL:")
            out.append(f"The hunter won't touch this one. {text.strip()}")
            if sql:
                out += ["```sql", sql.strip(), "```"]
        out.append("")
    for u in unreadable:
        if "error" in u:
            out += [f"## {u['db']}: couldn't hunt", "", f"{u['error']}", ""]
        else:
            out += [f"## {u['db']}: not checked", "", u["note"], ""]
    if not ghosts and not unreadable:
        out += ["No ghosts.", ""]
    can = [str(g["n"]) for g in ghosts if g["exorcise"]]
    if workflow_id and can:
        out += ["---", f"Nothing has been touched. To lay ghosts to rest: `ghost-hunt approve {workflow_id} {' '.join(can)}`"
                f" (or a subset). Ghosts without an exorcism are yours to bury.", ""]
    if not ghosts:
        return "\n".join(out) + "\n"
    calls = [c for c in story.get("calls", []) if "error" not in c]
    tok = sum(c.get("input_tokens", 0) + c.get("output_tokens", 0) for c in calls)
    model = next((m for m in story["by"].values() if m != "template"), "template")
    tmpl = sum(1 for m in story["by"].values() if m == "template")
    out.append(f"_Words by {model}, {len(calls)} call{'s' if len(calls) != 1 else ''}, {tok} tokens"
               + (f", {tmpl} line{'s' if tmpl != 1 else ''} from the template" if tmpl else "") + "._")
    return "\n".join(out) + "\n"


def rested(ghosts: list[dict], approved: list[int], outcomes: dict, after: dict, unchecked: set = frozenset(),
           workflow_id: str = "") -> str:
    """after: {ghost id: kind} from the hunt that ran after the exorcisms; unchecked: databases it couldn't reach."""
    out = ["# Ghost Hunt: after the exorcism", ""]
    if not approved:
        return "\n".join(out + ["Nobody approved an exorcism in time, so nothing was touched. The ghosts are where they were.", ""])
    known = {g["n"] for g in ghosts}
    for g in ghosts:
        if g["n"] not in approved:
            continue
        o = outcomes.get(g["id"], {"status": "", "log": []})
        head = f"- **{g['n']}.**"
        if not g["exorcise"]:
            out.append(f"{head} {g['what']}: not exorcised, the hunter doesn't touch this kind. {g['bury']}")
        elif o["status"] != "done":
            out.append(f"{head} {g['what']}: {'; '.join(o['log']) or 'not run'}.")
        elif g["db"] in unchecked:
            out.append(f"{head} {g['what']}: the exorcism ran, but {g['db']} couldn't be hunted again to check it.")
        elif after.get(g["id"]) == "killed":
            out.append(f"{head} {ICON[g['kind']]} Killed. The session is still rolling back its work and will leave "
                       f"when that's done; the session waiting on it is free.")
        elif g["id"] in after:
            out.append(f"{head} {g['what']}: **still there** after {', '.join(o['log'])}.")
        else:
            out.append(f"{head} {ICON[g['kind']]} Laid to rest. {g['what']}.")
    if unknown := [n for n in approved if n not in known]:
        out += ["", f"No ghost {', '.join(f'#{n}' for n in unknown)} in this hunt, so nothing was done for it."]
    left = [g for g in ghosts if g["n"] not in approved]
    if left:
        out += ["", f"Not approved, left as they were: {', '.join(str(g['n']) for g in left)}."]
    if workflow_id:
        out += ["", f"_Workflow {workflow_id}._"]
    return "\n".join(out) + "\n"


def fallback_story(ghosts: list[dict], why: str) -> dict:
    """When the model can't be used at all: plain lines from the facts, so the report still goes out."""
    dbs = sorted({g["db"] for g in ghosts})
    summary = ([f"{len(ghosts)} ghost{'s' if len(ghosts) != 1 else ''} in {', '.join(dbs)}.",
                f"Start with #1: {ghosts[0]['what']}."] if ghosts else ["No ghosts.", "Nothing to lay to rest."])
    return {"summary": summary, "epitaphs": {g["id"]: f"Here lies one more. {g['what']}." for g in ghosts},
            "by": {"summary": "template", **{g["id"]: "template" for g in ghosts}}, "calls": [], "unavailable": why}
