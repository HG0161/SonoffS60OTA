"""Resumable run state for one plug.

The state file is the single source of truth for where a run got to.  It is
written atomically after every step so an interrupted run - or a lost console -
can be resumed by reading it.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import secrets
from pathlib import Path
from typing import Any

SCHEMA = 1

PENDING = "pending"
ATTEMPTED = "attempted"
DONE = "done"
FAILED = "failed"
SKIPPED = "skipped"


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


class RunState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {
            "schema": SCHEMA,
            "run_id": secrets.token_hex(6),
            "created_utc": utc_now(),
            "updated_utc": utc_now(),
            "inputs": {},
            "steps": {},
            "captures": {},
        }
        if path.exists():
            self.load()

    @classmethod
    def open(cls, directory: Path) -> "RunState":
        directory.mkdir(parents=True, exist_ok=True)
        return cls(directory / "autoflash-state.json")

    def load(self) -> None:
        loaded = json.loads(self.path.read_text(encoding="utf-8"))
        if loaded.get("schema") != SCHEMA:
            raise ValueError(f"unsupported run-state schema in {self.path}")
        self.data = loaded

    def save(self) -> None:
        self.data["updated_utc"] = utc_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.{secrets.token_hex(4)}.tmp")
        try:
            temporary.write_text(
                json.dumps(self.data, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            os.replace(temporary, self.path)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    # -- inputs -------------------------------------------------------------

    def set_input(self, key: str, value: Any) -> None:
        self.data["inputs"][key] = value
        self.save()

    def input(self, key: str, default: Any = None) -> Any:
        return self.data["inputs"].get(key, default)

    def require(self, *keys: str) -> list[Any]:
        missing = [key for key in keys if self.data["inputs"].get(key) in (None, "")]
        if missing:
            raise ValueError(f"missing required inputs: {', '.join(missing)}")
        return [self.data["inputs"][key] for key in keys]

    # -- steps --------------------------------------------------------------

    def status(self, step: str) -> str:
        return self.data["steps"].get(step, {}).get("status", PENDING)

    def is_done(self, step: str) -> bool:
        return self.status(step) == DONE

    def mark(self, step: str, status: str, **fields: Any) -> None:
        record = self.data["steps"].setdefault(step, {})
        record["status"] = status
        record["at"] = utc_now()
        record.update(fields)
        self.save()

    def capture(self, key: str, value: Any) -> None:
        """Record an observation for the run sheet's capture log."""
        self.data["captures"][key] = value
        self.save()

    def summary(self, order: list[tuple[str, str]]) -> str:
        lines = []
        for step, title in order:
            record = self.data["steps"].get(step, {})
            status = record.get("status", PENDING)
            mark = {DONE: "x", FAILED: "!", SKIPPED: "-"}.get(status, " ")
            when = record.get("at", "")[:19].replace("T", " ")
            lines.append(f"  [{mark}] {step:<4} {title:<44} {status:<8} {when}")
        return "\n".join(lines)
