"""The data bag: schema, gem search/get/add, profiles+notes, markdown import."""

from __future__ import annotations

from pathlib import Path

from voice_local import db

REPO_KB = Path(__file__).resolve().parents[1] / "kb"


def _conn(tmp_path):
    return db.connect(tmp_path / "t.db")


def test_add_and_get_gem(tmp_path):
    conn = _conn(tmp_path)
    gem = db.add_gem(conn, name="Kin no Yu", city="Kobe", pitch="Gold water at 8am.",
                     tags="onsen, morning")
    assert gem["id"] == "kobe-kin-no-yu"
    assert gem["tags"] == ["onsen", "morning"]
    assert db.get_gem(conn, "kobe-kin-no-yu")["name"] == "Kin no Yu"


def test_get_gem_fuzzy_matches_voice_model_ids(tmp_path):
    conn = _conn(tmp_path)
    db.add_gem(conn, name="Motomachi Koukashita", city="kobe", pitch="Bars under tracks.")
    # voice models hand back approximate underscore ids
    assert db.get_gem(conn, "motomachi_koukashita")["id"] == "kobe-motomachi-koukashita"
    assert db.get_gem(conn, "no-such-gem-anywhere") is None


def test_search_ranks_tag_and_name_hits(tmp_path):
    conn = _conn(tmp_path)
    db.add_gem(conn, name="Quiet Onsen", city="kobe", pitch="Peaceful.", tags="onsen,quiet")
    db.add_gem(conn, name="Loud Bar", city="kobe", pitch="An onsen-themed cocktail bar.",
               tags="bar,nightlife")
    db.add_gem(conn, name="Kyoto Temple", city="kyoto", pitch="Zen.", tags="temple")
    got = db.search_gems(conn, city="kobe", query="quiet onsen morning")
    assert [g["id"] for g in got][0] == "kobe-quiet-onsen"
    assert all(g["city"] == "kobe" for g in got)
    # empty query returns city gems (no crash, deterministic)
    assert len(db.search_gems(conn, city="kobe", query="")) == 2


def test_add_gem_upserts_same_id(tmp_path):
    conn = _conn(tmp_path)
    db.add_gem(conn, name="Kin no Yu", city="kobe", pitch="v1")
    db.add_gem(conn, name="Kin no Yu", city="kobe", pitch="v2 better")
    assert db.get_gem(conn, "kobe-kin-no-yu")["pitch"] == "v2 better"
    assert conn.execute("SELECT count(*) FROM gems").fetchone()[0] == 1


def test_profile_brief_and_notes(tmp_path):
    conn = _conn(tmp_path)
    assert db.profile_brief(conn, "999999") == ""
    db.ensure_profile(conn, "123456", name="Boyan Lin", phone="+16506567722")
    db.add_note(conn, "123456", "loves onsen")
    db.add_note(conn, "123456", "no allergies")
    brief = db.profile_brief(conn, "123456")
    assert "Boyan Lin" in brief and "loves onsen" in brief and "no allergies" in brief


def test_import_markdown_idempotent(tmp_path):
    conn = _conn(tmp_path)
    counts = db.import_markdown(conn, REPO_KB)
    assert counts["gems"] >= 8 and counts["profiles"] >= 2
    again = db.import_markdown(conn, REPO_KB)
    assert again == counts
    gem = db.get_gem(conn, "kobe-arima-onsen-kin-no-yu-at-opening")
    assert gem and "gold-water" in gem["pitch"]
    assert "onsen" in gem["tags"]
    # profile bodies and notes both arrive
    brief = db.profile_brief(conn, "123456")
    assert "Food-first" in brief and "Nada sake" in brief
    # notes not duplicated on re-import
    n = conn.execute("SELECT count(*) FROM notes WHERE account='123456'").fetchone()[0]
    assert n == 2


