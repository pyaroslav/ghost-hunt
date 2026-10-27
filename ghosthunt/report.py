"""The ghost report as Markdown: what was found, Gemma's epitaphs, and how to lay each ghost to rest."""

ICON = {"lingering": "🕯️", "killed": "🕯️", "indoubt": "⛓️", "file": "📜", "recyclebin": "⚰️", "invalid": "🪦", "orphan": "🗝️"}


def found(ghosts: list[dict], story: dict, workflow_id: str = "", unreadable: list[dict] = ()) -> str:
    out = ["# Ghost Hunt", "", *[f"> {ln}" for ln in story["summary"]], ""]
    if story["by"].get("summary") == "template":
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
        out += [f"## {u['db']}: couldn't hunt", "", f"{u['error']}", ""]
    if not ghosts and not unreadable:
        out += ["No ghosts.", ""]
    can = [str(g["n"]) for g in ghosts if g["exorcise"]]
    if workflow_id and can:
        out += ["---", f"Nothing has been touched. To lay ghosts to rest: `ghost-hunt approve {workflow_id} {' '.join(can)}`"
                f" (or a subset). Ghosts without an exorcism are yours to bury.", ""]
    calls = story.get("calls", [])
    tok = sum(c.get("input_tokens", 0) + c.get("output_tokens", 0) for c in calls)
    model = next((m for m in story["by"].values() if m != "template"), "template")
    tmpl = sum(1 for m in story["by"].values() if m == "template")
    out.append(f"_Words by {model}, {len(calls)} call{'s' if len(calls) != 1 else ''}, {tok} tokens"
               + (f", {tmpl} line{'s' if tmpl != 1 else ''} from the template" if tmpl else "") + "._")
    return "\n".join(out) + "\n"


def rested(ghosts: list[dict], approved: list[int], outcomes: dict, still: set, workflow_id: str = "") -> str:
    out = ["# Ghost Hunt: after the exorcism", ""]
    if not approved:
        return "\n".join(out + ["Nobody approved an exorcism in time, so nothing was touched. The ghosts are where they were.", ""])
    for g in ghosts:
        if g["n"] not in approved:
            continue
        if not g["exorcise"]:
            out.append(f"- **{g['n']}.** {g['what']}: not exorcised, the hunter doesn't touch this kind. {g['bury']}")
        elif g["id"] in still:
            out.append(f"- **{g['n']}.** {g['what']}: **still there** after {', '.join(outcomes.get(g['id'], ['no attempt']))}.")
        else:
            out.append(f"- **{g['n']}.** {ICON[g['kind']]} Laid to rest. {g['what']}.")
    left = [g for g in ghosts if g["n"] not in approved and g["id"] in still]
    if left:
        out += ["", f"Not approved, still there: {', '.join(str(g['n']) for g in left)}."]
    if workflow_id:
        out += ["", f"_Workflow {workflow_id}._"]
    return "\n".join(out) + "\n"
