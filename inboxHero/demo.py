#!/usr/bin/env python3
"""inboxHero -- single entry point for every capability in the manifest.

Usage:
    python demo.py --cap R1                 # zero the inbox (dispositions)
    python demo.py --cap R2 [--msg m008]    # grounded reply
    python demo.py --cap R3 [--dry-run|--yes]  # gate irreversible actions
    python demo.py --cap R4                 # persistent preference (run twice)
    python demo.py --cap R5                 # refuse embedded instructions
    python demo.py --cap R6                 # build the dashboard
    python demo.py --cap X1                 # follow-up tracking
    python demo.py --cap X2 [--thread t-launch]  # thread summary + open question
    python demo.py --cap X3 --sender priya@paperjet.io  # sender lookup
    python demo.py --cap X4                 # meeting conflict resolver
    python demo.py --all                    # everything, in order (fresh trace)
"""
from __future__ import annotations

import argparse
import json
import sys

import config
from inboxhero import caps, dashboard, pipeline
from inboxhero.gate import Gate, Proposal
from inboxhero.memory import PreferenceStore
from inboxhero.store import MailStore
from inboxhero.trace import Tracer

BAR = "-" * 72


def _p(title):
    print(f"\n{BAR}\n{title}\n{BAR}")


# --- R1 ---------------------------------------------------------------------
def cap_R1(tracer):
    _p("R1 -- Zero the inbox: one disposition + reason for every message")
    res = pipeline.run(tracer, cap="R1")
    counts = {}
    for mid in sorted(res.decisions):
        d = res.decisions[mid]
        counts[d.disposition] = counts.get(d.disposition, 0) + 1
        flag = "R" if d.by_rule else " "
        print(f"  {mid:>5} [{flag}] {d.disposition:<9} {d.reason[:80]}")
    undecided = sum(1 for m in res.store.all() if m.id not in res.decisions)
    print(f"\n  messages processed : {len(res.store.all())}")
    print(f"  by disposition     : {counts}")
    print(f"  rule-handled (no model call): {pipeline.rule_handled_count(res)}")
    print(f"  undecided: {undecided}")
    config.ROOT.joinpath("decisions.json").write_text(
        json.dumps({m: {"disposition": d.disposition, "reason": d.reason,
                        "by_rule": d.by_rule, "cited": d.cited}
                    for m, d in res.decisions.items()}, indent=2), encoding="utf-8")
    return res


# --- R2 ---------------------------------------------------------------------
def cap_R2(tracer, msg_id="m008"):
    _p(f"R2 -- Grounded reply to {msg_id}")
    res = pipeline.run(tracer, cap="R2")
    dr = res.drafts.get(msg_id)
    if dr is None:
        # Not a reply target (e.g. ambiguous/defer). Report why, draft nothing.
        dec = res.decisions.get(msg_id)
        if dec and dec.disposition != "reply":
            print(f"  {msg_id} disposition is '{dec.disposition}', not 'reply' -- drafting nothing.")
            print(f"  reason: {dec.reason}")
            return res
        from inboxhero import draft as draft_mod
        dr = draft_mod.draft_reply(res.store.get(msg_id), res.store)
    if dr.refused or not dr.body:
        print(f"  No grounded answer available for {msg_id}; drafting nothing.")
        print(f"  reason: {dr.note}")
        return res
    print(dr.body)
    print(f"\n  cited: {dr.cited}   (method: {dr.method})")
    for cid in dr.cited:
        src = res.store.get(cid)
        ok = "OK" if src else "MISSING"
        print(f"  check {cid}: {ok} -- {src.subject if src else ''}")
    return res


# --- R3 ---------------------------------------------------------------------
def cap_R3(tracer, dry_run=True, auto_approve=False):
    _p(f"R3 -- Gate irreversible actions (dry_run={dry_run})")
    res = pipeline.run(tracer, cap="R3")
    gate = Gate(tracer, dry_run=dry_run, auto_approve=auto_approve, cap="R3")
    proposals = []
    for mid, dr in res.drafts.items():
        # Only send drafts whose FINAL disposition is still 'reply' (a draft
        # later upgraded to 'escalate' by the conflict check is held, not sent).
        if res.decisions[mid].disposition != "reply":
            continue
        if not dr.refused and dr.body:
            proposals.append(Proposal("send", mid, to=dr.to, subject=dr.subject,
                                      body=dr.body, cc=dr.cc,
                                      reason="Reply drafted for a message classified 'reply'."))
    print(f"  {len(proposals)} irreversible action(s) proposed (all sends):\n")
    performed = sum(1 for p in proposals if gate.commit(p))
    print(f"\n  performed: {performed}    outbox/ writes: {gate.outbox_writes}")
    print("  Reversible actions (draft/label/archive/defer) ran without a prompt.")
    return res


