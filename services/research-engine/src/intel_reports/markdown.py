"""Markdown export — the printable form of a stored snapshot.

RENDERS FROM THE STORED PAYLOAD, not by regenerating. An export that re-ran the generators
would produce a document that disagrees with the report it claims to be exporting the moment
any input moved, which defeats the point of a citable snapshot.
"""
from __future__ import annotations

_STATE_MARK = {
    "OK": "",
    "UNKNOWN": "UNKNOWN",
    "UNAVAILABLE": "UNAVAILABLE",
    "STALE": "STALE",
    "CONFLICTING": "CONFLICTING",
    "NOT_APPLICABLE": "NOT_APPLICABLE",
}


def _render_value(f: dict) -> str:
    state = f.get("state", "OK")
    if state != "OK":
        return f"**{_STATE_MARK.get(state, state)}** — {f.get('reason') or 'no reason recorded'}"
    v = f.get("value")
    if isinstance(v, dict):
        return "<br>".join(f"{k}: {vv}" for k, vv in v.items())
    if isinstance(v, list):
        return "<br>".join(
            "; ".join(f"{k}: {vv}" for k, vv in item.items()) if isinstance(item, dict) else str(item)
            for item in v)
    units = f" {f['units']}" if f.get("units") else ""
    return f"{v}{units}"


def to_markdown(report) -> str:
    p = report.payload or {}
    fields = p.get("fields", {})
    lines = [
        f"# {report.report_type.replace('_', ' ').title()} — {report.subject_key}",
        "",
        "| | |",
        "|---|---|",
        f"| Report ID | {report.id} |",
        f"| Version | {report.version}"
        + (f" (supersedes {report.supersedes_id})" if report.supersedes_id else "") + " |",
        f"| Status | {report.status} |",
        f"| Market / currency | {report.market or 'n/a'} |",
        f"| Generated at | {report.generated_at} |",
        f"| Information available through | {report.cutoff_at} |",
        f"| Contract / policy version | {report.contract_version} / {report.policy_version} |",
        f"| Input fingerprint | `{report.input_fingerprint}` |",
    ]
    if report.pre_report_id:
        lines.append(f"| Frozen pre-earnings report | {report.pre_report_id} |")
    cov = report.coverage or {}
    if cov:
        lines.append(f"| Data coverage | {cov.get('ok', 0)} of {cov.get('total', 0)} "
                     f"fields resolved |")
    lines += ["", "## Fields", "", "| Field | Class | Value |", "|---|---|---|"]
    for key in sorted(fields):
        f = fields[key]
        lines.append(f"| {key.replace('_', ' ')} | {f.get('statement', '')} | {_render_value(f)} |")

    missing = {k: f for k, f in fields.items() if f.get("state") != "OK"}
    if missing:
        lines += ["", "## Not reported, and why", ""]
        for k, f in sorted(missing.items()):
            lines.append(f"- **{k.replace('_', ' ')}** — {f.get('state')}: {f.get('reason')}")
    lines += ["", "---", "",
              "Read-only research. A constructive report is not an order authorisation; "
              "position sizing requires a current portfolio snapshot and the existing risk "
              "checks, which this report does not consult."]
    return "\n".join(lines)
