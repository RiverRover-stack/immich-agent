"""Write-ahead undo journal.

An entry is written BEFORE a fault is applied and removed only after the undo is verified.
If the tool crashes mid-fault, `shutterstorm panic` reads this file and puts everything back.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from shutterstorm.config import ROOT

DEFAULT_STATE_DIR = ROOT / ".shutterstorm"


class Journal:
    def __init__(self, state_dir: Path | None = None) -> None:
        self.state_dir = Path(state_dir or DEFAULT_STATE_DIR)
        self.path = self.state_dir / "faults.json"

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        try:
            return json.loads(self.path.read_text())
        except json.JSONDecodeError as e:
            raise RuntimeError(
                f"undo journal {self.path} is corrupt ({e}); inspect it by hand before continuing"
            ) from e

    def _write(self, entries: list[dict]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, prefix=".faults-", suffix=".tmp")
        with os.fdopen(fd, "w") as f:
            json.dump(entries, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)  # atomic: never a half-written journal

    def add(self, target: str, fault: str, params: dict, recipe: dict, ttl_s: float) -> dict:
        entry = {
            "id": uuid.uuid4().hex[:8],
            "target": target,
            "fault": fault,
            "params": params,
            "recipe": recipe,
            "ttl_s": ttl_s,
            "created_at": time.time(),
            "pid": os.getpid(),
        }
        self._write([*self.entries(), entry])
        return entry

    def remove(self, entry_id: str) -> None:
        self._write([e for e in self.entries() if e["id"] != entry_id])


class KillSwitch:
    """Latched STOP file. Once set, nothing runs until it is explicitly cleared."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self.path = Path(state_dir or DEFAULT_STATE_DIR) / "STOP"

    def is_set(self) -> bool:
        return self.path.exists()

    def trip(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
