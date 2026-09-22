"""MailStore: the read-only view of the mailbox.

This is the single source of truth every other component reads through. It is
deliberately the ONLY place that returns message bodies, and it never executes
anything found inside them -- bodies are data, not instructions.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

import config


@dataclass
class Message:
    id: str
    thread_id: str
    sender: str            # 'from' is a Python keyword-ish; store as sender
    to: str
    subject: str
    timestamp: str
    body: str
    unread: bool = True

    @property
    def dt(self) -> datetime:
        return datetime.fromisoformat(self.timestamp)

    @property
    def sender_domain(self) -> str:
        return self.sender.split("@")[-1].lower()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "from": self.sender,
            "to": self.to,
            "subject": self.subject,
            "timestamp": self.timestamp,
            "unread": self.unread,
            "body": self.body,
        }


class MailStore:
    def __init__(self, messages: List[Message]):
        self._messages = messages
        self._by_id: Dict[str, Message] = {m.id: m for m in messages}

    # --- construction -------------------------------------------------------
    @classmethod
    def load(cls, path=None) -> "MailStore":
        path = path or config.INBOX_PATH
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        msgs = [
            Message(
                id=m["id"],
                thread_id=m.get("thread_id", ""),
                sender=m.get("from", ""),
                to=m.get("to", ""),
                subject=m.get("subject", ""),
                timestamp=m.get("timestamp", ""),
                body=m.get("body", ""),
                unread=bool(m.get("unread", True)),
            )
            for m in raw
        ]
        # Sort chronologically so thread-walks and timelines are natural.
        msgs.sort(key=lambda m: m.timestamp)
        return cls(msgs)

    # --- access -------------------------------------------------------------
    def all(self) -> List[Message]:
        return list(self._messages)

    def get(self, msg_id: str) -> Optional[Message]:
        return self._by_id.get(msg_id)

    def exists(self, msg_id: str) -> bool:
        return msg_id in self._by_id

    def thread(self, thread_id: str) -> List[Message]:
        """All messages in a thread, chronological."""
        return sorted(
            (m for m in self._messages if m.thread_id == thread_id),
            key=lambda m: m.timestamp,
        )

    def thread_of(self, msg_id: str) -> List[Message]:
        m = self.get(msg_id)
        return self.thread(m.thread_id) if m else []

    def from_sender(self, sender: str) -> List[Message]:
        s = sender.lower()
        return [m for m in self._messages if s in m.sender.lower()]

    def sent_by_owner(self) -> List[Message]:
        return [m for m in self._messages if m.sender.lower() == config.OWNER.lower()]

    def keyword_search(self, terms: List[str], exclude_id: Optional[str] = None) -> List[Message]:
        """Cross-thread fallback retrieval: rank by number of term hits."""
        terms = [t.lower() for t in terms if t]
        scored = []
        for m in self._messages:
            if m.id == exclude_id:
                continue
            hay = (m.subject + " " + m.body).lower()
            score = sum(hay.count(t) for t in terms)
            if score:
                scored.append((score, m))
        scored.sort(key=lambda x: (-x[0], x[1].timestamp))
        return [m for _, m in scored]
