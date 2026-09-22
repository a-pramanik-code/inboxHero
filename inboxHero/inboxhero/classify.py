"""Disposition classifier: the reasoning path.

Every message that survives the security scanner and the noise rules gets
exactly one disposition here, with a stated reason. This layer is where a
language model *would* be used in production; it is written as deterministic
heuristics so the manifest runs offline and reproducibly, and an LLM can be
layered on top through llm.py without changing the contract.

Disposition vocabulary (defined once, used everywhere):
  reply     -- a response is owed; the system drafts one (sending is gated)
  archive   -- no action owed; filed away (rule-path noise lands here)
  defer     -- needs the owner's attention/decision later; kept on the radar
  escalate  -- needs a human decision now (money, legal, signatures, conflicts)
  flag      -- refused; hostile or phishing, left in place and reported
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import rules
from .store import MailStore, Message

LEGAL_DOMAINS = {"hartwellcho.com"}
SCHEDULING_HINTS = ("meeting", "call", "1:1", "demo", "coffee", "slot",
                    "at 9", "at 2", "at 3", "am?", "pm", "tuesday", "wednesday",
                    "monday", "thursday", "friday")


@dataclass
class Decision:
    msg_id: str
    disposition: str
    reason: str
    by_rule: bool = False                 # True if no model reasoning was needed
    tags: List[str] = field(default_factory=list)
    cited: List[str] = field(default_factory=list)


def _has_later_reply_from_other(msg: Message, store: MailStore, owner: str) -> bool:
    """Did anyone reply after an owner-sent message in the same thread?"""
    for other in store.thread_of(msg.id):
        if other.timestamp > msg.timestamp and other.sender.lower() != owner.lower():
            return True
    return False


def _looks_like_scheduling(msg: Message) -> bool:
    text = (msg.subject + " " + msg.body).lower()
    keys = ("meeting", "1:1", " demo", "coffee", "reschedule", "slot",
            "weekly call", "intro call", "our call")
    return any(k in text for k in keys)


def _is_proposal(msg: Message) -> bool:
    """True only when the message actually PROPOSES a time to the owner (rather
    than confirming a fixed event). Keeps board-review confirmations out of the
    'no early meetings' path."""
    text = msg.body.lower()
    phrases = ("does tuesday", "does that", "does monday", "does wednesday",
               "work on your side", "work for you", "can we move", "could you do",
               "want to grab", "grab coffee", "reply to confirm", "does the",
               "before markets", "can we do", "how about")
    return any(p in text for p in phrases)


def _answerable_question(msg: Message) -> bool:
    text = msg.body.lower()
    return ("?" in msg.body) and any(
        k in text for k in ("resend", "what", "which", "can you send", "the url", "the link", "creds")
    )


REQUEST_VERBS = ("can you", "could you", "would you", "please", "need you",
                 "approve", "review", "sign", "confirm", "let me know", "?")


def _is_request(msg: Message) -> bool:
    text = (msg.subject + " " + msg.body).lower()
    return any(v in text for v in REQUEST_VERBS)


def classify(msg: Message, store: MailStore, prefs, owner: str) -> Decision:
    domain = msg.sender_domain
    owner_domain = owner.split("@")[-1]
    internal = domain.endswith(owner_domain)
    body = msg.body.lower()
    subj = msg.subject.lower()

    # 1) A stated standing preference -> recorded to memory, filed.
    if rules.detect_preference(msg, owner):
        return Decision(msg.id, "archive",
                        "Standing preference; recorded to memory (see R4). No reply owed.",
                        tags=["preference"])

    # 2) Owner's own outbound mail sitting in the box.
    if rules.is_owner(msg, owner):
        if not _has_later_reply_from_other(msg, store, owner):
            return Decision(msg.id, "defer",
                            "Owner-sent, no reply yet; tracked for follow-up (see X1).",
                            tags=["awaiting_reply", "follow_up"])
        return Decision(msg.id, "archive", "Owner-sent message already answered in-thread.")

    # 3) Legal correspondence -> a human must decide; never auto-sign.
    if domain in LEGAL_DOMAINS or any(k in subj for k in ("safe amendment", "signature", "board minutes", "ip assignment")):
        tags = ["legal", "needs_human"]
        cc = prefs.cc_rule_for(domain) if prefs else None
        if cc:
            tags.append("cc:" + cc.params.get("cc", ""))
        reason = "Legal matter requiring the owner's signature/decision; escalated (never auto-signed)."
        if cc:
            reason += f" Preference applied: CC {cc.params.get('cc')}."
        return Decision(msg.id, "escalate", reason, tags=tags)

    # 4) Ambiguous request with no resolvable referent -> ask, don't guess.
    if _is_ambiguous(msg):
        return Decision(msg.id, "defer",
                        "Ambiguous request with no groundable referent in the inbox; ask the owner rather than guess. Nothing drafted.",
                        tags=["ambiguous", "needs_human"])

    # 5) A genuine scheduling PROPOSAL -> may collide with a stated preference.
    if _looks_like_scheduling(msg) and _is_proposal(msg):
        min_hour = prefs.earliest_meeting_hour() if prefs else None
        early = _proposes_early_meeting(msg, min_hour)
        if early is not None:
            return Decision(
                msg.id, "escalate",
                f"Scheduling request at {early}:00 violates the 'no meetings before "
                f"{min_hour}:00' preference; holding a counter-proposal for approval (see X4).",
                tags=["scheduling", "conflict:preference", "needs_human"],
            )
        return Decision(msg.id, "reply",
                        "Scheduling request within allowed hours; drafting an acknowledgement (send gated).",
                        tags=["scheduling", "draft"])

    # 6) A question answerable from an EARLIER message in-thread -> grounded reply.
    has_earlier = any(o.timestamp < msg.timestamp for o in store.thread_of(msg.id))
    if has_earlier and _answerable_question(msg):
        return Decision(msg.id, "reply",
                        "Answerable from an earlier message in the mail store; drafting a grounded reply (send gated).",
                        tags=["draft", "grounded"])

    # 7) External press query / recruiting -> owner answers personally.
    if not internal and (any(k in body for k in ("launch coverage", "coverage", "on deadline")) or "techbrief" in domain):
        return Decision(msg.id, "defer", "External press query; owner should answer personally. Kept on the radar.",
                        tags=["press", "needs_human"])
    if "gmail.com" in domain and any(k in body for k in ("role", "offer", "timeline", "next steps")):
        return Decision(msg.id, "defer", "Candidate follow-up with a stated deadline; owner decision needed.",
                        tags=["hiring", "needs_human", "deadline"])

    # 8) Internal mail: a request the owner must act on -> defer; else FYI -> archive.
    if internal:
        if _is_request(msg):
            return Decision(msg.id, "defer",
                            "Internal request the owner must action; kept on the radar (no auto-reply).",
                            tags=["internal", "needs_human"])
        return Decision(msg.id, "archive", "Internal status update / FYI; no action owed.", tags=["fyi"])

    # 9) External booking / vendor ask -> defer for the owner's confirmation.
    return Decision(msg.id, "defer",
                    "External request needing the owner's confirmation; kept for review.",
                    tags=["external", "needs_human"])


def _proposes_early_meeting(msg: Message, min_hour: Optional[int]) -> Optional[int]:
    """Return the proposed hour if the message proposes a meeting before min_hour."""
    if not min_hour:
        return None
    import re
    text = msg.body.lower()
    # Match "9:00am", "9am", "at 9"
    for m in re.finditer(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text):
        hour = int(m.group(1))
        ampm = m.group(3)
        if ampm == "pm" and hour != 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if hour < min_hour:
            return hour
    return None


def _is_ambiguous(msg: Message) -> bool:
    text = (msg.subject + " " + msg.body).lower()
    vague = ("the thing", "that thing", "sort out that")
    return any(v in text for v in vague)
