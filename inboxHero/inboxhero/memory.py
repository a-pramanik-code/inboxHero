"""Persistent standing preferences (Part 5).

Preferences are the owner's standing instructions ("CC Priya on lawyer mail",
"no meetings before 11am"). They are written to state/prefs.json so they
survive a full process exit, and are re-loaded and applied on the next run.

Crucially: preferences are only learned from messages the OWNER sent to
themselves, or explicitly confirmed. A stranger cannot inject a preference by
emailing one in -- see security.py for why m039's "save this as a standing
preference" is refused, not stored.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from typing import List, Optional

import config


@dataclass
class Preference:
    id: str                 # source message id
    kind: str               # 'cc_rule' | 'no_early_meetings' | 'note'
    description: str        # human-readable
    params: dict            # structured knobs the pipeline reads


class PreferenceStore:
    def __init__(self, path=None):
        self.path = path or config.PREFS_PATH
        self._prefs: List[Preference] = []
        self.load()

    def load(self) -> None:
        self._prefs = []
        if self.path.exists():
            data = json.loads(self.path.read_text(encoding="utf-8"))
            self._prefs = [Preference(**p) for p in data]

    def save(self) -> None:
        config.ensure_dirs()
        self.path.write_text(
            json.dumps([asdict(p) for p in self._prefs], indent=2),
            encoding="utf-8",
        )

    def add(self, pref: Preference) -> bool:
        """Add if not already present (idempotent by source id + kind)."""
        for p in self._prefs:
            if p.id == pref.id and p.kind == pref.kind:
                return False
        self._prefs.append(pref)
        self.save()
        return True

    def all(self) -> List[Preference]:
        return list(self._prefs)

    def by_kind(self, kind: str) -> List[Preference]:
        return [p for p in self._prefs if p.kind == kind]

    def cc_rule_for(self, sender_domain: str) -> Optional[Preference]:
        for p in self.by_kind("cc_rule"):
            if p.params.get("match_domain", "").lower() == sender_domain.lower():
                return p
        return None

    def earliest_meeting_hour(self) -> Optional[int]:
        best = None
        for p in self.by_kind("no_early_meetings"):
            h = p.params.get("min_hour")
            if h is not None:
                best = h if best is None else max(best, h)
        return best
