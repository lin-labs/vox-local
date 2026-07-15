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


# Notebook topics: the agent prefixes each note ("trip: two people mid-November")
# so the dossier files itself. trip/reaction notes land in trip.md; everything
# else about the humans lands in persona.md (or a named companion's file).
_TRIP_TOPICS = ("trip", "reaction")


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

    # ---- the notebook (markdown docs beside account.json) -----------------------
    # User data NEVER lives in the git-committed gems DB. Each account folder is
    # a small dossier the host keeps:
    #   trip.md     — exploration and clear-cut plans (trip: / reaction: notes)
    #   persona.md  — the caller themself: family, style, tastes, constraints
    #   <person>.md — one file per named travel companion (bryan_li.md, ...)
    # Every file is timestamped append-only bullets; the whole dossier loads into
    # the agent's context when the account is loaded.

    def _doc_for(self, note: str, person: str) -> str:
        if person.strip():
            slug = re.sub(r"_+", "_",
                          re.sub(r"[^a-z0-9]+", "_", person.lower())).strip("_")
            if slug:
                return f"{slug}.md"
        topic = note.split(":", 1)[0].strip().lower()
        return "trip.md" if topic in _TRIP_TOPICS else "persona.md"

    _DOC_TITLES = {"trip.md": "Trip — plans and exploration",
                   "persona.md": "Persona — the caller, family, and style"}

    def append_note(self, account_number: str, note: str, *, person: str = "",
                    ts: str = "") -> None:
        folder = self._folder(account_number)
        folder.mkdir(parents=True, exist_ok=True)
        doc = self._doc_for(str(note), person)
        p = folder / doc
        if not p.exists():
            title = self._DOC_TITLES.get(
                doc, f"{person.strip() or doc.removesuffix('.md')} — companion")
            p.write_text(f"# {title} (account {account_number})\n\n", encoding="utf-8")
        ts = ts or _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        with p.open("a", encoding="utf-8") as f:
            f.write(f"- {ts} | {' '.join(str(note).split())}\n")

    def _docs(self, account_number: str) -> list[Path]:
        """The dossier files, persona first, trip last, companions between."""
        folder = self._folder(account_number)
        if not folder.is_dir():
            return []
        docs = sorted(p for p in folder.glob("*.md")
                      if p.name not in ("persona.md", "trip.md"))
        persona, trip = folder / "persona.md", folder / "trip.md"
        return ([persona] if persona.exists() else []) + docs + (
            [trip] if trip.exists() else [])

    def read_doc(self, account_number: str, doc: str) -> list[tuple[str, str]]:
        """[(ts, note), ...] oldest first; tolerant of hand-edited lines."""
        p = self._folder(account_number) / doc
        if not p.exists():
            return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line.startswith("- "):
                continue   # headers / hand-written prose stay out of the parse
            ts, sep, text = line[2:].partition(" | ")
            if sep and text.strip():
                out.append((ts.strip(), text.strip()))
            elif line[2:].strip():
                out.append(("", line[2:].strip()))
        return out

    def read_notes(self, account_number: str) -> list[tuple[str, str]]:
        """Every note across the dossier, oldest first (legacy notes.txt included)."""
        notes = []
        for p in self._docs(account_number):
            notes += self.read_doc(account_number, p.name)
        legacy = self._folder(account_number) / "notes.txt"
        if legacy.exists():
            for line in legacy.read_text(encoding="utf-8").splitlines():
                ts, sep, text = line.partition(" | ")
                if sep and text.strip():
                    notes.append((ts.strip(), text.strip()))
        return sorted(notes, key=lambda n: n[0])

    def move_notes(self, account_number: str, target: "AccountStore",
                   target_number: str) -> int:
        """Merge this account's dossier into the target's (timestamps preserved,
        same doc routing) and drop the source files. Returns how many lines moved."""
        moved = 0
        for p in self._docs(account_number):
            person = ("" if p.name in ("persona.md", "trip.md")
                      else p.stem.replace("_", " "))
            for ts, text in self.read_doc(account_number, p.name):
                target.append_note(target_number, text, person=person, ts=ts)
                moved += 1
            p.unlink()
        legacy = self._folder(account_number) / "notes.txt"
        if legacy.exists():
            for ts, text in self.read_notes(account_number):
                target.append_note(target_number, text, ts=ts)
                moved += 1
            legacy.unlink()
        return moved

    def companions(self, account_number: str) -> list[str]:
        """Named travel companions with their own dossier page ('mark kim', ...)."""
        return [p.stem.replace("_", " ") for p in self._docs(account_number)
                if p.name not in ("persona.md", "trip.md")]

    def profile_brief(self, account_number: str) -> str:
        """The dossier as caller context, injected whenever the account loads:
        header + persona + one section per companion + the trip. ALL notes ride
        along (deduped, chronological; the newest line on a topic is the current
        truth). Empty string for a guest with no dossier."""
        acct = self.get(account_number)
        docs = self._docs(account_number)
        legacy = self.read_notes(account_number) if not docs else []
        if not docs and not legacy and acct is None:
            return ""
        head = f"Caller: {(acct.name if acct else '') or 'unknown'} (account {account_number})"
        parts = [head]
        seen: set[str] = set()

        def lines_of(pairs: list[tuple[str, str]]) -> list[str]:
            out = []
            for ts, text in pairs:
                key = " ".join(text.lower().split())
                if not key or key in seen:
                    continue
                seen.add(key)
                out.append(f"- {text} ({ts[:10]})" if ts else f"- {text}")
            return out

        titles = {"persona.md": "Persona (the caller, family, style)",
                  "trip.md": "Trip (chronological; the newest line on a topic is "
                             "the current truth)"}
        for p in docs:
            body = lines_of(self.read_doc(account_number, p.name))
            if body:
                title = titles.get(p.name, f"Companion — {p.stem.replace('_', ' ')}")
                parts.append(f"{title}:\n" + "\n".join(body))
        if legacy:
            body = lines_of(legacy)
            if body:
                parts.append("Notes:\n" + "\n".join(body))
        # Head alone carries no history — callers with an empty dossier get "".
        return "\n\n".join(parts) if len(parts) > 1 else ""


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