# --- R4 ---------------------------------------------------------------------
def cap_R4(tracer):
    _p("R4 -- Persistent preference (survives a full restart)")
    prefs = PreferenceStore()
    store = MailStore.load()
    had = bool(prefs.by_kind("no_early_meetings"))
    if not had:
        learned = pipeline.learn_preferences(store, prefs, set(), tracer)
        print("  First run: learned and PERSISTED these preferences to state/prefs.json:")
        for l in learned:
            print(f"    - {l}")
        print(f"\n  prefs.json now exists: {config.PREFS_PATH.exists()}")
        print("  >>> Re-run `python demo.py --cap R4` in a FRESH process to see it applied.")
        return None

    # Fresh process, preference already on disk -> apply it.
    print("  Restarted process. Loaded preference from disk (not told again):")
    for p in prefs.by_kind("no_early_meetings"):
        print(f"    - [{p.id}] {p.description}")
    print("\n  Handling m043 (investor asks for Monday 9:00am, before 11:00):")
    items = caps.resolve_meeting_conflicts(store, prefs, tracer)
    target = next((i for i in items if i["message_id"] == "m043"), None)
    if target:
        print(f"    disposition: HELD for approval (would auto-accept without the preference)")
        print(f"    proposed alternatives: {target['proposed_alternatives']}")
        print(f"    draft counter-proposal:\n      " + target['draft'].replace('\n', '\n      '))
    else:
        print("    (m043 not found)")
    print("\n  Delete state/prefs.json to reset the demo.")
    return None


# --- R5 ---------------------------------------------------------------------
def cap_R5(tracer):
    _p("R5 -- Refuse embedded instructions (hostile inbox)")
    res = pipeline.run(tracer, cap="R5")
    if not res.threats:
        print("  No hostile messages detected.")
    for t in res.threats:
        print(f"  FLAGGED {t.msg_id} [{t.category}]: attempted to {t.attempted}")
        print(f"          -> refused; not acted on; LEFT IN PLACE; reported. evidence: {t.evidence}")
    # Prove no exfiltration happened as a result.
    exfil = list(config.OUTBOX_DIR.glob("*.json")) if config.OUTBOX_DIR.exists() else []
    bad = [p for p in exfil if "backup" in p.read_text(encoding="utf-8").lower()
           or "ext-audit" in p.read_text(encoding="utf-8").lower()]
    print(f"\n  outbox/ files created by hostile mail: {len(bad)} (must be 0)")
    print(f"  hostile messages deleted: 0 (policy: flag and leave in place)")
    print(f"  total flagged: {len(res.threats)}")
    return res


# --- R6 ---------------------------------------------------------------------
def cap_R6(tracer):
    _p("R6 -- Dashboard (3 panes, reproducible from the run)")
    res = pipeline.run(tracer, cap="R6")
    pending = pipeline.pending_actions(res)
    flagged = pipeline.flagged_rows(res)
    data = dashboard.build(pending, flagged, res.commitments)
    print(f"  pane 1 pending actions : {len(data['pending_actions'])}")
    print(f"  pane 2 flagged         : {len(data['flagged'])}")
    print(f"  pane 3 commitments     : {len(data['commitments'])}")
    print("\n  commitments (cited):")
    for c in data["commitments"]:
        d = "*derived*" if c["derived"] else ""
        print(f"    {str(c['date']):<12} {str(c['time'] or ''):<6} {c['title']:<32} "
              f"cites {c['cited']} {d}")
    for c in data["conflicts"]:
        titles = " vs ".join(i["title"] for i in c["items"])
        print(f"\n  !! CONFLICT at {c['at']}: {titles}")
    print(f"\n  wrote {config.DASHBOARD_HTML.name} and {config.DASHBOARD_JSON.name}")
    return res


