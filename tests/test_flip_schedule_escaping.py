"""Flip-schedule entries are rendered with |safe, so they must be escaped here.

get_flip_schedule builds each entry as an HTML string, and both home.html and
flip_schedule.html render it through `| safe`. The stock/cross Name, TrayID and
TrayPosition were interpolated raw, and a maintainer's schedule shows records
owned by OTHER users -- so another user's crafted Name reached a supervisor's
page as live markup.

The CSP (nonce-based script-src, no 'unsafe-inline') blocks script execution,
so this was never remote code execution; but img-src allows https:, so a
beacon or arbitrary layout injection still worked. Escaping here is the actual
fix -- the |safe was only safe by accident.
"""
from contextlib import contextmanager
from unittest.mock import patch

from flymanager.app import db as _app_db  # noqa: F401  (breaks a circular import)
from flymanager.utils.mongo.helpers import get_flip_schedule

HOSTILE = '<img src=x onerror=alert(1)>"\'&'
HELPERS = "flymanager.utils.mongo.helpers"


def _stock(**overrides):
    stock = {"UniqueID": "s1", "Name": HOSTILE, "User": "mallory",
             "TrayID": HOSTILE, "TrayPosition": "1",
             "NextFlipDates": "2026-09-01", "Status": "Maintained"}
    stock.update(overrides)
    return stock


@contextmanager
def _schedule_for(stock):
    """Access resolution and flip-day preferences are not what is under test."""
    with patch("flymanager.utils.mongo.access.get_maintainable_stocks",
               return_value=[stock]), \
         patch("flymanager.utils.mongo.access.get_maintainable_crosses",
               return_value=[]), \
         patch("flymanager.utils.mongo.user_data.get_user_flip_days",
               return_value=["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]):
        yield get_flip_schedule("victim", object()) or {}


def _entries(schedule):
    out = []
    for entries in schedule.values():
        for entry in entries:
            out.append(entry if isinstance(entry, str)
                       else (entry.get("description") or entry.get("display_string") or ""))
    return out


def test_a_hostile_name_is_escaped_not_emitted_raw():
    with _schedule_for(_stock()) as schedule:
        entries = _entries(schedule)
    assert entries, "expected at least one schedule entry"
    body = " ".join(entries)
    # "onerror" surviving as escaped TEXT is fine and expected -- what must
    # not survive is a real tag the browser would parse.
    assert "<img" not in body
    assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_a_hostile_tray_id_is_escaped():
    with _schedule_for(_stock(Name="harmless")) as schedule:
        body = " ".join(_entries(schedule))
    assert "<img" not in body
    assert "&lt;img" in body


def test_an_ordinary_name_is_untouched():
    """Escaping must not mangle the genotype-ish names these records really
    carry -- brackets and plus signs are ordinary here, not markup."""
    with _schedule_for(_stock(Name="w[1118]", TrayID="T1")) as schedule:
        body = " ".join(_entries(schedule))
    assert "w[1118]" in body
    assert "T1 - 1" in body


def test_the_anchor_survives_when_a_request_context_exists():
    """Escaping the VALUES must not escape the anchor built around them, or
    every schedule row turns into visible tag soup on the page."""
    from flymanager.app import create_app
    with patch("flymanager.app.get_settings", return_value={
            "lab_info": {"lab_name": "L", "admin_name": "A", "admin_email": "a@b.c"},
            "theme": {"accent_color": "#000", "dark_mode": False}}):
        app = create_app()
    with app.test_request_context("/"):
        with _schedule_for(_stock(Name="w[1118]", TrayID="T1")) as schedule:
            body = " ".join(_entries(schedule))
    assert "<a href=" in body
    assert "w[1118]</a>" in body
