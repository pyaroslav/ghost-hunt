"""Gemma writes the ghost report's voice: two lines on top and an epitaph for each ghost.

The list of ghosts, their ids and what to do about them come straight from the hunt. The model only writes
words around them, and every line is checked before it's used: each epitaph has to name its own ghost and
may only use numbers and names that are in that ghost's facts. A line that fails gets one retry; after that a
plain line from the facts takes its place. (Same idea as Night Shift's two-line brief.)
"""

import json
import os
import re
import time

import requests

OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
MODEL = os.environ.get("GHOST_MODEL", "gemma4:26b")

PROMPT = """You keep the field notes of a ghost hunt in two Oracle databases. Each ghost below is real: something
still in the database after whatever owned it is gone. Write like an old caretaker who has buried many databases:
dry, kind, a little spooky. No exclamation marks, no emoji, no jargon like "indoubt" or "lingering".

Return JSON: {{"summary": ["line 1", "line 2"], "epitaphs": {{"G1": "...", "G2": "..."}}}}
- summary line 1: under 30 words. Name both databases and what haunts each, in plain words.
- summary line 2: under 25 words. Deal with {first} first; say the harm it is doing right now.
- one epitaph per ghost, under 30 words. Start with "Here lies" and its "name". Say what it was for in life
  and what it still does now that nobody is there. Example of the tone (not a real ghost):
  "Here lies session 88, who began a payroll update at 17:02 and went home. It still holds the row; two clerks wait at the door."
Key the epitaphs by G1, G2, ... exactly. Use only facts, names, numbers and dates from that ghost's entry; call every ghost "it".
In the summary, call ghosts by name, never G1/G2.

Ghosts:
{ghosts}
"""


HARM = {"lingering": 0, "indoubt": 1, "killed": 2, "orphan": 3, "file": 4, "recyclebin": 5, "invalid": 6}


def harm(g: dict) -> tuple:
    """What hurts someone right now comes first: a session others wait on, a row nobody can read."""
    f = g["facts"]
    return (0 if f.get("blocking") or f.get("state") == "prepared" else 1, HARM[g["kind"]])


def compact(ghosts: list[dict]) -> dict:
    """What the model sees: a short ref, the database, a name and the facts. No SQL."""
    return {f"G{i + 1}": {"database": g["db"], "name": name(g), "what": g["what"],
                          "since": g["since"], **({"decision": g["facts"]["decision"]} if "decision" in g["facts"] else {})}
            for i, g in enumerate(ghosts)}


def name(g: dict) -> str:
    """The word an epitaph must contain, so a reader can match it to the ghost."""
    f = g["facts"]
    return {"lingering": f"session {f.get('sid')}", "killed": f"session {f.get('sid')}",
            "indoubt": f.get("local_tran_id", ""), "recyclebin": f.get("table", ""), "invalid": f.get("object", ""),
            "orphan": f.get("user", ""), "file": f.get("file", "").rsplit("/", 1)[-1]}[g["kind"]]


def numbers(text: str) -> set[str]:
    return {n.lstrip("0") or "0" for n in re.findall(r"\d+", text)}


def words(text: str) -> set[str]:
    """Identifiers: upper-case names with underscores or dots, which the model must not invent."""
    return set(re.findall(r"\b[A-Z][A-Z0-9]*(?:[_.$#][A-Z0-9]+)+\b", text))


def check_epitaph(text: str, entry: dict) -> list[str]:
    out = []
    if entry["name"].lower() not in text.lower():
        out.append(f"doesn't name {entry['name']}")
    src = json.dumps(entry)
    if bad := numbers(text) - numbers(src):
        out.append(f"numbers not in the facts: {', '.join(sorted(bad))}")
    if bad := words(text) - words(src):
        out.append(f"names not in the facts: {', '.join(sorted(bad))}")
    if len(text.split()) > 40:
        out.append("too long")
    return out


