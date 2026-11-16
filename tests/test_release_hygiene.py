"""What the published plugin promises (catalog rules 11 and 13) must survive every re-vendor.

1.0.20 was hardened by hand at release time; vendor_canvas.py PATCHES now does it. These fail if a
re-vendor of a newer 1guy commit brings back a key-file read, a direct Google reader or a
build-diary note from the private box.
"""
import io
import os
import pathlib
import re
import tokenize

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
VENDOR = ROOT / "og_core" / "vendor" / "app"


def test_no_key_file_and_no_assumed_model(api, home):
    from og_core import board
    b = board.app().board
    assert b.headlines.ENV_PATH == os.devnull
    assert b.headlines.api_key() == ""


def test_direct_google_readers_are_off_for_good(api, home, monkeypatch):
    monkeypatch.setenv("ONEGUY_EMAIL_GLANCE", "on")      # the standalone 1guy's opt-in switches
    monkeypatch.setenv("ONEGUY_CALENDAR", "on")
    from og_core import board
    w = board.app().board.widgets
    assert w.util.CFG["email_glance"] is False and w.util.CFG["calendar_feed"] is False
    with pytest.raises(RuntimeError):
        w._cal_get("/users/me/calendarList", {}, "tok")


def test_no_build_diary_notes_in_the_vendored_copy():
    """QA round refs and the first user's initial in client comments name the private build."""
    bad = []
    for f in VENDOR.rglob("*"):
        if f.suffix not in (".py", ".js", ".css"):
            continue
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"QA r\d+", text):
            bad.append(f"{f.relative_to(VENDOR)}: {m.group(0)}")
        if f.suffix == ".py":
            continue
        for c in re.finditer(r"//[^\n]*|/\*[\s\S]*?\*/", text):
            if re.search(r"\bJ('s)?\b", c.group(0)):
                bad.append(f"{f.relative_to(VENDOR)}: {c.group(0)[:60]!r}")
    assert not bad, bad[:10]


def test_no_python_string_in_the_vendored_copy_reads_an_env_file():
    bad = []
    for f in VENDOR.rglob("*.py"):
        for t in tokenize.generate_tokens(io.StringIO(f.read_text(encoding="utf-8")).readline):
            if t.type == tokenize.STRING and re.search(r"['\"]\.env['\"]", t.string):
                bad.append(f"{f.relative_to(VENDOR)}:{t.start[0]}")
    assert not bad, bad
