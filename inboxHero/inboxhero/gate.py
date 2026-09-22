"""The irreversible-action gate (Part 4).

Reversible vs irreversible in this system:
  reversible   : draft, label, archive, defer   (run freely, no prompt)
  irreversible : send, delete                    (must pass this gate)

'send' writes a file into outbox/ and cannot be unsent. 'delete' is treated as
irreversible because the mock store has no trash -- a deleted message is gone.
So both are gated.

This module is the ONLY path to an irreversible effect. Every send/delete goes
through `commit`, which either:
  * refuses in --dry-run (prints what it WOULD do, writes nothing), or
  * asks for explicit per-action approval (y/N) and only then acts.
Every decision is logged: what was proposed, what the human said, what happened.
This is also the Part 6 defence: a hostile email can influence a draft, but it
cannot reach a send without a human passing this gate.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import config
from .trace import Tracer

IRREVERSIBLE = {"send", "delete"}
REVERSIBLE = {"draft", "label", "archive", "defer"}


@dataclass
class Proposal:
    action: str            # 'send' | 'delete'
    msg_id: str
    to: str = ""
    subject: str = ""
    body: str = ""
    cc: Optional[List[str]] = None
    reason: str = ""


class Gate:
    def __init__(self, tracer: Tracer, dry_run: bool = True, auto_approve: bool = False,
                 cap: str = ""):
        """
        dry_run=True     : never act; show what it would do (default, safe).
        auto_approve=True : non-interactive 'yes' (for scripted demos only).
        Otherwise         : prompt y/N per action on stdin.
        """
        self.tracer = tracer
        self.dry_run = dry_run
        self.auto_approve = auto_approve
        self.cap = cap
        self.outbox_writes = 0
        self.decisions: List[dict] = []

    def _log(self, proposal: Proposal, decision: str, outcome: str) -> None:
        record = {
            "action": proposal.action,
            "msg_id": proposal.msg_id,
            "to": proposal.to,
            "cc": proposal.cc or [],
            "reason": proposal.reason,
            "decision": decision,
            "outcome": outcome,
        }
        self.decisions.append(record)
        self.tracer.emit("gate", cap=self.cap, msg_id=proposal.msg_id,
                         action=proposal.action, decision=decision, outcome=outcome,
                         to=proposal.to, reason=proposal.reason)
        self._append_action_log(record)

    def _append_action_log(self, record: dict) -> None:
        config.ensure_dirs()
        log = []
        if config.ACTION_LOG_PATH.exists():
            try:
                log = json.loads(config.ACTION_LOG_PATH.read_text(encoding="utf-8"))
            except Exception:
                log = []
        record = dict(record)
        record["ts"] = datetime.now().isoformat(timespec="seconds")
        log.append(record)
        config.ACTION_LOG_PATH.write_text(json.dumps(log, indent=2), encoding="utf-8")

    def commit(self, proposal: Proposal) -> bool:
        """Attempt an irreversible action through the gate. Returns True if it
        actually happened."""
        assert proposal.action in IRREVERSIBLE, "gate only handles irreversible actions"

        if self.dry_run:
            print(f"[DRY-RUN] would {proposal.action} to {proposal.to} "
                  f"(re: {proposal.subject}) -- {proposal.reason}")
            self._log(proposal, decision="dry-run", outcome="not performed")
            return False

        approved = self.auto_approve
        if not approved:
            ans = input(f"Approve {proposal.action} to {proposal.to} "
                        f"(re: {proposal.subject})? [y/N] ").strip().lower()
            approved = ans == "y"

        if not approved:
            self._log(proposal, decision="declined", outcome="not performed")
            return False

        if proposal.action == "send":
            self._write_outbox(proposal)
            self._log(proposal, decision="approved", outcome="written to outbox/")
            return True

        if proposal.action == "delete":
            # By policy this system never deletes; deletion is irreversible and
            # is only ever proposed by hostile mail. Refuse even if approved.
            self._log(proposal, decision="approved", outcome="refused by policy (no delete)")
            return False
        return False

    def _write_outbox(self, proposal: Proposal) -> None:
        config.ensure_dirs()
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", proposal.msg_id)
        path = config.OUTBOX_DIR / f"{safe}.json"
        payload = {
            "in_reply_to": proposal.msg_id,
            "to": proposal.to,
            "cc": proposal.cc or [],
            "subject": proposal.subject,
            "body": proposal.body,
            "sent_at": datetime.now().isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        self.outbox_writes += 1
