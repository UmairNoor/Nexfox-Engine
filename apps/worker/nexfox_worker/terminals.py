"""Account-to-terminal association. Each MT5 account always uses the same terminal installation on this host
(persisted in data/terminals.json), and a terminal never serves two accounts at the same time."""
import json
import threading
from pathlib import Path


class TerminalPool:
    def __init__(self, terminals: list[str], state_file: Path):
        self.terminals, self.file = list(terminals), state_file
        self.lock = threading.Lock()
        self.assigned: dict[str, str] = {}   # account_id -> terminal (sticky, persisted)
        self.busy: dict[str, int] = {}       # terminal -> instance_id currently using it
        if state_file.exists():
            try:
                self.assigned = {k: v for k, v in json.loads(state_file.read_text(encoding="utf-8")).items() if v in self.terminals}
            except ValueError:
                self.assigned = {}

    def _save(self) -> None:
        self.file.write_text(json.dumps(self.assigned, indent=1), encoding="utf-8")

    def free(self) -> int:
        return len(self.terminals) - len(self.busy)

    def acquire(self, account_id: int, instance_id: int) -> str | None:
        key = str(account_id)
        with self.lock:
            t = self.assigned.get(key)
            if t and t not in self.busy:
                self.busy[t] = instance_id
                return t
            used_by_others = set(self.assigned.values())
            for cand in self.terminals:  # prefer a terminal not bound to another account
                if cand not in self.busy and cand not in used_by_others:
                    break
            else:
                cand = next((c for c in self.terminals if c not in self.busy), None)
            if cand is None:
                return None
            for k, v in list(self.assigned.items()):
                if v == cand and k != key:
                    del self.assigned[k]  # re-bind: the previous account's data stays in that terminal's portable dir
            self.assigned[key] = cand
            self.busy[cand] = instance_id
            self._save()
            return cand

    def release(self, terminal: str) -> None:
        with self.lock:
            self.busy.pop(terminal, None)
