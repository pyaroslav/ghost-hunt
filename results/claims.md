# False claims in Gemma's lines, counted by hand

Input: `results/ghosts.json`, one real hunt of the two lab databases (7 ghosts). `lab/eval.py` asked each model
20 times; every answer's lines are in `results/eval.json` under `first.lines`.

The automatic checks (each epitaph names its ghost, no number or upper-case name that isn't in the ghost's
facts, two summary lines that name both databases and start line 2 with the ghost ranked first) passed on
**every line of every run for both models: 160/160 each, no retries, no template lines.** So the checks didn't
tell the models apart. What did is something the checks can't see: a sentence that uses only the right names and
numbers and still says something untrue. I read all 280 epitaphs and 40 summaries against the facts.

A line counts as false only if it states something wrong about the ghost. Odd wording doesn't count
("seven point zero MB", "who was a file").

| | gemma4:e4b | gemma4:26b |
|---|---|---|
| Epitaphs with a false claim | **24 / 140** | **1 / 140** |
| Summary line 2 with a false claim | **5 / 20** | **0 / 20** |
| Median seconds per answer (after the model is loaded) | 1.05 | 1.32 |

## What e4b got wrong

- "It still holds 7.0 MB **of memory**" (8): it's disk space in a tablespace.
- The idle session "**still waits for another session**" / "holds a lock that waits on another session" (6):
  it's the other way round. It is the one being waited on.
- The committed in-doubt entry is "**purged** by the system" / "already been purged" (5): it isn't. Purging it
  is the exorcism, and nothing had been approved.
- GH_CONTRACTOR "**locked objects**" / "keeps two objects locked" / "**was active** since 2026-10-27" (5):
  the account was locked on that date. It didn't lock anything.
- Summary line 2: the blocking session is "**waiting for a decision** that will not come" (5): that's the
  in-doubt transaction's story, moved onto the session.

## What 26b got wrong

- "It still holds 7.0 MB of memory" (1).

## Why this decides the model

Every one of e4b's false lines would pass a stricter version of the checks too: right ghost, right numbers, no
invented names. Only reading them catches them. So the default is gemma4:26b (`GHOST_MODEL` changes it). It's
the same family, still local, and about a quarter of a second slower once loaded. The first call after a cold
start takes ~30 s while it loads.

## Limits

One fixture, 20 runs per model, one reader (me). The facts are short, which makes the judging easy. The list of
ghosts, their order, the decision for each in-doubt transaction and the SQL never come from the model. A false
epitaph makes the report wrong in tone. It can't make the hunter touch the wrong thing.
