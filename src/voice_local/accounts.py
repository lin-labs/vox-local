"""Accounts — the inbound-call customer registry + PIN auth gate.

One FOLDER per account under ``<state>/accounts/<account_number>/`` holding
``account.json`` (identity, phones, channels) and ``notes.txt`` (the host's
notebook — one timestamped line per remembered fact, kept OUT of the
git-committed gems DB because it is caller-private). Legacy flat
``<number>.json`` files are still read and migrate to folders on next save.
Caller-ID lookup happens SILENTLY when a call connects: a match only shortcuts
which account the PIN is checked against — the service must never reveal which
account (if any) a caller-ID matched. ``AuthGate`` owns the per-call attempt
counter so the 3-strike lockout is deterministic and unit-testable, independent
of the voice brain.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Account:
    account_number: str
    pin: str
    name: str
    phones: list[str] = field(default_factory=list)
    notes: str = ""
    # booking thread roots keyed by thread name ("kobe-2026-08-01-3-food-tour" -> msg_...)
    booking_threads: dict[str, str] = field(default_factory=dict)
    # per-destination Puffo channels keyed by destination slug ("kobe" -> ch_...);
    # the channel is named <destination>-<account_number> and holds all of this
    # caller's booking threads for that destination
    channels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Account":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def to_dict(self) -> dict:
        return asdict(self)


def _digits(number: str) -> str:
    """Normalize a phone number to its digits so '+1 (650) 656-7722' == '+16506567722'."""
    return re.sub(r"\D", "", number or "")


class AccountStore:
    """Load/lookup/save accounts under one directory (created lazily on save),
    one folder per account: <dir>/<number>/account.json + notes.txt."""

    def __init__(self, dir: str | Path) -> None:
        self.dir = Path(dir)

    def _folder(self, account_number: str) -> Path:
        return self.dir / str(account_number).strip()

    def load_all(self) -> list[Account]:
        if not self.dir.is_dir():
            return []
        out, seen = [], set()
        # Folder layout first so it wins over a stale legacy flat file.
        for p in sorted(self.dir.glob("*/account.json")) + sorted(self.dir.glob("*.json")):
            try:
                a = Account.from_dict(json.loads(p.read_text()))
            except (ValueError, TypeError):  # skip malformed files, never break call setup
                continue
            if a.account_number not in seen:
                seen.add(a.account_number)
                out.append(a)
        return out

    def get(self, account_number: str) -> Account | None:
        for a in self.load_all():
            if a.account_number == str(account_number).strip():
                return a
        return None

    def lookup_by_phone(self, number: str) -> Account | None:
        """Match a caller's E.164 number against every account's phones (digit-wise)."""
        want = _digits(number)
        if not want:
            return None
        for a in self.load_all():
            if any(_digits(p) == want for p in a.phones):
                return a
        return None

    def verify(self, account_number: str, pin: str) -> Account | None:
        """Account-number + PIN check; the unknown-caller auth path."""
        a = self.get(account_number)
        if a is not None and str(pin).strip() == a.pin:
            return a
        return None

    def save(self, account: Account) -> Path:
        folder = self._folder(account.account_number)
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / "account.json"
        p.write_text(json.dumps(account.to_dict(), indent=2, ensure_ascii=False))
        legacy = self.dir / f"{account.account_number}.json"
        if legacy.exists():   # migrate the flat layout on first save
            legacy.unlink()
        return p

    def remove(self, account_number: str) -> None:
        folder = self._folder(account_number)
        if folder.is_dir():
            shutil.rmtree(folder)
        legacy = self.dir / f"{account_number}.json"
        if legacy.exists():
            legacy.unlink()

    # ---- the notebook (notes.txt beside account.json) ---------------------------

    def append_note(self, account_number: str, note: str, *, ts: str = "") -> None:
        folder = self._folder(account_number)
        folder.mkdir(parents=True, exist_ok=True)
        ts = ts or _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        with (folder / "notes.txt").open("a", encoding="utf-8") as f:
            f.write(f"{ts} | {' '.join(str(note).split())}\n")

    def read_notes(self, account_number: str) -> list[tuple[str, str]]:
        """[(ts, note), ...] oldest first; tolerant of hand-edited lines."""
        p = self._folder(account_number) / "notes.txt"
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            ts, sep, text = line.partition(" | ")
            if sep and text.strip():
                out.append((ts.strip(), text.strip()))
            elif line.strip():
                out.append(("", line.strip()))
        return out

    def move_notes(self, account_number: str, target: "AccountStore",
                   target_number: str) -> int:
        """Append this account's notes onto the target's notebook (timestamps
        preserved) and drop the source file. Returns how many moved."""
        notes = self.read_notes(account_number)
        for ts, text in notes:
            target.append_note(target_number, text, ts=ts)
        p = self._folder(account_number) / "notes.txt"
        if p.exists():
            p.unlink()
        return len(notes)


class AuthGate:
    """Per-call verification state: which account caller-ID matched (secret until the
    PIN proves it), how many failed attempts, and whether the gate is now locked.

    - caller-ID matched -> PIN alone is checked against THAT account.
    - no match          -> account_number + PIN are both required.
    After ``max_attempts`` failures the gate locks: further attempts always fail.
    """

    def __init__(self, store: AccountStore, *, matched: Account | None = None,
                 max_attempts: int = 3) -> None:
        self._store = store
        self.matched = matched
        self.verified: Account | None = None
        self.attempts = 0
        self.max_attempts = max_attempts

    @property
    def locked(self) -> bool:
        return self.attempts >= self.max_attempts

    def attempt(self, pin: str, account_number: str = "") -> Account | None:
        """One verification try. Returns the Account on success, else None (and
        counts the failure). A locked gate never succeeds."""
        if self.locked:
            return None
        account = None
        if self.matched is not None and not account_number:
            if str(pin).strip() == self.matched.pin:
                account = self.matched
        elif account_number:
            account = self._store.verify(account_number, pin)
        if account is not None:
            self.verified = account
            return account
        self.attempts += 1
        return None


# Voice-brain tool: the model relays the digits it heard; the SERVER decides (the
# model never sees a real PIN or account number to compare against).
VERIFY_CALLER_TOOL = {
    "type": "function",
    "name": "verify_caller",
    "description": (
        "Verify the caller's identity from the PIN (and account number, if you asked for "
        "one) they just gave you. The check happens server-side — call this with exactly "
        "the digits they said and follow the returned instruction."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pin": {"type": "string", "description": "the PIN the caller said, digits only"},
            "account_number": {"type": "string",
                               "description": "the account number they said (omit if not asked)"},
        },
        "required": ["pin"],
    },
}
