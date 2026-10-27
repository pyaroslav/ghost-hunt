# Ghost Hunt

**Ghost Hunt finds what's still haunting your Oracle databases, and lays it to rest only when you say so.**
Sessions nobody is coming back for, in-doubt distributed transactions holding rows no one can read, files the
database forgot, tables dropped but still holding space, users who left with their jobs still running.

SQL finds every ghost. Gemma (running locally) writes the field notes. A Temporal workflow waits, for hours if
need be, for a person to approve each exorcism, and then hunts again to prove the ghost is gone.

Work in progress for the DEV Hacktoberfest "Ghost in the Machine" challenge (Oct 26 to 31, 2026).
The full write-up, demo and results land here before Oct 31.

```bash
pip install -e .
ghost-hunt now                 # hunt and print the report; touches nothing
ghost-hunt server & ghost-hunt worker &
ghost-hunt hunt                # report, then wait for approval
ghost-hunt approve <id> 1 2    # lay ghosts 1 and 2 to rest
```

Builds on patterns from my earlier entry [Night Shift](https://github.com/pyaroslav/night-shift)
(validated model output with a template fallback, the local Temporal setup).

## License

MIT
