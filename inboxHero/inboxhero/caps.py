"""The Part 8 capabilities (X1-X4), each runnable on its own and judgeable.

  X1 Follow-up tracking      (tier B)
  X2 Thread summariser       (tier B)
  X3 Sender lookup           (tier A)
  X4 Meeting conflict resolver (tier C)
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import config
from . import draft as draft_mod, rules
from .store import MailStore, Message
from .trace import Tracer


# --- X1: Follow-up tracking (tier B) ----------------------------------------

def follow_ups(store: MailStore, tracer: Tracer, min_days: int = 3) -> List[dict]:
    """Owner-sent messages nobody has answered in >= min_days, with a chase draft."""
    now = datetime.fromisoformat(config.REFERENCE_NOW)
    out = []
    for msg in store.sent_by_owner():
        # Skip owner notes-to-self (preferences) -- not awaiting a reply.
        if msg.sender.lower() == msg.to.lower():
            continue
        thread = store.thread_of(msg.id)
        answered = any(m.timestamp > msg.timestamp and m.sender.lower() != config.OWNER.lower()
                       for m in thread)
        if answered:
            continue
        days = (now - msg.dt).days
        if days < min_days:
            continue
        chase = (f"Hi,\n\nJust following up on my note below (\"{msg.subject}\") "
                 f"from {msg.dt:%b %d} -- any update when you get a moment?\n\n-- Sam")
        out.append({"message_id": msg.id, "to": msg.to, "subject": msg.subject,
                    "days_waiting": days, "draft": chase})
        tracer.emit("follow_up", cap="X1", msg_id=msg.id, days_waiting=days, to=msg.to)
    return out


# --- X2: Thread summariser -> open question (tier B) ------------------------

def summarise_thread(store: MailStore, thread_id: str, tracer: Tracer) -> dict:
    msgs = store.thread(thread_id)
    if not msgs:
        return {"thread_id": thread_id, "error": "no such thread"}

    timeline = [{"id": m.id, "from": m.sender, "gist": _gist(m.body)} for m in msgs]
    open_q = _open_question(msgs)
    result = {
        "thread_id": thread_id,
        "messages": [m.id for m in msgs],
        "participants": sorted({m.sender for m in msgs}),
        "timeline": timeline,
        "open_question": open_q,
    }
    tracer.emit("thread_summary", cap="X2", msg_id=msgs[-1].id,
                thread_id=thread_id, open_question=open_q.get("question", ""))
    return result


def _gist(body: str, n: int = 90) -> str:
    s = " ".join(body.split())
    return s[:n] + ("..." if len(s) > n else "")


def _open_question(msgs: List[Message]) -> dict:
    """Find the message that names an unresolved action for the owner."""
    for m in msgs:
        low = m.body.lower()
        if "sam" in low and ("approve" in low or "can you" in low) and "?" in m.body:
            return {"from": m.id,
                    "question": _gist(m.body, 160),
                    "cited": [m.id]}
    # fallback: last message
    last = msgs[-1]
    return {"from": last.id, "question": _gist(last.body, 160), "cited": [last.id]}


# --- X3: Sender lookup (tier A) ---------------------------------------------

def sender_lookup(store: MailStore, sender: str, tracer: Tracer, unread_only: bool = False) -> List[dict]:
    msgs = store.from_sender(sender)
    if unread_only:
        msgs = [m for m in msgs if m.unread]
    out = [{"id": m.id, "subject": m.subject, "timestamp": m.timestamp, "unread": m.unread}
           for m in msgs]
    tracer.emit("sender_lookup", cap="X3", msg_id="", sender=sender, count=len(out))
    return out


# --- X4: Meeting conflict resolver (tier C) ---------------------------------

def resolve_meeting_conflicts(store: MailStore, prefs, tracer: Tracer) -> List[dict]:
    """Detect scheduling requests that violate the 'no meetings before N' rule,
    propose alternatives, and hold a reply for approval (does not send)."""
    import re
    min_hour = prefs.earliest_meeting_hour()
    out = []
    if not min_hour:
        return out
    for msg in store.all():
        low = msg.body.lower()
        if not any(k in low for k in ("meeting", "call", "slot", "before markets", "could you do")):
            continue
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", low)
        if not m:
            continue
        hour = int(m.group(1))
        if m.group(3) == "pm" and hour != 12:
            hour += 12
        if m.group(3) == "am" and hour == 12:
            hour = 0
        if hour < min_hour:
            alts = [f"{min_hour}:00", f"{min_hour + 1}:00", f"{min_hour + 2}:00"]
            item = {
                "message_id": msg.id,
                "from": msg.sender,
                "proposed_time": f"{hour:02d}:00",
                "violates": f"no meetings before {min_hour}:00",
                "proposed_alternatives": alts,
                "held_for_approval": True,
                "draft": (f"Hi,\n\nThanks for the note. I don't take meetings before "
                          f"{min_hour}:00 -- could we do {alts[0]} or later instead? "
                          f"Happy to lock one in.\n\n-- Sam"),
            }
            out.append(item)
            tracer.emit("conflict_resolved", cap="X4", msg_id=msg.id,
                        proposed_time=item["proposed_time"], alternatives=alts,
                        held_for_approval=True)
    return out