def check_summary(lines: list, digest: dict) -> list[str]:
    out = []
    if len(lines) != 2:
        return [f"summary has {len(lines)} lines, not 2"]
    src = json.dumps(digest)
    text = " ".join(lines)
    if bad := numbers(text) - numbers(src):
        out.append(f"summary numbers not in the facts: {', '.join(sorted(bad))}")
    if re.search(r"\bG\d+\b", text):
        out.append("summary uses the G labels; name the ghosts instead")
    for db in {e["database"] for e in digest.values()}:
        if db.lower() not in text.lower():
            out.append(f"summary doesn't name {db}")
    return out


def ask(prompt: str) -> dict:
    t0 = time.time()
    r = requests.post(f"{OLLAMA}/api/chat", timeout=600, json={
        "model": MODEL, "stream": False, "think": False, "format": "json",
        "options": {"temperature": 0.4, "num_ctx": 8192}, "messages": [{"role": "user", "content": prompt}]})
    r.raise_for_status()
    j = r.json()
    return {"content": j["message"]["content"], "input_tokens": j.get("prompt_eval_count", 0),
            "output_tokens": j.get("eval_count", 0), "seconds": round(time.time() - t0, 2)}


def template_epitaph(entry: dict) -> str:
    return f"Here lies {entry['name']}. {entry['what']}."


def narrate(ghosts: list[dict], tries: int = 2) -> dict:
    """{'summary': [2 lines], 'epitaphs': {ghost id: line}, 'by': {...}, 'calls': [token/latency per call]}"""
    ghosts = sorted(ghosts, key=harm)
    digest = compact(ghosts)
    if not digest:
        return {"summary": ["No ghosts tonight. Every session has a living client and every transaction an owner.",
                            "Nothing to lay to rest."], "epitaphs": {}, "by": {"summary": "template"}, "calls": []}
    first = digest["G1"]["name"]
    prompt = PROMPT.format(ghosts=json.dumps(digest, indent=1), first=first)
    calls, epitaphs, summary, by, issues = [], {}, None, {}, []
    for attempt in range(tries):
        try:
            res = ask(prompt if not issues else prompt + "\nYour last answer had problems: " + "; ".join(issues[:8]) + ". Fix them.")
        except requests.RequestException as e:
            calls.append({"error": type(e).__name__})
            break
        calls.append({k: res[k] for k in ("input_tokens", "output_tokens", "seconds")})
        try:
            out = json.loads(res["content"])
        except json.JSONDecodeError:
            issues = ["answer wasn't JSON"]
            continue
        issues = []
        for ref, entry in digest.items():
            if ref in epitaphs:
                continue
            eps = out.get("epitaphs") or {}
            if isinstance(eps, list):  # sometimes a list in ghost order
                eps = {f"G{i + 1}": e for i, e in enumerate(eps)}
            text = " ".join(str(eps.get(ref) or eps.get(entry["name"]) or "").split())
            if not (bad := check_epitaph(text, entry)):
                epitaphs[ref] = text
                by[ref] = MODEL
            else:
                issues += [f"{ref} {b}" for b in bad]
        if summary is None:
            lines = [" ".join(str(x).split()) for x in out.get("summary", [])]
            bad = check_summary(lines, digest)
            if len(lines) == 2 and first.lower() not in lines[1].lower():
                bad.append(f"line 2 must start with {first}")
            if not bad:
                summary, by["summary"] = lines, MODEL
            else:
                issues += bad
        if summary and len(epitaphs) == len(digest):
            break
    for ref, entry in digest.items():
        if ref not in epitaphs:
            epitaphs[ref], by[ref] = template_epitaph(entry), "template"
    if summary is None:
        dbs = sorted({e["database"] for e in digest.values()})
        summary, by["summary"] = [f"{len(digest)} ghosts in {', '.join(dbs)}.",
                                  f"Start with {digest['G1']['name']} on {digest['G1']['database']}."], "template"
    ids = {f"G{i + 1}": g["id"] for i, g in enumerate(ghosts)}
    return {"summary": summary, "epitaphs": {ids[r]: t for r, t in epitaphs.items()},
            "by": {ids.get(r, r): m for r, m in by.items()}, "calls": calls, "rejected": issues}
