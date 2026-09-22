"""Retrieval for grounded replies (Part 3).

Primary method: thread-walk. An inbox already carries its own structure in
thread_id, so walking the thread is cheaper and more precise than embeddings
for this task. Keyword search is the cross-thread fallback.

Every fact used in a draft must trace to a real message id in the store. The
citation check here is what stops the system citing a message it never read or
inventing a detail found nowhere in the inbox.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .store import MailStore, Message

# A staging/broker URL, an amount, a date -- concrete facts worth grounding on.
URL_RE = re.compile(r"[a-z]+://[^\s]+", re.I)


@dataclass
class Grounding:
    answer_fact: Optional[str]     # the concrete fact to reuse, or None
    cited: List[str]               # message ids the fact came from
    method: str                    # 'thread-walk' | 'keyword'
    note: str = ""


def _earlier_in_thread(msg: Message, store: MailStore) -> List[Message]:
    return [m for m in store.thread_of(msg.id) if m.timestamp < msg.timestamp]


def find_grounding(msg: Message, store: MailStore) -> Grounding:
    """Find the earlier message that answers `msg`, thread-walk first."""
    earlier = _earlier_in_thread(msg, store)

    # If the message asks to "resend the URL/creds", find the earlier message
    # that actually contains a URL/credential.
    wants_url = any(k in msg.body.lower() for k in ("url", "creds", "link", "resend"))
    if wants_url:
        for prev in reversed(earlier):          # most recent earlier first
            m = URL_RE.search(prev.body)
            if m:
                return Grounding(answer_fact=m.group(0), cited=[prev.id], method="thread-walk",
                                 note="URL located by walking the thread.")

    # Otherwise, ground on the most substantive earlier message if any.
    if earlier:
        prev = earlier[-1]
        return Grounding(answer_fact=None, cited=[prev.id], method="thread-walk",
                         note="Nearest earlier message in thread.")

    # Cross-thread keyword fallback.
    terms = [w for w in re.findall(r"[a-zA-Z]{4,}", msg.subject)]
    hits = store.keyword_search(terms, exclude_id=msg.id)
    if hits:
        return Grounding(answer_fact=None, cited=[hits[0].id], method="keyword",
                         note="Cross-thread keyword match.")

    return Grounding(answer_fact=None, cited=[], method="thread-walk",
                     note="No supporting message found in the inbox.")


def verify_citations(cited: List[str], store: MailStore) -> bool:
    """Every cited id must exist in the mail store."""
    return all(store.exists(cid) for cid in cited)
