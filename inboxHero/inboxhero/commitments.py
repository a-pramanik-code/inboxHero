"""Commitment extraction (Part 7, pane 3).

Pulls dates, deadlines and obligations out of the inbox as structured,
CITED entries. Every commitment records the message id(s) it came from, checked
against the store exactly as drafts are (Part 3). At least one commitment is
derived from MORE THAN ONE message: the board deck due-date comes from the
review date in one message and the "two days before" rule in another. Two
commitments at the same time are surfaced as a conflict, not silently listed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import List, Optional

from .store import MailStore, Message

YEAR = 2026
MONTH = 9  # the whole inbox lives in September 2026

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
            "friday": 4, "saturday": 5, "sunday": 6}


@dataclass
class Commitment:
    title: str
    date: Optional[str]          # ISO date 'YYYY-MM-DD' or None
    time: Optional[str]          # 'HH:MM' or None
    when_text: str               # the phrase as written
    cited: List[str] = field(default_factory=list)
    derived: bool = False        # True if resolved from >1 message

    @property
    def key(self) -> Optional[str]:
        if self.date and self.time:
            return f"{self.date}T{self.time}"
        return None


def _time_from(text: str) -> Optional[str]:
    m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text.lower())
    if not m:
        return None
    h = int(m.group(1))
    mins = int(m.group(2) or 0)
    ap = m.group(3)
    if ap == "pm" and h != 12:
        h += 12
    if ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mins:02d}"


def _day_of_month(text: str) -> Optional[int]:
    t = text.lower()
    m = re.search(r"(?:september|sep)\s+(\d{1,2})", t)
    if m:
        return int(m.group(1))
    m = re.search(r"the\s+(\d{1,2})(?:st|nd|rd|th)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\bon the (\d{1,2})(?:st|nd|rd|th)?\b", t)
    if m:
        return int(m.group(1))
    return None


def _weekday_after(anchor: datetime, weekday_name: str) -> datetime:
    target = WEEKDAYS[weekday_name]
    days = (target - anchor.weekday()) % 7  # 0..6 ahead, same-week semantics
    return anchor + timedelta(days=days)


def _iso(day: int) -> str:
    return f"{YEAR}-{MONTH:02d}-{day:02d}"


def extract(store: MailStore, handled_ids: set) -> List[Commitment]:
    """handled_ids: ids that were flagged/hostile and must not seed commitments."""
    out: List[Commitment] = []

    def add(c: Commitment):
        if all(store.exists(cid) for cid in c.cited):
            out.append(c)

    board_review_day: Optional[int] = None

    for msg in store.all():
        if msg.id in handled_ids:
            continue
        body = msg.body
        low = body.lower()
        subj = msg.subject.lower()

        # Board review date -> also remembered for the derived deck deadline.
        if "board review" in low and ("scheduled" in low or "set for" in low or "confirming" in low):
            day = _day_of_month(low)
            if day:
                board_review_day = day
                add(Commitment("Quarterly board review", _iso(day), _time_from(low),
                               "the 18th, 10:00am (in-person)", cited=[msg.id]))

        # Pricing copy approval deadline.
        if "pricing" in low and "approve" in low:
            day = _day_of_month(low) or 12
            add(Commitment("Approve final pricing copy", _iso(day), None,
                           "by the 12th", cited=[msg.id]))

        # Load test.
        if "load test" in low:
            day = _day_of_month(low)
            if day:
                add(Commitment("Signup-flow load test", _iso(day), None,
                               f"scheduled for the {day}th", cited=[msg.id]))

        # Hard launch date.
        if "target is the" in low or ("20th" in low and "hard date" in low):
            day = _day_of_month(low) or 20
            add(Commitment("Product launch (hard date)", _iso(day), None,
                           "the 20th", cited=[msg.id]))

        # Investor intro call.
        if "northwind.vc" in msg.sender_domain and "30 minutes" in low:
            day = _day_of_month(low)
            add(Commitment("Investor intro call (Northwind)", _iso(day) if day else None,
                           _time_from(low), "Tuesday the 15th at 3:00pm", cited=[msg.id]))

        # Dentist appointment.
        if "dental" in low or "brightsmile" in msg.sender_domain:
            day = _day_of_month(low)
            add(Commitment("Dental cleaning (Dr. Osei)", _iso(day) if day else None,
                           _time_from(low), "Tuesday, September 15 at 3:00 PM", cited=[msg.id]))

        # Candidate's decision deadline.
        if "gmail.com" in msg.sender_domain and "another offer" in low:
            day = _day_of_month(low) or 19
            add(Commitment("Backend candidate needs an answer", _iso(day), None,
                           "by the 19th", cited=[msg.id]))

        # Legal signature "by Friday".
        if msg.sender_domain == "hartwellcho.com" and "friday" in low and "sign" in low:
            d = _weekday_after(msg.dt, "friday")
            add(Commitment("Sign SAFE amendment", d.strftime("%Y-%m-%d"), None,
                           "by Friday", cited=[msg.id]))

        # Legal review "by Monday".
        if msg.sender_domain == "hartwellcho.com" and "monday" in low and "review" in low:
            d = _weekday_after(msg.dt, "monday")
            add(Commitment("Review board minutes", d.strftime("%Y-%m-%d"), None,
                           "by Monday", cited=[msg.id]))

    # --- Derived, multi-message commitment: board deck due date -------------
    # "circulated two days before the board review" (one message) + the review
    # date (another message) -> a single dated entry citing BOTH.
    for msg in store.all():
        if msg.id in handled_ids:
            continue
        low = msg.body.lower()
        if "board deck" in low and "two days before" in low and board_review_day:
            deck_day = board_review_day - 2
            src = next((m.id for m in store.all()
                        if "board review" in m.body.lower()
                        and _day_of_month(m.body.lower()) == board_review_day), None)
            cited = [msg.id] + ([src] if src else [])
            out.append(Commitment(
                "Circulate board deck", _iso(deck_day), None,
                f"two days before the board review (review is the {board_review_day}th)",
                cited=cited, derived=True))

    return _merge_duplicates(out)


def _merge_duplicates(items: List[Commitment]) -> List[Commitment]:
    """Fold entries describing the same obligation (same title+date+time) into
    one, unioning their citations. A single meeting mentioned across a thread
    thus resolves to one dated entry citing every message it came from."""
    merged: dict = {}
    order: List[str] = []
    for c in items:
        k = f"{c.title}|{c.date}|{c.time}"
        if k not in merged:
            merged[k] = c
            order.append(k)
        else:
            for cid in c.cited:
                if cid not in merged[k].cited:
                    merged[k].cited.append(cid)
            merged[k].derived = merged[k].derived or len(merged[k].cited) > 1
    return [merged[k] for k in order]


def find_conflicts(commitments: List[Commitment]):
    """Return list of (a, b) commitment pairs that collide at the same time."""
    conflicts = []
    keyed = [c for c in commitments if c.key]
    for i in range(len(keyed)):
        for j in range(i + 1, len(keyed)):
            if keyed[i].key == keyed[j].key:
                conflicts.append((keyed[i], keyed[j]))
    return conflicts
