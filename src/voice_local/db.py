"""The data bag: hidden gems, guest profiles, and per-guest notes in ONE SQLite
file that is committed to git (data/gems.db) — the repo IS the distribution
channel for curated local knowledge, exactly like the markdown files it
replaces. Writers on the serving box commit/push to publish.

Import path: `vox-local import-md kb/` parses the legacy concierge-kb
markdown (frontmatter + body) so the pivot loses nothing.
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS gems (
    id      TEXT PRIMARY KEY,
    name    TEXT NOT NULL,
    city    TEXT NOT NULL,
    area    TEXT DEFAULT '',
    tags    TEXT DEFAULT '',          -- comma-separated, lowercase
    price   TEXT DEFAULT '',
    phone   TEXT DEFAULT '',
    booking TEXT DEFAULT '',
    source  TEXT DEFAULT 'curator',   -- curator | caller | web-extension
    url     TEXT DEFAULT '',          -- where it was clipped from (extension)
    updated TEXT DEFAULT '',
    pitch   TEXT DEFAULT '',          -- the one-breath spoken sell
    details TEXT DEFAULT ''           -- longer notes (hours, budget, insider)
);
CREATE TABLE IF NOT EXISTS profiles (
    account   TEXT PRIMARY KEY,       -- vox-local account number
    name      TEXT DEFAULT '',
    phones    TEXT DEFAULT '',        -- comma-separated E.164
    home_city TEXT DEFAULT '',
    languages TEXT DEFAULT '',
    tier      TEXT DEFAULT '',
    emergency TEXT DEFAULT '',
    updated   TEXT DEFAULT '',
    body      TEXT DEFAULT ''         -- markdown body: preferences/constraints/history
);
CREATE TABLE IF NOT EXISTS notes (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    account TEXT NOT NULL,
    ts      TEXT NOT NULL,
    note    TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # One connection serves the whole daemon; queries are serialized by the MCP
    # backend's lock, and test harnesses drive the app from a separate thread.
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def _today() -> str:
    return _dt.date.today().isoformat()


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


# ---- gems ---------------------------------------------------------------------


def gem_to_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["tags"] = [t for t in (d.get("tags") or "").split(",") if t]
    return d


def search_gems(conn: sqlite3.Connection, *, city: str = "", query: str = "",
                tags: str = "", limit: int = 6) -> list[dict]:
    """Token-overlap search over name/tags/pitch/details, city-filtered. Every
    query token that hits scores; tag and name hits score double — good enough
    ranking for grounding a voice recommendation, no FTS setup to maintain."""
    rows = conn.execute(
        "SELECT * FROM gems WHERE (? = '' OR city = ?)",
        (city.lower(), city.lower())).fetchall()
    tokens = [t for t in re.findall(r"[a-z0-9]+", f"{query} {tags}".lower()) if len(t) > 2]
    scored: list[tuple[float, sqlite3.Row]] = []
    for row in rows:
        hay_hi = f"{row['name']} {row['tags']}".lower()
        hay_lo = f"{row['pitch']} {row['details']} {row['area']}".lower()
        score = sum(2.0 for t in tokens if t in hay_hi) + sum(1.0 for t in tokens if t in hay_lo)
        if score > 0 or not tokens:
            scored.append((score, row))
    scored.sort(key=lambda x: -x[0])
    return [gem_to_dict(r) for _, r in scored[:limit]]


def get_gem(conn: sqlite3.Connection, gem_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM gems WHERE id = ?", (gem_id,)).fetchone()
    if row is None:
        # Voice models hand back approximate ids ("motomachi_koukashita") — try a
        # normalized/substring match before giving up.
        norm = _slug(gem_id)
        row = conn.execute(
            "SELECT * FROM gems WHERE id LIKE ? OR replace(id,'-','_') = ? LIMIT 1",
            (f"%{norm}%", gem_id.lower())).fetchone()
    return gem_to_dict(row) if row else None


def add_gem(conn: sqlite3.Connection, *, name: str, city: str, pitch: str,
            area: str = "", tags: str = "", price: str = "", phone: str = "",
            booking: str = "", source: str = "caller", url: str = "",
            details: str = "") -> dict:
    city = city.lower().strip()
    gem_id = f"{_slug(city)}-{_slug(name)}"
    tags_norm = ",".join(_slug(t) for t in re.split(r"[,;]", tags) if t.strip())
    conn.execute(
        "INSERT INTO gems (id,name,city,area,tags,price,phone,booking,source,url,updated,"
        "pitch,details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
        "name=excluded.name, pitch=excluded.pitch, tags=excluded.tags, area=excluded.area, "
        "details=excluded.details, url=excluded.url, updated=excluded.updated",
        (gem_id, name.strip(), city, area, tags_norm, price, phone, booking, source,
         url, _today(), pitch.strip(), details))
    conn.commit()
    return gem_to_dict(conn.execute("SELECT * FROM gems WHERE id = ?", (gem_id,)).fetchone())


# ---- profiles & notes -----------------------------------------------------------


def ensure_profile(conn: sqlite3.Connection, account: str, *, name: str = "",
                   phone: str = "") -> None:
    conn.execute(
        "INSERT INTO profiles (account, name, phones, updated) VALUES (?,?,?,?) "
        "ON CONFLICT(account) DO NOTHING", (account, name, phone, _today()))
    conn.commit()


def profile_brief(conn: sqlite3.Connection, account: str, *, max_notes: int = 6) -> str:
    """The warm-start text injected post-verify: header line + profile body +
    recent notes. Empty string when the guest has no profile yet."""
    row = conn.execute("SELECT * FROM profiles WHERE account = ?", (account,)).fetchone()
    if row is None:
        return ""
    head = f"Caller: {row['name'] or 'unknown'} (account {account}"
    for field in ("tier", "home_city", "languages"):
        if row[field]:
            head += f", {field.replace('_', ' ')} {row[field]}"
    head += ")"
    notes = conn.execute(
        "SELECT ts, note FROM notes WHERE account = ? ORDER BY id DESC LIMIT ?",
        (account, max_notes)).fetchall()
    note_lines = [f"- {n['ts']}: {n['note']}" for n in reversed(notes)]
    parts = [head]
    if (row["body"] or "").strip():
        parts.append(row["body"].strip())
    if note_lines:
        parts.append("Recent notes:\n" + "\n".join(note_lines))
    return "\n\n".join(parts)


def add_note(conn: sqlite3.Connection, account: str, note: str) -> None:
    ensure_profile(conn, account)
    conn.execute("INSERT INTO notes (account, ts, note) VALUES (?,?,?)",
                 (account, _dt.datetime.now().strftime("%Y-%m-%d %H:%M"), note.strip()))
    conn.commit()


# ---- legacy markdown import ------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"\A---\n(.*?)\n---\n(.*)\Z", text, re.DOTALL)
    if not m:
        return {}, text
    meta: dict = {}
    for line in m.group(1).splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        v = v.strip().strip('"')
        if v.startswith("[") and v.endswith("]"):
            v = ",".join(x.strip() for x in v[1:-1].split(",") if x.strip())
        meta[k.strip()] = v
    return meta, m.group(2).strip()


def import_markdown(conn: sqlite3.Connection, kb_dir: str | Path) -> dict:
    """One-shot migration from the concierge-kb layout (kb/gems/<city>/<id>.md,
    kb/profiles/<account>.md). Idempotent — reruns upsert."""
    kb = Path(kb_dir)
    n_gems = n_profiles = 0
    for f in sorted(kb.glob("gems/*/*.md")):
        meta, body = _parse_frontmatter(f.read_text())
        if not meta.get("id"):
            continue
        pitch, _, details = body.partition("\n## ")
        conn.execute(
            "INSERT INTO gems (id,name,city,area,tags,price,phone,booking,source,url,"
            "updated,pitch,details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, pitch=excluded.pitch, "
            "tags=excluded.tags, details=excluded.details, updated=excluded.updated",
            (meta["id"], meta.get("name", ""), meta.get("city", "").lower(),
             meta.get("area", ""), meta.get("tags", ""), meta.get("price", ""),
             meta.get("phone", ""), meta.get("booking", ""),
             meta.get("source", "curator"), "", meta.get("updated", _today()),
             pitch.strip(), ("## " + details).strip() if details else ""))
        n_gems += 1
    for f in sorted(kb.glob("profiles/*.md")):
        meta, body = _parse_frontmatter(f.read_text())
        account = str(meta.get("account", f.stem))
        # Notes live in their own table; strip the section out of the body.
        body_main, _, notes_md = body.partition("## Notes")
        conn.execute(
            "INSERT INTO profiles (account,name,phones,home_city,languages,tier,"
            "emergency,updated,body) VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(account) DO UPDATE SET name=excluded.name, body=excluded.body, "
            "phones=excluded.phones, updated=excluded.updated",
            (account, meta.get("name", ""), meta.get("phones", ""),
             meta.get("home_city", ""), meta.get("languages", ""),
             meta.get("tier", ""), meta.get("emergency_contact", ""),
             meta.get("updated", _today()), body_main.strip()))
        for line in notes_md.splitlines():
            m = re.match(r"-\s*([0-9:\- ]+\w*):\s*(.+)", line.strip())
            if m:
                exists = conn.execute(
                    "SELECT 1 FROM notes WHERE account=? AND note=?",
                    (account, m.group(2).strip())).fetchone()
                if not exists:
                    conn.execute("INSERT INTO notes (account, ts, note) VALUES (?,?,?)",
                                 (account, m.group(1).strip(), m.group(2).strip()))
        n_profiles += 1
    conn.commit()
    return {"gems": n_gems, "profiles": n_profiles}


_BOOKING_KINDS = {"", "walk-in", "phone", "online", "via-hotel"}
_PRICE_TIERS = {"", "$", "$$", "$$$", "$$$$"}


def import_jsonl(conn: sqlite3.Connection, path: str | Path) -> dict:
    """Bulk-seed gems from a JSONL file (one gem object per line: name, city,
    pitch, details required; area/tags/price/booking optional). Invalid lines
    are reported, not imported; generated phone numbers are never trusted."""
    imported = 0
    errors: list[str] = []
    for i, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            g = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"line {i}: bad json ({e})")
            continue
        missing = [k for k in ("name", "city", "pitch", "details") if not str(g.get(k, "")).strip()]
        if missing:
            errors.append(f"line {i}: missing {missing}")
            continue
        add_gem(conn,
                name=g["name"], city=g["city"], pitch=g["pitch"],
                area=g.get("area", ""), tags=g.get("tags", ""),
                price=g.get("price", "") if g.get("price", "") in _PRICE_TIERS else "",
                booking=g.get("booking", "") if g.get("booking", "") in _BOOKING_KINDS else "",
                source=g.get("source", "curator"), details=g["details"])
        imported += 1
    return {"imported": imported, "errors": errors}


def export_json(conn: sqlite3.Connection) -> str:
    """Whole-bag JSON dump (debug / diffing / the extension's duplicate check)."""
    return json.dumps({
        "gems": [gem_to_dict(r) for r in conn.execute("SELECT * FROM gems ORDER BY id")],
        "profiles": [dict(r) for r in conn.execute(
            "SELECT account, name, updated FROM profiles ORDER BY account")],
    }, indent=1)
