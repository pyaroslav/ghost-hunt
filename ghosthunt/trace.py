"""Sentry agent tracing, metadata only. Off unless SENTRY_DSN is set.

What leaves the machine: span names and timings, the model name, token counts, how many ghosts of each kind,
which lab database (by its name in targets.json), and errors. What never does: SQL, object or user names,
the ghosts' facts, the prompt or Gemma's answer. Prompts and answers stay out because nothing here records them
(and Sentry's own PII collection is off by default); scrub() also drops any string that slips into span data, as a last guard.

Each activity runs as its own Sentry transaction, continued from one trace id derived from the workflow id,
so a whole hunt (including the wait for approval and a worker restart) reads as one trace.
"""

import contextlib
import hashlib
import os
import re

AGENT = "ghost-hunt"
_on = False

SAFE = {"gen_ai.operation.name", "gen_ai.agent.name", "gen_ai.request.model", "gen_ai.response.model",
        "gen_ai.provider.name", "gen_ai.tool.name", "gen_ai.request.temperature", "gen_ai.usage.input_tokens",
        "gen_ai.usage.output_tokens", "gen_ai.usage.total_tokens", "ghost.database", "ghost.count", "ghost.kinds",
        "ghost.retries", "ghost.validated", "ghost.template_lines", "ghost.approved", "ghost.laid_to_rest",
        "temporal.workflow_id", "temporal.activity", "temporal.attempt"}


def scrub(event, hint=None):
    """Keep only our own gen_ai spans and whitelisted span data. Applied to every transaction before it is sent.
    (Auto-instrumented spans, such as a subprocess call, would carry the command line.)"""
    event["spans"] = [s for s in event.get("spans", []) if str(s.get("op", "")).startswith("gen_ai.")]
    for span in [event.get("contexts", {}).get("trace", {}), *event.get("spans", [])]:
        data = span.get("data") or {}
        for k in list(data):
            if k not in SAFE and not k.startswith(("sentry.", "thread.")):
                del data[k]
    event.pop("request", None)
    event.pop("user", None)
    event.pop("server_name", None)
    return event


def scrub_error(event, hint=None):
    """Errors keep their type and Oracle error code only: messages can name tables, users or files."""
    for ex in event.get("exception", {}).get("values", []):
        ex["value"] = " ".join(re.findall(r"\b(?:ORA|TNS|DPY|DPI)-\d{4,5}\b", ex.get("value") or "")) or "(message removed)"
        for fr in (ex.get("stacktrace") or {}).get("frames", []):
            fr.pop("vars", None)
            fr.pop("abs_path", None)  # a home directory names the user
            fr["filename"] = (fr.get("module") or "").rsplit(".", 1)[-1] or "?"
            fr.pop("context_line", None)
            fr.pop("pre_context", None)
            fr.pop("post_context", None)
    event.pop("breadcrumbs", None)
    event.pop("logentry", None)
    return scrub(event)


def init():
    global _on
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn or _on:
        return
    import sentry_sdk
    from sentry_sdk.integrations.stdlib import StdlibIntegration
    sentry_sdk.init(dsn=dsn, disabled_integrations=[StdlibIntegration()], traces_sample_rate=1.0, server_name="ghost-hunt",
                    include_local_variables=False, before_send_transaction=scrub, before_send=scrub_error, max_breadcrumbs=0,
                    environment="lab")
    _on = True


def _trace_header(workflow_id: str) -> dict:
    h = hashlib.sha256(workflow_id.encode()).hexdigest()
    return {"sentry-trace": f"{h[:32]}-{h[32:48]}-1"}


@contextlib.contextmanager
def activity(workflow_id: str, name: str, attempt: int = 1):
    """One Sentry transaction per Temporal activity, all in the workflow's trace."""
    if not _on:
        yield None
        return
    import sentry_sdk
    tx = sentry_sdk.continue_trace(_trace_header(workflow_id), op="gen_ai.invoke_agent", name=f"invoke_agent {AGENT}")
    with sentry_sdk.start_transaction(tx) as t:
        for k, v in {"gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": AGENT,
                     "temporal.workflow_id": workflow_id, "temporal.activity": name, "temporal.attempt": attempt}.items():
            t.set_data(k, v)
        try:
            yield t
        except Exception as e:
            sentry_sdk.capture_exception(e)
            raise


@contextlib.contextmanager
def span(op: str, name: str, **data):
    """A child span: op is 'gen_ai.chat' or 'gen_ai.execute_tool'."""
    if not _on:
        yield _Null()
        return
    import sentry_sdk
    with sentry_sdk.start_span(op=op, name=name) as s:
        s.set_data("gen_ai.operation.name", op.split(".", 1)[1])
        s.set_data("gen_ai.agent.name", AGENT)
        for k, v in data.items():
            s.set_data(k, v)
        yield s


class _Null:
    def set_data(self, *_):
        pass
