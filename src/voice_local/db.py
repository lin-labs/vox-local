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
"""


def connect(path: str | Path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # One connection serves the whole daemon; queries are serialized by the MCP
    # backend's lock, and test harnesses drive the app from a separate thread.
    conn = sqlite3.connect(p, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    # User data NEVER lives in this git-committed DB: profiles and notes moved to
    # the per-account folders under the state dir (accounts.AccountStore). Any
    # legacy tables from before that move are dropped on open — migrate first
    # (scripts existed at the 2026-07 cutover) if the data still matters.
    conn.executescript("DROP TABLE IF EXISTS notes; DROP TABLE IF EXISTS profiles;")
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


# Day-trip orbits: towns a local mentally files under the anchor city. Guide
# slots left over after the anchor's own gems fill from here, labeled by town.
_ORBITS = {
    "kobe": ["arima"],
    "hakone": ["odawara", "atami", "yugawara", "gotemba", "mishima"],
    "nagoya": ["gifu", "inuyama", "seto", "tokoname", "handa",
               "okazaki", "gujo-hachiman"],
}


def resolve_city(conn: sqlite3.Connection, raw: str) -> str:
    """Map free-form caller speech ('Hakone town', 'downtown Nagoya') to a known
    gems city slug; empty string when nothing in the bag matches."""
    slug = _slug(str(raw or ""))
    if not slug:
        return ""
    cities = {r["city"] for r in conn.execute("SELECT DISTINCT city FROM gems")}
    if slug in cities:
        return slug
    for city in sorted(cities, key=len, reverse=True):
        if city in slug or (len(slug) >= 4 and slug in city):
            return city
    return ""


def _guide_line(row: sqlite3.Row, *, day_trip: bool = False) -> str:
    hook = re.split(r"(?<=[.!?])\s", (row["pitch"] or "").strip())[0][:110]
    bits = [row["id"], row["name"]]
    where = row["city"] if day_trip else (row["area"] or "")
    if where:
        bits.append(f"day trip: {where}" if day_trip else where)
    tags = (row["tags"] or "").replace(",", " ")
    if tags:
        bits.append(tags)
    return "- " + " | ".join(bits) + (f" — {hook}" if hook else "")


def city_guide(conn: sqlite3.Connection, city: str, *, limit: int = 30) -> str | None:
    """The agent's mental map of a city: the top `limit` gems as one compact
    line each (id first, so get_gem is one hop away). Ranked by curation
    richness with a per-tag cap so one theme can't flood the map, and a slice
    of slots reserved for the city's day-trip orbit. None when the city is
    unknown to the bag — the caller's guide has to be honest about that."""
    city = _slug(city)
    home = conn.execute("SELECT * FROM gems WHERE city = ?", (city,)).fetchall()
    if not home:
        return None
    orbit: list[sqlite3.Row] = []
    for town in _ORBITS.get(city, []):
        orbit += conn.execute("SELECT * FROM gems WHERE city = ?", (town,)).fetchall()

    def richness(row: sqlite3.Row) -> int:
        return (len(row["details"] or "") + 2 * len(row["pitch"] or "")
                + (200 if row["source"] == "curator" else 0))

    def pick(rows: list[sqlite3.Row], n: int) -> list[sqlite3.Row]:
        cap = max(3, n // 5)   # diversity guard: one leading tag can't flood the map
        counts: dict[str, int] = {}
        chosen, spill = [], []
        for row in sorted(rows, key=richness, reverse=True):
            bucket = ((row["tags"] or "").split(",")[0] or row["area"] or "misc")
            if counts.get(bucket, 0) < cap and len(chosen) < n:
                counts[bucket] = counts.get(bucket, 0) + 1
                chosen.append(row)
            else:
                spill.append(row)
        return chosen + spill[: n - len(chosen)]

    n_orbit = min(len(orbit), max(limit // 4, limit - len(home))) if orbit else 0
    picked_home = pick(home, limit - n_orbit)
    picked_orbit = pick(orbit, n_orbit)
    towns = sorted({r["city"] for r in picked_orbit})
    head = (f"[City guide: {city} — {len(picked_home) + len(picked_orbit)} spots you "
            f"know by heart{', with day trips to ' + ', '.join(towns) if towns else ''}. "
            "This is YOUR OWN memory of the place — browse it silently; NEVER read it "
            "out, list it, or mention a guide exists. Pick the one or two spots that "
            "fit THIS caller, and query get_gem with the id BEFORE sharing specifics.]")
    lines = [_guide_line(r) for r in picked_home]
    lines += [_guide_line(r, day_trip=True) for r in picked_orbit]
    return "\n".join([head, *lines])


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


def import_markdown(conn: sqlite3.Connection, kb_dir: str | Path,
                    store=None) -> dict:
    """One-shot migration from the concierge-kb layout (kb/gems/<city>/<id>.md,
    kb/profiles/<account>.md). Gems land in the DB; profiles/notes are USER data
    and land in the given accounts.AccountStore dossier (skipped when store is
    None — user stuff never lives in gems.db). Idempotent — reruns upsert."""
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
    for f in (sorted(kb.glob("profiles/*.md")) if store is not None else []):
        meta, body = _parse_frontmatter(f.read_text())
        account = str(meta.get("account", f.stem))
        body_main, _, notes_md = body.partition("## Notes")
        existing = {(ts, text) for ts, text in store.read_notes(account)}
        header_bits = [meta.get("name", "")] + [
            f"{k.replace('_', ' ')}: {meta[k]}" for k in
            ("home_city", "languages", "tier", "emergency_contact") if meta.get(k)]
        seed = [f"personal: {'; '.join(b for b in header_bits if b)}"] if any(
            header_bits) else []
        seed += [f"personal: {line}" for line in body_main.strip().splitlines()
                 if line.strip() and not line.startswith("#")]
        for note in seed:
            if (meta.get("updated", _today()), note) not in existing:
                store.append_note(account, note, ts=meta.get("updated", _today()))
                existing.add((meta.get("updated", _today()), note))
        for line in notes_md.splitlines():
            m = re.match(r"-\s*([0-9:\- ]+\w*):\s*(.+)", line.strip())
            if m and (m.group(1).strip(), m.group(2).strip()) not in existing:
                store.append_note(account, m.group(2).strip(), ts=m.group(1).strip())
                existing.add((m.group(1).strip(), m.group(2).strip()))
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
    }, indent=1)