def test_import_jsonl_seeds_and_reports_errors(tmp_path):
    conn = _conn(tmp_path)
    f = tmp_path / "batch.jsonl"
    f.write_text(
        '{"name":"Yabaton Honten","city":"nagoya","area":"osu","tags":"food,miso",'
        '"price":"$$","booking":"walk-in","pitch":"Miso katsu institution.",'
        '"details":"Order the waraji katsu."}\n'
        'not json\n'
        '{"name":"No Details","city":"nagoya","pitch":"x"}\n'
        '{"name":"Weird Fields","city":"hakone","pitch":"Fine.","details":"Fine.",'
        '"price":"cheap","booking":"telepathy"}\n')
    res = db.import_jsonl(conn, f)
    assert res["imported"] == 2
    assert len(res["errors"]) == 2
    gem = db.get_gem(conn, "nagoya-yabaton-honten")
    assert gem["price"] == "$$" and gem["booking"] == "walk-in"
    weird = db.get_gem(conn, "hakone-weird-fields")
    assert weird["price"] == "" and weird["booking"] == ""
    # idempotent upsert
    assert db.import_jsonl(conn, f)["imported"] == 2
    assert conn.execute("SELECT count(*) FROM gems").fetchone()[0] == 2


def test_profile_brief_groups_topic_notes(tmp_path):
    conn = _conn(tmp_path)
    db.ensure_profile(conn, "555555", name="Mika")
    db.add_note(conn, "555555", "trip: mid-November, two people")
    db.add_note(conn, "555555", "taste: quiet onsen, hates crowds")
    db.add_note(conn, "555555", "taste: quiet onsen, hates crowds")   # dupe collapses
    db.add_note(conn, "555555", "just rambling context")              # unprefixed
    db.add_note(conn, "555555", "trip: now 3 nights in Hakone, was 2")
    brief = db.profile_brief(conn, "555555")
    assert brief.count("quiet onsen") == 1
    trip_block = brief.split("trip:\n")[1].split("taste:")[0]
    assert "mid-November" in trip_block and "now 3 nights" in trip_block
    assert "notes:\n- just rambling context" in brief


def test_resolve_city_exact_fuzzy_and_miss(tmp_path):
    conn = _conn(tmp_path)
    db.add_gem(conn, name="A", city="hakone", pitch="x.")
    db.add_gem(conn, name="B", city="gujo-hachiman", pitch="y.")
    assert db.resolve_city(conn, "Hakone") == "hakone"
    assert db.resolve_city(conn, "Hakone town") == "hakone"
    assert db.resolve_city(conn, "gujo") == "gujo-hachiman"
    assert db.resolve_city(conn, "paris") == ""
    assert db.resolve_city(conn, "") == ""


def test_city_guide_caps_ranks_and_labels(tmp_path):
    conn = _conn(tmp_path)
    for i in range(40):
        db.add_gem(conn, name=f"Spot {i}", city="nagoya", pitch=f"Pitch {i}.",
                   tags=f"tag{i % 12}", details="d" * (i * 10))
    guide = db.city_guide(conn, "nagoya")
    lines = guide.splitlines()
    assert lines[0].startswith("[City guide: nagoya — 30 spots")
    assert len(lines) == 31  # header + 30
    # richest gem (i=39) made the cut; poorest (i=0) did not
    assert any("nagoya-spot-39" in line for line in lines)
    assert db.city_guide(conn, "atlantis") is None


def test_city_guide_diversity_guard(tmp_path):
    conn = _conn(tmp_path)
    # 20 rich same-tag gems + 10 poorer varied ones; the cap must let variety in
    for i in range(20):
        db.add_gem(conn, name=f"Ramen {i}", city="tokyo", pitch="Slurp.",
                   tags="ramen", details="d" * 500)
    for i in range(10):
        db.add_gem(conn, name=f"Other {i}", city="tokyo", pitch="Nice.",
                   tags=f"vibe{i}", details="d" * 10)
    guide = db.city_guide(conn, "tokyo")
    body = guide.splitlines()[1:]
    assert sum ("| ramen" in line for line in body) <= 20
    assert any("vibe0" in line for line in body)  # variety survived the flood


def test_city_guide_reserves_orbit_slots(tmp_path):
    conn = _conn(tmp_path)
    for i in range(35):
        db.add_gem(conn, name=f"K {i}", city="kobe", pitch="k.", tags=f"t{i % 9}",
                   details="d" * 100)
    db.add_gem(conn, name="Kin no Yu back door", city="arima", pitch="Steam.",
               tags="onsen", details="d" * 50)
    guide = db.city_guide(conn, "kobe")
    assert "day trips to arima" in guide.splitlines()[0]
    assert any("day trip: arima" in line for line in guide.splitlines())
