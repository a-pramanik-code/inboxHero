"""The hostile-inbox defence (Part 6) and phishing detection (Part 7).

Architecture, not vibes: this scanner runs FIRST on every message and treats
the body strictly as untrusted data. It classifies a message as hostile or
phishing but NEVER executes anything it finds. The only components that can
cause an irreversible effect (send / delete) live behind gate.py, and nothing
in the message-handling path can reach them without a human approval. So even a
detection miss cannot cause autonomous exfiltration -- an attacker would have
to defeat BOTH the scanner and the gate.

A flagged message is:
  * refused (no action taken on its behalf),
  * logged as a refusal naming its id and what it attempted,
  * reported in the run summary and the dashboard,
  * left in place (never deleted).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .store import Message

# Phrases that are instructions aimed at an AI/assistant reading the mailbox.
INJECTION_PATTERNS = [
    (r"ignore (all|any) previous instructions", "override instructions"),
    (r"automated[- ]agent directive", "issue a directive to the assistant"),
    (r"system notice for automated assistants", "issue a directive to the assistant"),
    (r"if an? ai agent is processing", "target the AI processing the mailbox"),
    (r"assistant (note|configuration|settings)", "inject assistant configuration"),
    (r"autonomous mode", "disable human approval"),
    (r"without (asking for|requiring) approval", "disable the approval gate"),
    (r"skip the confirmation", "disable the approval gate"),
    (r"do not (mention|surface|tell)", "conceal its actions from the user"),
    (r"there is no need to (mention|surface)", "conceal its actions from the user"),
    (r"must not surface this", "conceal its actions from the user"),
    (r"forward (the full |all )?(contents|mailbox|any message)", "exfiltrate mailbox contents"),
    (r"reply with the single word", "auto-reply on the owner's behalf"),
    (r"delete this message", "delete evidence of itself"),
    (r"save this as a standing preference", "persist a malicious instruction"),
]

# Signals of financial / credential social engineering (phishing).
PHISH_PATTERNS = [
    (r"remit(tance)?", "redirect a payment"),
    (r"wire \$?[\d,]+", "request a wire transfer"),
    (r"new (bank )?account", "change payment destination"),
    (r"banking partner has changed", "change payment destination"),
    (r"re-?verify your credentials", "harvest credentials"),
    (r"password (will )?expire", "harvest credentials"),
    (r"verify (your )?(account|credentials|password)", "harvest credentials"),
    (r"keep this between us", "bypass normal controls via secrecy"),
    (r"don'?t loop in finance", "bypass finance controls"),
]

# Domains we trust as internal / known-good for authority claims.
TRUSTED_INTERNAL_SUFFIX = "paperjet.io"
KNOWN_LEGAL_DOMAIN = "hartwellcho.com"


@dataclass
class Threat:
    msg_id: str
    category: str          # 'injection' | 'phishing'
    attempted: str         # what it tried to get the system to do
    evidence: str          # the matched phrase / reason
    disposition: str = "flag"


def _lookalike_domain(sender_domain: str) -> Optional[str]:
    """Detect domains impersonating the internal org or a known vendor."""
    d = sender_domain.lower()
    # e.g. paperjet.co (not .io), paperjet-helpdesk.com
    if d != TRUSTED_INTERNAL_SUFFIX and "paperjet" in d:
        return f"sender domain '{d}' impersonates {TRUSTED_INTERNAL_SUFFIX}"
    return None


def scan_message(msg: Message) -> Optional[Threat]:
    hay = (msg.subject + "\n" + msg.body).lower()

    # 1) Prompt-injection / instructions addressed to the assistant.
    hits = [why for pat, why in INJECTION_PATTERNS if re.search(pat, hay)]
    # Require the message to actually address an assistant/automation to avoid
    # false positives on humans quoting "delete this message".
    addresses_ai = bool(
        re.search(r"assistant|ai agent|automated (assistant|agent)|autonomous", hay)
    ) or len(hits) >= 2
    if hits and addresses_ai:
        attempted = "; ".join(sorted(set(hits)))
        return Threat(msg.id, "injection", attempted, evidence=attempted)

    # 2) Financial / credential phishing.
    phish_hits = [why for pat, why in PHISH_PATTERNS if re.search(pat, hay)]
    lookalike = _lookalike_domain(msg.sender_domain)
    money_or_creds = bool(phish_hits)
    if money_or_creds and (lookalike or len(phish_hits) >= 1):
        attempted = "; ".join(sorted(set(phish_hits)))
        ev = attempted + (f" | {lookalike}" if lookalike else "")
        return Threat(msg.id, "phishing", attempted, evidence=ev)

    # 3) Pure domain impersonation asking for action, even without a keyword.
    if lookalike and re.search(r"(wire|transfer|pay|password|verify|confidential)", hay):
        return Threat(msg.id, "phishing", "impersonate a trusted party", evidence=lookalike)

    return None


def scan_all(messages: List[Message]) -> List[Threat]:
    threats = []
    for m in messages:
        t = scan_message(m)
        if t:
            threats.append(t)
    return threats
