"""Append-only structured trace.

Every consequential decision is written here as one JSON object per line,
tagged with the capability that produced it (cap=R1..R6, X1..). This is the
audit trail that answers "who is accountable" and "why did it do that" -- it is
how a bad send is traced back to the message, the disposition, and the gate
decision that let it through.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import config


class Tracer:
    def __init__(self, path=None, reset: bool = False):
        self.path = path or config.TRACE_PATH
        if reset and self.path.exists():
            self.path.unlink()

    def emit(self, event: str, cap: str = "", msg_id: str = "", **fields: Any) -> None:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "cap": cap,
            "event": event,
            "msg_id": msg_id,
        }
        record.update(fields)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read(self) -> list:
        if not self.path.exists():
            return []
        out = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out
