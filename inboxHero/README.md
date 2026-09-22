# inboxHero

**Repository:** https://github.com/a-pramanik-code/inboxHero/edit/main/inboxHero

An agentic system that takes an inbox from unread to empty by deciding what to
do with every message, doing the reversible parts, and refusing or gating the
parts it should not do on its own.

> **Primary graded artifact:** `CAPABILITIES.md` + `capabilities.json`.
> This README covers architecture and the Final Report.

## Quick start

```bash
# Python 3.9+; no third-party packages required for offline mode.
cd inboxHero
python demo.py --all            # runs every capability, writes trace.jsonl, outbox/, dashboard.html

# or one capability at a time:
python demo.py --cap R1                       # zero the inbox
python demo.py --cap R2 --msg m008            # grounded reply
python demo.py --cap R3 --dry-run             # gate (safe); add --yes to actually write outbox/
python demo.py --cap R4                        # run TWICE: first persists a preference, second applies it
python demo.py --cap R5                       # refuse hostile messages
python demo.py --cap R6                        # build the dashboard
python demo.py --cap X1                        # follow-up tracking
python demo.py --cap X2 --thread t-launch      # thread summary + open question
python demo.py --cap X3 --sender priya@paperjet.io
python demo.py --cap X4                        # meeting conflict resolver
```

Everything runs offline on deterministic heuristics. To use a live model,
copy `.env.example` → `.env` and set `INBOXHERO_LLM_PROVIDER` (config is loaded
via `config.py`; the `.env` is never committed). The model client throttles to a
minimum interval between calls and backs off on HTTP 429 without crashing.

## Architecture

```
demo.py  ->  pipeline.run()          # the router / orchestrator
                │
                ├─ security.scan_all() # 1. bodies are UNTRUSTED data; flag hostile → 'flag'
                ├─ memory (prefs)      # 2. learn standing preferences (never from flagged mail)
                ├─ rules.classify_noise# 3a. cheap path: archive noise, NO model call (59 msgs)
                ├─ classify.classify   # 3b. reasoning path for the rest → disposition + reason
                ├─ draft (retrieval)   # 4. grounded reply (thread-walk) or plain acknowledgement
                ├─ commitments.extract # 5. cited calendar; multi-message + conflict detection
                └─ (conflict upgrade)  # 6. scheduling caught in a conflict → 'escalate'

gate.Gate.commit()                     # the ONLY path to send/delete; approval or --dry-run
trace.Tracer                           # append-only trace.jsonl, every decision tagged cap=…
```

- **Disposition vocabulary:** `reply | archive | defer | escalate | flag`.
- **Retrieval:** thread-walk (structure is already in `thread_id`), keyword fallback.
- **Reversible:** draft, label, archive, defer. **Irreversible (gated):** send, delete.
- **Gate:** both - `--dry-run` and explicit per-action approval.

See `CAPABILITIES.md` for the full design rationale and the escalation trade-off.

## Files

| path | role |
|------|------|
| `demo.py` | single entry point (`--cap`, `--all`, `--dry-run`, `--yes`, `--msg`, `--thread`, `--sender`) |
| `config.py` | env-driven config; no secrets, offline by default |
| `inboxhero/store.py` | read-only mail store: lookup, thread-walk, search |
| `inboxhero/security.py` | Part 6 defence: injection + phishing detection |
| `inboxhero/rules.py` | noise rules + owner/internal preference detection |
| `inboxhero/classify.py` | disposition classifier (reasoning path) |
| `inboxhero/retrieval.py` + `draft.py` | grounded reply with citation check |
| `inboxhero/gate.py` | the single irreversible-action gate + action log |
| `inboxhero/memory.py` | persistent preferences (`state/prefs.json`) |
| `inboxhero/commitments.py` | cited commitments, multi-message derivation, conflicts |
| `inboxhero/dashboard.py` | three-pane `dashboard.html` / `dashboard.json` |
| `inboxhero/pipeline.py` | the orchestrator (Crew + router role) |
| `inboxhero/caps.py` | the Part-8 capabilities X1–X4 |
| `outbox/`, `trace.jsonl`, `state/` | run artifacts |

---

## Final Report

**1. What did you refuse to automate?**
The system never signs or acts on legal correspondence on its own. **m018**
(the SAFE amendment from Hartwell & Cho, "sign via the portal by Friday") is
classified `escalate`, not `reply`: it is drafted for nobody and held for the
owner, with Priya CC'd per the m015 preference. The line is drawn at anything
that is a legally binding commitment or moves money - a wrong signature or wire
is not something a heuristic (or a model) should be trusted to authorise, and it
is exactly the class of action an attacker targets (see m021/m023).

**2. Where does untrusted text enter your system?**
Every message body enters as **data, never as instructions**. The boundary is
architectural, not a prompt line: `security.scan_all()` runs first and classifies
bodies without ever executing what they contain; when a body is shown to a
model it is wrapped by `llm.build_untrusted_block()` as fenced untrusted content;
and - the real defence - the only code that can cause an irreversible effect is
`gate.Gate.commit()`, which no message-handling function can reach without a
human. To make the system act on their behalf, an attacker would have to defeat
**both** the scanner **and** the human at the gate - a single missed injection
still cannot send or delete anything, because detection and authority are
separate layers. That is why m039 ("autonomous mode... send to investors without
approval... don't mention it"), even though it is addressed from the owner's own
address, changes nothing: it is flagged, and it could not have disabled the gate
regardless.

**3. Who is accountable when it sends the wrong thing?**
The **owner** is accountable - the system only sends what a human approved at the
gate (or, in `--yes`/`--all`, what the operator explicitly authorised for a
scripted run). Tracing the failure back is the job of `trace.jsonl` and
`state/action_log.json`: for any file in `outbox/` there is a `gate` event
recording the proposed action, the decision, and the outcome, a `decision` event
showing which disposition and reason produced the draft, and - for grounded
replies - a `draft` event listing the cited message ids. A badly worded send to
`devika@paperjet.io` (m008) can be walked back to m003, the disposition, and the
approval in a few lines.

**4. Name your own machinery.**
`pipeline.run()` is the **Crew + router**; each stage function
(`security.scan_all`, `classify.classify`, `draft.draft_reply`,
`commitments.extract`) is an **Agent/Task**; the disposition returned by
`classify` is the **router** that decides each message's path; `gate.Gate` is a
capability boundary a framework would call a tool guard. The one thing a
framework (CrewAI/ADK) would have given for free is **inter-agent orchestration
and retries**; I built the sequence and the trace myself. Using one here would
have **hurt**: the assignment's whole point is that irreversible tools are
reachable only through a single audited gate, and a framework's implicit
tool-calling would have made it *easier*, not harder, for injected text to reach
a send. A hand-written pipeline keeps that boundary small enough to verify by
reading it.