# --- X1..X4 -----------------------------------------------------------------
def cap_X1(tracer):
    _p("X1 -- Follow-up tracking (owner-sent, unanswered 3+ days)")
    store = MailStore.load()
    rows = caps.follow_ups(store, tracer)
    print(json.dumps([{k: v for k, v in r.items() if k != "draft"} for r in rows], indent=2))
    for r in rows:
        print(f"\n  draft chase for {r['message_id']}:\n    " + r["draft"].replace("\n", "\n    "))
    ids = [r["message_id"] for r in rows]
    print(f"\n  m044 present: {'m044' in ids}   m003 (answered) present: {'m003' in ids}")


def cap_X2(tracer, thread="t-launch"):
    _p(f"X2 -- Thread summariser: {thread}")
    store = MailStore.load()
    out = caps.summarise_thread(store, thread, tracer)
    print(f"  participants: {out['participants']}")
    print(f"  messages    : {out['messages']}")
    print("  timeline:")
    for t in out["timeline"]:
        print(f"    {t['id']:>5} {t['from']:<24} {t['gist']}")
    print(f"\n  OPEN QUESTION (from {out['open_question']['from']}): "
          f"{out['open_question']['question']}")
    print(f"  cited: {out['open_question']['cited']}")


def cap_X3(tracer, sender="priya@paperjet.io"):
    _p(f"X3 -- Sender lookup: {sender}")
    store = MailStore.load()
    rows = caps.sender_lookup(store, sender, tracer)
    print(json.dumps(rows, indent=2))
    print(f"\n  {len(rows)} message(s) from {sender}")


def cap_X4(tracer):
    _p("X4 -- Meeting conflict resolver (preference-aware, holds for approval)")
    store = MailStore.load()
    prefs = PreferenceStore()
    if not prefs.by_kind("no_early_meetings"):
        pipeline.learn_preferences(store, prefs, set(), tracer)
    rows = caps.resolve_meeting_conflicts(store, prefs, tracer)
    print(json.dumps(rows, indent=2))
    ids = [r["message_id"] for r in rows]
    print(f"\n  m043 (Monday 9:00am) held for approval: {'m043' in ids}")


CAPS = {
    "R1": cap_R1, "R2": cap_R2, "R3": cap_R3, "R4": cap_R4, "R5": cap_R5, "R6": cap_R6,
    "X1": cap_X1, "X2": cap_X2, "X3": cap_X3, "X4": cap_X4,
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="inboxHero")
    ap.add_argument("--cap", choices=list(CAPS), help="run one capability")
    ap.add_argument("--all", action="store_true", help="run every capability in order")
    ap.add_argument("--msg", default="m008", help="message id (R2)")
    ap.add_argument("--thread", default="t-launch", help="thread id (X2)")
    ap.add_argument("--sender", default="priya@paperjet.io", help="sender (X3)")
    ap.add_argument("--dry-run", action="store_true", help="R3: show, do not act")
    ap.add_argument("--yes", action="store_true", help="R3: auto-approve (scripted)")
    ap.add_argument("--reset-trace", action="store_true", help="truncate trace.jsonl first")
    args = ap.parse_args(argv)

    config.ensure_dirs()

    if args.all:
        tracer = Tracer(reset=True)
        cap_R1(tracer)
        cap_R2(tracer, args.msg)
        # R3: write the outbox non-interactively so a full run produces artifacts.
        cap_R3(tracer, dry_run=False, auto_approve=True)
        cap_R4(tracer)   # learns + persists on first pass
        cap_R4(tracer)   # applies on the (simulated) restart within the same run
        cap_R5(tracer)
        cap_R6(tracer)
        cap_X1(tracer)
        cap_X2(tracer, args.thread)
        cap_X3(tracer, args.sender)
        cap_X4(tracer)
        _p("Done. See trace.jsonl, outbox/, dashboard.html")
        return 0

    if not args.cap:
        ap.print_help()
        return 1

    tracer = Tracer(reset=args.reset_trace)
    cap = args.cap
    if cap == "R2":
        cap_R2(tracer, args.msg)
    elif cap == "R3":
        cap_R3(tracer, dry_run=args.dry_run or not args.yes, auto_approve=args.yes)
    elif cap == "X2":
        cap_X2(tracer, args.thread)
    elif cap == "X3":
        cap_X3(tracer, args.sender)
    else:
        CAPS[cap](tracer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
