# CAPABILITIES.md - inboxHero

**Student:Ansuman Pramaink, Evernorth-aai-1180197;

Run everything through one entry point:

```
python demo.py --cap R1        # one capability
python demo.py --all           # all of them, in the order below (fresh trace)
```

No API key is required: the system runs fully offline on deterministic
heuristics, so every command below reproduces on a clean checkout.

---

## The system, in one paragraph

A single Python pipeline, no framework. Every message is first passed through a
**security scanner** that treats the body as untrusted data; anything hostile is
flagged and removed from further handling. Surviving messages hit a **rule
layer** that archives obvious noise (receipts, newsletters, alerts) with no
model call, and only what the rules cannot dispose of reaches the **reasoning
layer** that assigns a disposition, drafts grounded replies, and detects
commitments and conflicts across messages. Irreversible actions (`send`,
`delete`) are reachable only through one **gate**. State that must outlive a run
(preferences, the action log, the trace) lives in small files on disk.

## Design choices you were asked to state

- **Framework: none.** The work is a fixed sequence with one branch (rule-path
  vs reasoning-path); a crew or graph would have been overhead and would have
  blurred the one property that matters most here - that only one function can
  send. See Final Report Q4.
- **Retrieval: thread-walk.** An inbox already carries its structure in
  `thread_id`, so walking the thread is cheaper and more precise than embeddings
  for grounding a reply (e.g. m008 -> the AMQP URL in m003). Keyword search is
  the cross-thread fallback. Named in `capabilities.json` as `retrieval`.
- **Messages processed: 100. Rule-handled with no model call: 59.** The 59 are
  the receipts/newsletters/notifications/alerts the rule layer archives
  outright.
- **Reversible vs irreversible.** `send` and `delete` are irreversible and
  gated. `draft`, `label`, `archive`, `defer` are reversible and run without a
  prompt. **Deleting is treated as irreversible** because the mock store has no
  trash - a deleted message is gone - so the system never deletes at all (and a
  delete is refused even if a human approves it, because the only things that
  ever *ask* to delete are hostile messages).
- **Disposition vocabulary:** `reply` (a response is owed; drafted, send gated),
  `archive` (no action owed; the rule path lands here), `defer` (needs the owner
  later; kept on the radar), `escalate` (needs a human decision now - money,
  legal, signatures, time conflicts), `flag` (refused: hostile/phishing, left in
  place and reported).
- **Where the gate sits.** Exactly one class, `gate.Gate`, can cause an
  irreversible effect, and every send/delete goes through its `commit()`.
  Nothing in the message-handling path can reach the filesystem otherwise. This
  is also the Part 6 defence: a hostile message can influence a *draft*, but it
  cannot reach a *send* without a human passing the gate.
- **Escalation line - where we drew it and what we traded.** We ask for approval
  only on **sends** (all of them, since a send can't be unsent) and we
  **escalate** money/legal/signature decisions and time conflicts to the owner.
  Reversible archives and defers are automatic. The trade: a newsletter could in
  principle be wrongly archived (low cost, reversible), in exchange for never
  asking the owner to rubber-stamp forty items - so the four things we do
  surface (the legal signatures, the two conflicting Sep-15 3pm items, the early
  investor slot) actually get read.

## Capabilities

| id | name | tier | one-line claim |
|----|------|------|----------------|
| R1 | Zero the inbox | B | every message gets one disposition + reason; 59 handled by rule, none left undecided |
| R2 | Grounded reply | B | drafts cite the earlier message they used; ungroundable -> nothing drafted |
| R3 | Gate the irreversible | C | no send/delete without --dry-run or per-action approval |
| R4 | Persistent preference | C | a stated preference survives a full restart and changes behaviour |
| R5 | Refuse embedded instructions | C | detects, refuses, flags, reports 7 hostile messages; deletes none |
| R6 | Dashboard | C | three panes; commitments cited; the Sep-15 conflict surfaced |
| X1 | Follow-up tracking | B | owner-sent mail unanswered 3+ days, with a drafted chase (m044) |
| X2 | Thread summarisser | B | a thread → timeline + the one open question (t-launch -> m030) |
| X3 | Sender lookup | A | all mail from one sender, in a single lookup |
| X4 | Meeting conflict resolver | C | preference-violating slot -> 3 alternatives, held for approval (m043) |

Tiers A, B and C are all represented (X3 is the tier-A lookup; R1/R2/X1/X2 are
tier B; R3/R4/R5/R6/X4 are tier C).

The exact command, observable outcome and evidence for each is in
`capabilities.json`, which is the machine-readable version a marking script
reads. Keep the two in step.

## Data assumptions

- `inbox.json` is a list of 100 objects with keys `id, thread_id, from, to,
  subject, timestamp, body, unread`. Timestamps are naive ISO-8601, all in
  September 2026.
- `from == to == owner` (`sam@paperjet.io`) marks a note-to-self.
- A fixed "run day" (`2026-09-12T09:00`, override with `INBOXHERO_NOW`) makes
  follow-up ages and orderings reproducible.

## Final Report

See `README.md` for the four Final-Report answers.
