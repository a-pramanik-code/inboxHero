"""The dashboard (Part 7): exactly three panes, reproducible from a run.

  1. Pending actions -- what the system wants to do but may not do alone
     (each row: message, proposed action, why it needs a human).
  2. Flagged        -- what it refused to act on (hostile + phishing +
     ungroundable), each row: what was attempted, what the system did instead.
  3. Commitments    -- a cited calendar; conflicts called out explicitly.

Writes dashboard.json (the data) and dashboard.html (a static view built from
that JSON). Nothing here is hand-assembled -- it is produced from run outputs.
"""
from __future__ import annotations

import html
import json
from typing import List

import config
from .commitments import Commitment, find_conflicts


def build(pending: List[dict], flagged: List[dict], commitments: List[Commitment]) -> dict:
    conflicts = find_conflicts(commitments)
    data = {
        "pending_actions": pending,
        "flagged": flagged,
        "commitments": [
            {
                "title": c.title, "date": c.date, "time": c.time,
                "when_text": c.when_text, "cited": c.cited, "derived": c.derived,
            }
            for c in sorted(commitments, key=lambda c: (c.date or "9999", c.time or ""))
        ],
        "conflicts": [
            {
                "at": a.key,
                "items": [
                    {"title": a.title, "cited": a.cited},
                    {"title": b.title, "cited": b.cited},
                ],
            }
            for a, b in conflicts
        ],
    }
    config.DASHBOARD_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    config.DASHBOARD_HTML.write_text(_render_html(data), encoding="utf-8")
    return data


def _render_html(data: dict) -> str:
    def esc(x):
        return html.escape(str(x if x is not None else ""))

    rows_pending = "".join(
        f"<tr><td>{esc(p['msg_id'])}</td><td>{esc(p['action'])}</td>"
        f"<td>{esc(p['why'])}</td></tr>"
        for p in data["pending_actions"]
    ) or "<tr><td colspan=3><em>none</em></td></tr>"

    rows_flagged = "".join(
        f"<tr><td>{esc(f['msg_id'])}</td><td>{esc(f['category'])}</td>"
        f"<td>{esc(f['attempted'])}</td><td>{esc(f['action_taken'])}</td></tr>"
        for f in data["flagged"]
    ) or "<tr><td colspan=4><em>none</em></td></tr>"

    conflict_keys = {c["at"] for c in data["conflicts"]}
    rows_commit = ""
    for c in data["commitments"]:
        key = f"{c['date']}T{c['time']}" if c["date"] and c["time"] else None
        cls = "conflict" if key in conflict_keys else ""
        derived = " <span class='badge'>derived</span>" if c["derived"] else ""
        cited = ", ".join(c["cited"])
        when = f"{esc(c['date'] or '?')} {esc(c['time'] or '')}".strip()
        rows_commit += (
            f"<tr class='{cls}'><td>{when}</td>"
            f"<td>{esc(c['title'])}{derived}</td>"
            f"<td>{esc(c['when_text'])}</td><td>{esc(cited)}</td></tr>"
        )
    rows_commit = rows_commit or "<tr><td colspan=4><em>none</em></td></tr>"

    banners = "".join(
        f"<div class='alert'>&#9888; CONFLICT at {esc(c['at'])}: "
        + " vs ".join(esc(i['title']) for i in c["items"])
        + f" (cites {esc(', '.join(sum([i['cited'] for i in c['items']], [])))})</div>"
        for c in data["conflicts"]
    )

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>inboxHero dashboard</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; color:#1a1a1a; }}
 h1 {{ margin-bottom:.2rem; }} h2 {{ margin-top:2rem; }}
 table {{ border-collapse: collapse; width:100%; margin-top:.5rem; }}
 th,td {{ border:1px solid #ddd; padding:.5rem .6rem; text-align:left; vertical-align:top; font-size:.92rem; }}
 th {{ background:#f4f4f6; }}
 tr.conflict {{ background:#fff3f3; }}
 .alert {{ background:#ffe2e2; border:1px solid #ffb3b3; padding:.6rem .8rem; border-radius:6px; margin:.4rem 0; }}
 .badge {{ background:#e8f0fe; color:#1558d6; border-radius:4px; padding:0 .35rem; font-size:.72rem; }}
 .muted {{ color:#666; font-size:.85rem; }}
</style></head><body>
<h1>inboxHero &mdash; run dashboard</h1>
<p class="muted">Reproduced from the latest run. Three panes: pending actions, flagged, commitments.</p>

<h2>1 &middot; Pending actions <span class="muted">(wants to act, needs a human)</span></h2>
<table><tr><th>Message</th><th>Proposed action</th><th>Why it needs a human</th></tr>
{rows_pending}</table>

<h2>2 &middot; Flagged <span class="muted">(refused; left in place)</span></h2>
<table><tr><th>Message</th><th>Category</th><th>What was attempted</th><th>What the system did</th></tr>
{rows_flagged}</table>

<h2>3 &middot; Commitments <span class="muted">(cited calendar)</span></h2>
{banners}
<table><tr><th>When</th><th>What</th><th>As written</th><th>Cited</th></tr>
{rows_commit}</table>
</body></html>"""
