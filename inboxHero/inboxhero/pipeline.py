"""The orchestrator -- the role a framework's Crew + router would play.

run() executes the fixed sequence for a full inbox pass:

  scan (security)  ->  learn preferences  ->  classify every message
    ->  draft grounded replies  ->  extract commitments + conflicts
    ->  upgrade conflicting scheduling to 'escalate'  ->  build dashboard

It returns a single Result object holding every artifact, so each capability in
the manifest is just a view over one run.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import config
from . import classify, commitments as commit_mod, draft as draft_mod, rules, security
from .classify import Decision
from .draft import Draft
from .gate import Gate, Proposal
from .memory import Preference, PreferenceStore
from .store import MailStore
from .trace import Tracer


@dataclass
class Result:
    store: MailStore
    prefs: PreferenceStore
    tracer: Tracer
    decisions: Dict[str, Decision] = field(default_factory=dict)
    drafts: Dict[str, Draft] = field(default_factory=dict)
    threats: List[security.Threat] = field(default_factory=list)
    commitments: List[commit_mod.Commitment] = field(default_factory=list)
    learned: List[str] = field(default_factory=list)   # descriptions learned this run

    @property
    def flagged_ids(self):
        return {t.msg_id for t in self.threats}


def learn_preferences(store: MailStore, prefs: PreferenceStore, flagged_ids: set,
                      tracer: Tracer, cap: str = "R4") -> List[str]:
    """Record standing preferences stated in the inbox. Flagged (hostile)
    messages are never allowed to set a preference."""
    learned = []
    for msg in store.all():
        if msg.id in flagged_ids:
            continue
        det = rules.detect_preference(msg, config.OWNER)
        if det:
            kind, desc, params = det
            pref = Preference(id=msg.id, kind=kind, description=desc, params=params)
            if prefs.add(pref):
                learned.append(f"{msg.id}: {desc}")
                tracer.emit("preference_learned", cap=cap, msg_id=msg.id, kind=kind, description=desc)
    return learned


def run(tracer: Optional[Tracer] = None, cap: str = "R1") -> Result:
    config.ensure_dirs()
    tracer = tracer or Tracer()
    store = MailStore.load()
    prefs = PreferenceStore()

    res = Result(store=store, prefs=prefs, tracer=tracer)

    # 1) Security scan FIRST -- bodies are untrusted data.
    res.threats = security.scan_all(store.all())
    for t in res.threats:
        tracer.emit("refusal", cap="R5", msg_id=t.msg_id, category=t.category,
                    attempted=t.attempted, action_taken="refused; left in place; reported")
        res.decisions[t.msg_id] = Decision(t.msg_id, "flag",
                                            f"Refused ({t.category}): {t.attempted}. Left in place, reported.",
                                            tags=[t.category, "refused"])

    # 2) Learn preferences (never from flagged mail).
    res.learned = learn_preferences(store, prefs, res.flagged_ids, tracer)

    # 3) Dispose everything else: cheap rule path first, model/reasoning path
    #    only for what the rules cannot confidently handle.
    for msg in store.all():
        if msg.id in res.decisions:      # already flagged by the security scan
            continue
        noise = rules.classify_noise(msg)
        if noise:
            disp, reason = noise
            res.decisions[msg.id] = Decision(msg.id, disp, reason, by_rule=True, tags=["noise"])
            tracer.emit("decision", cap=cap, msg_id=msg.id, disposition=disp,
                        reason=reason, by_rule=True)
            continue
        d = classify.classify(msg, store, prefs, config.OWNER)
        res.decisions[msg.id] = d
        tracer.emit("decision", cap=cap, msg_id=msg.id, disposition=d.disposition,
                    reason=d.reason, by_rule=False)

    # 4) Draft replies. Grounded questions get retrieval + citations; plain
    #    scheduling messages get an acknowledgement that claims nothing.
    for mid, d in res.decisions.items():
        if d.disposition != "reply" or "draft" not in d.tags:
            continue
        msg = store.get(mid)
        cc = _cc_targets(d)
        if "grounded" in d.tags:
            dr = draft_mod.draft_reply(msg, store, cc=cc)
        else:
            dr = draft_mod.draft_ack(msg, cc=cc)
        res.drafts[mid] = dr
        if dr.cited:
            d.cited = dr.cited
        tracer.emit("draft", cap="R2", msg_id=mid, cited=dr.cited,
                    grounded=dr.grounded, method=dr.method, refused=dr.refused)

    # 5) Commitments + conflicts.
    res.commitments = commit_mod.extract(store, res.flagged_ids)
    conflicts = commit_mod.find_conflicts(res.commitments)

    # 6) Upgrade any scheduling message caught in a time conflict to 'escalate'.
    for a, b in conflicts:
        for c in (a, b):
            for cid in c.cited:
                dec = res.decisions.get(cid)
                if dec and dec.disposition in ("reply", "defer"):
                    other = b if c is a else a
                    dec.disposition = "escalate"
                    dec.reason = (f"Time conflict at {c.key}: '{a.title}' vs '{b.title}'. "
                                  f"Holding for the owner to resolve.")
                    dec.tags = list(set(dec.tags + ["conflict:time", "needs_human"]))
                    tracer.emit("decision", cap="R6", msg_id=cid, disposition="escalate",
                                reason=dec.reason)

    return res


def _cc_targets(decision: Decision) -> List[str]:
    return [t.split("cc:", 1)[1] for t in decision.tags if t.startswith("cc:") and t.split("cc:", 1)[1]]


# --- Views used by the dashboard and the run summary ------------------------

def pending_actions(res: Result) -> List[dict]:
    rows = []
    for mid, d in sorted(res.decisions.items()):
        if d.disposition == "escalate":
            rows.append({"msg_id": mid, "action": "hold / escalate", "why": d.reason})
        elif d.disposition == "reply":
            dr = res.drafts.get(mid)
            if dr and not dr.refused and dr.body:
                cc = f" (cc {', '.join(dr.cc)})" if dr.cc else ""
                rows.append({"msg_id": mid, "action": f"send reply draft{cc}",
                             "why": "Sending is irreversible; per-action approval required (Part 4)."})
    return rows


def flagged_rows(res: Result) -> List[dict]:
    rows = []
    for t in res.threats:
        rows.append({"msg_id": t.msg_id, "category": t.category,
                     "attempted": t.attempted,
                     "action_taken": "Refused; not acted on; left in place; reported."})
    # Ungroundable drafts are also 'flagged' (could not ground).
    for mid, dr in res.drafts.items():
        if dr.refused:
            rows.append({"msg_id": mid, "category": "ungrounded",
                         "attempted": "reply requested but no supporting message found",
                         "action_taken": "Drafted nothing; deferred to owner."})
    return rows


def rule_handled_count(res: Result) -> int:
    return sum(1 for d in res.decisions.values() if d.by_rule)
