"""The free plans a visitor who has not signed in may generate (DECISIONS entry 262).

A plan spends searches and model calls, so an open page is a wallet. Signed-in travellers are not
counted; anyone else gets `ANONYMOUS_PLAN_ALLOWANCE` plans, counted against the address the request
came from. A cookie would be the gentler key and is useless here: clearing it resets the count.

**What is stored is a count against a keyed hash of the address, never the address.** The key is
random, made on first use and kept beside the counts, so the file alone does not say who visited.

**A plan is counted when it is accepted, before anything is spent** — after the request is valid
and the corridor possible, so a malformed request or an unsupported destination costs nothing.
A refusal after research has started still counts: the money was spent.

**Known limits.** Visitors behind one address (an office, a mobile carrier) share its allowance,
and someone moving between addresses gets more. It is a brake on an open wallet, not an identity.
"""

import hashlib
import hmac
import json
import os
import secrets
import threading
from pathlib import Path
from typing import Any


class AllowanceStoreError(RuntimeError):
    """The allowance file could not be read or written; plans are refused rather than uncounted."""


class AnonymousAllowance:
    """Counts plans per address, up to `limit`, in one JSON file."""

    _lock = threading.Lock()

    def __init__(self, path: Path, limit: int) -> None:
        self.path = path
        self.limit = limit

    def remaining(self, address: str) -> int:
        with self._lock:
            state = self._load()
            used: int = state["used"].get(self._key(state, address), 0)
            return max(0, self.limit - used)

    def spend(self, address: str) -> int:
        """Count one plan for `address` and return how many are left, or raise when none were."""

        with self._lock:
            state = self._load()
            key = self._key(state, address)
            used: int = state["used"].get(key, 0)
            if used >= self.limit:
                raise AllowanceSpent(self.limit)
            state["used"][key] = used + 1
            self._save(state)
            return self.limit - used - 1

    def _key(self, state: dict[str, Any], address: str) -> str:
        return hmac.new(bytes.fromhex(state["key"]), address.encode(), hashlib.sha256).hexdigest()

    def _load(self) -> dict[str, Any]:
        try:
            if not self.path.exists():
                return {"key": secrets.token_hex(32), "used": {}}
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise AllowanceStoreError(f"{self.path} could not be read") from exc
        if (
            not isinstance(state, dict)
            or "key" not in state
            or not isinstance(state.get("used"), dict)
        ):
            raise AllowanceStoreError(f"{self.path} is not an allowance file")
        return state

    def _save(self, state: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            partial = self.path.with_suffix(".tmp")
            partial.write_text(json.dumps(state), encoding="utf-8")
            os.replace(partial, self.path)
        except OSError as exc:
            raise AllowanceStoreError(f"{self.path} could not be written") from exc


class AllowanceSpent(Exception):
    """Every free plan for this address has been used."""

    def __init__(self, limit: int) -> None:
        super().__init__(f"all {limit} free plans used")
        self.limit = limit
