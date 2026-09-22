"""Reply drafting (Part 3).

A draft is reversible: it can be rewritten and it is never sent without passing
the Part 4 gate. Drafts are grounded -- they reuse a concrete fact from a cited
earlier message rather than inventing one. If nothing grounds the answer, we
draft nothing and say so.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from . import llm
from .retrieval import Grounding, find_grounding, verify_citations
from .store import MailStore, Message


@dataclass
class Draft:
    msg_id: str
    to: str
    subject: str
    body: str
    cited: List[str] = field(default_factory=list)
    grounded: bool = False
    method: str = ""
    cc: List[str] = field(default_factory=list)
    refused: bool = False
    note: str = ""


def draft_ack(msg: Message, cc: Optional[List[str]] = None) -> Draft:
    """A plain acknowledgement for a scheduling/simple message. It makes NO
    factual claims and cites nothing, so there is nothing to ground."""
    body = (
        "Hi,\n\n"
        "Thanks for the note -- I've seen this and will follow up shortly to "
        "confirm.\n\n-- Sam"
    )
    return Draft(msg.id, msg.sender, _re(msg.subject), body, cited=[], grounded=False,
                 method="acknowledgement", cc=cc or [],
                 note="Acknowledgement only; no factual claim, nothing to cite.")


def draft_reply(msg: Message, store: MailStore, cc: Optional[List[str]] = None) -> Draft:
    g: Grounding = find_grounding(msg, store)
    cc = cc or []

    if g.cited and not verify_citations(g.cited, store):
        # Should never happen, but never cite something not in the store.
        return Draft(msg.id, msg.sender, _re(msg.subject), "", refused=True,
                     note="Citation failed verification; drafting nothing.")

    subject = _re(msg.subject)

    # Case A: we have a concrete fact (e.g. the staging URL) to reuse.
    if g.answer_fact:
        src = g.cited[0]
        body = (
            f"Hi,\n\n"
            f"As requested, here is the detail from our earlier message ({src}):\n\n"
            f"    {g.answer_fact}\n\n"
            f"Let me know if you need anything else.\n\n-- Sam"
        )
        body = _maybe_polish(msg, body)
        return Draft(msg.id, msg.sender, subject, body, cited=list(g.cited),
                     grounded=True, method=g.method, cc=cc,
                     note="Grounded on a concrete fact from the cited message.")

    # Case B: we have a related earlier message but no single concrete fact.
    if g.cited:
        src = g.cited[0]
        body = (
            f"Hi,\n\n"
            f"Following up on your note. Referencing our earlier exchange ({src}), "
            f"I'll get back to you shortly with specifics.\n\n-- Sam"
        )
        return Draft(msg.id, msg.sender, subject, body, cited=list(g.cited),
                     grounded=True, method=g.method, cc=cc,
                     note=g.note)

    # Case C: nothing grounds it -> draft nothing, say so.
    return Draft(msg.id, msg.sender, subject, "", refused=True, method=g.method,
                 note="No supporting message in the inbox; drafting nothing (Part 3).")


def _re(subject: str) -> str:
    return subject if subject.lower().startswith("re:") else f"Re: {subject}"


def _maybe_polish(msg: Message, fallback_body: str) -> str:
    """Optionally let a configured model rewrite the tone. Falls back to the
    deterministic draft when offline -- the cited fact is preserved verbatim.
    """
    if not llm.available():
        return fallback_body
    prompt = (
        "Rewrite the following reply to be warm and concise. Do NOT change, add, "
        "or remove any factual detail (URLs, numbers). Return only the reply.\n\n"
        + llm.build_untrusted_block(msg.body)
        + "\n\nDraft to rewrite:\n" + fallback_body
    )
    out = llm.complete(prompt, system="You are an assistant drafting email replies for Sam.")
    return out.strip() if out else fallback_body
