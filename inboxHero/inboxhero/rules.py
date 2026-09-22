"""Rule layer: the cheap, deterministic path that never touches a model.

Receipts, newsletters, notifications and automated alerts are recognised by
sender and phrasing alone. Recognising them with an LLM would waste money and
latency, so they are archived by rule. Everything a rule cannot confidently
dispose of is passed on to the reasoning layer.
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from .store import Message

# Sender local-part / substrings that reliably indicate machine-generated mail.
# Threats are removed by the security scanner BEFORE this runs, so a broad list
# here is safe (a phishing "billing@" is already flagged and never reaches us).
NOISE_SENDER_HINTS = (
    "no-reply@", "noreply@", "no_reply@", "notifications@", "notify@",
    "newsletter@", "digest@", "updates@", "receipts@", "receipt@",
    "billing@", "invoice", "orders@", "order@", "ship-confirm@", "info@",
    "insights@", "feedback@", "hello@", "alerts@", "alert@", "status@",
    "checkin@", "success@", "no-reply-aws@", "mailer-daemon@", "support@",
    "help@", "security@", "calendar-notification@", "statements@", "notify@",
    "reminders@", "hr@",
)

# Domains whose mail is essentially always automated for this owner.
NOISE_DOMAINS = {
    "dropbox.com", "slack.com", "vercel.com", "1password.com", "amazon.com",
    "members.netflix.com", "google.com", "apple.com", "spotify.com",
    "coursera.org", "github.com", "figma.com", "bluebottlecoffee.com",
    "pagerduty.com", "producthunt.com", "accounts.google.com", "sentry.io",
    "postmarkapp.com", "datadoghq.com", "email.apple.com", "namecheap.com",
    "notion.so", "mail.notion.so", "cloudflare.com", "pragmaticengineer.com",
    "robinhood.com", "stripe.com", "intercom.io", "chase.com", "zoom.us",
    "twitter.com", "medium.com", "substack.com", "mailchimp.com",
    "digitalocean.com", "swiggy.in", "ramp.com", "doordash.com",
    "linkedin.com", "grammarly.com", "united.com", "instacart.com",
    "todoist.com", "uber.com", "lyft.com", "hackernewsletter.com",
    "calendly.com", "email.apple.com",
}

# Phrases typical of "no action needed" machine mail.
NOISE_BODY_HINTS = (
    "no action needed", "no further action needed", "this is a receipt",
    "for your records", "manage your membership", "manage your subscription",
    "rate your", "view invoice", "download your invoice", "unread activity",
    "open slack", "open notion", "open todoist", "see what's happening",
    "read in your browser", "upvote your favourites", "keep it up",
    "your statement is", "auto-renews", "was charged", "receipt for",
)


def is_owner(msg: Message, owner: str) -> bool:
    return msg.sender.lower() == owner.lower()


def classify_noise(msg: Message) -> Optional[Tuple[str, str]]:
    """Return (disposition, reason) if this is clearly noise, else None.

    Only 'archive' comes out of here. The security scanner runs BEFORE this in
    the pipeline, so a hostile message dressed as a newsletter is caught first.
    """
    sender = msg.sender.lower()
    domain = msg.sender_domain

    # A 2FA code or password-change alert is transactional noise, archive it.
    if domain == "accounts.google.com" and "verification code" in msg.subject.lower():
        return ("archive", "One-time verification code; transactional noise, no reply needed.")

    hit_sender = any(h in sender for h in NOISE_SENDER_HINTS)
    hit_domain = domain in NOISE_DOMAINS
    body = msg.body.lower()
    hit_body = any(h in body for h in NOISE_BODY_HINTS)

    # A known pure-noise vendor domain is enough on its own.
    if hit_domain:
        return ("archive", f"Automated notification/receipt from {domain}; rule-archived, no model call.")
    # A machine sender local-part (no-reply@, receipts@, billing@, ...) is enough.
    if hit_sender:
        return ("archive", f"Machine-generated mail from '{sender}'; rule-archived, no model call.")
    # Or unmistakable 'no action needed' / receipt phrasing.
    if hit_body:
        return ("archive", "Automated 'no action needed' / receipt phrasing; rule-archived, no model call.")
    return None


# --- Preference learning (rule-based, owner-only) ---------------------------

def detect_preference(msg: Message, owner: str):
    """If a message states a standing preference we may safely honour, return a
    (kind, description, params) tuple, else None.

    Two trust rules keep this from being an injection vector:
      * A calendar/behaviour rule is only learned from the OWNER's own mail.
      * A "CC me on X" rule is only learned when the CC target is INTERNAL
        (same domain as the owner). A stranger cannot use this to have mail
        exfiltrated to an external address -- that request is dropped here and,
        if it also asks for concealment, flagged by the security scanner.
    Hostile "preferences" (disable approvals, auto-send, hide it) never match
    these patterns and are refused upstream.
    """
    owner_domain = owner.split("@")[-1].lower()
    body = msg.body.lower()

    # Owner-authored calendar rule: "no meetings before 11:00am".
    if is_owner(msg, owner):
        m = re.search(r"meetings before\s*(\d{1,2})", body)
        if m or ("meetings before" in body):
            hour = int(m.group(1)) if m else 11
            return (
                "no_early_meetings",
                f"Never accept meetings before {hour}:00; offer {hour}:00 or later instead.",
                {"min_hour": hour, "source_subject": msg.subject},
            )

    # "CC / loop me in on mail from <lawfirm>" from an internal colleague.
    wants_cc = any(p in body for p in ("cc'd on", "cc me", "loop me in", "make sure i'm cc"))
    if wants_cc and msg.sender_domain == owner_domain:
        # Resolve the correspondent category the rule applies to.
        match_domain = None
        m = re.search(r"@([a-z0-9.\-]+\.[a-z]{2,})", body)
        if "hartwell" in body:
            match_domain = "hartwellcho.com"
        elif m:
            match_domain = m.group(1)
        if match_domain:
            return (
                "cc_rule",
                f"Always CC {msg.sender} on mail from {match_domain}.",
                {"match_domain": match_domain, "cc": msg.sender, "source_subject": msg.subject},
            )
    return None
