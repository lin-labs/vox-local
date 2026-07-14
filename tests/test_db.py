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
