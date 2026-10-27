"""How often does the model's first answer pass the checks, and what reaches the report after the retry?

    python lab/eval.py results/ghosts.json gemma4:e4b gemma4:26b --runs 20 > results/eval.json

The ghosts are a fixed snapshot of one hunt, so every run sees the same input. Each run asks once and checks
the raw answer line by line (what you'd get without validation), then runs the real narrator (check, one retry,
template fallback). All lines are kept so the false claims the checks can't see can be counted by hand.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ghosthunt import narrate as n  # noqa: E402


def first_answer(ghosts: list[dict]) -> dict:
    ghosts = sorted(ghosts, key=n.harm)
    digest = n.compact(ghosts)
    first = digest["G1"]["name"]
    res = n.ask(n.PROMPT.format(ghosts=json.dumps(digest, indent=1), first=first))
    try:
        out = json.loads(res["content"])
    except json.JSONDecodeError:
        return {"json": False, "lines": {}, "fails": {"all": ["not JSON"]}}
    eps = out.get("epitaphs") or {}
    if isinstance(eps, list):
        eps = {f"G{i + 1}": e for i, e in enumerate(eps)}
    lines, fails = {}, {}
    for ref, entry in digest.items():
        text = " ".join(str(eps.get(ref) or eps.get(entry["name"]) or "").split())
        lines[ref] = text
        if bad := n.check_epitaph(text, entry):
            fails[ref] = bad
    summary = [" ".join(str(x).split()) for x in out.get("summary", [])]
    lines["summary"] = summary
    bad = n.check_summary(summary, digest)
    if len(summary) == 2 and first.lower() not in summary[1].lower():
        bad.append(f"line 2 doesn't start with {first}")
    if bad:
        fails["summary"] = bad
    return {"json": True, "lines": lines, "fails": fails, "seconds": res["seconds"],
            "tokens": res["input_tokens"] + res["output_tokens"]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("ghosts")
    ap.add_argument("models", nargs="+")
    ap.add_argument("--runs", type=int, default=20)
    a = ap.parse_args()
    ghosts = json.loads(Path(a.ghosts).read_text())
    out = {"ghosts": len(ghosts), "runs": a.runs, "models": {}}
    for model in a.models:
        n.MODEL = model
        n.ask(n.PROMPT.format(ghosts="{}", first="x"))  # load the model before timing
        runs = []
        for i in range(a.runs):
            raw = first_answer(ghosts)
            final = n.narrate(ghosts)
            runs.append({"first": raw, "final": {"summary": final["summary"], "epitaphs": final["epitaphs"],
                                                 "by": final["by"], "calls": final["calls"]}})
            print(f"{model} run {i + 1}: first-answer fails {len(raw['fails'])}, "
                  f"template lines {sum(1 for m in final['by'].values() if m == 'template')}", file=sys.stderr)
        out["models"][model] = runs
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main()
