"""The Friday email renders from friday.json and matches the sent October 30 email."""
import re

import pytest

from builder.pipeline import friday


def test_example_validates_and_renders():
    doc = friday.load("2026-11-01")
    assert doc["friday"] == "2026-10-30"
    html = friday.render(doc)
    lines = friday.text_of_html(html)
    assert "October 30, 2026" in lines and "Those Who Serve this Sunday" in lines
    assert 'width="600"' in html and "@media only screen" in html and "<style" in html
    assert html.count("<tr>") > 20 and "Follow us on Instagram: <strong>@fpc_kidsquest</strong>" in html
    assert '<a href="tel:9012750765"' in html and "<em>Theo of Golden</em>" in html
    assert "Unsubscribe" not in html  # Constant Contact adds its own footer


def test_rich_filter_escapes_first():
    out = str(friday.rich("a <b> & *c* at 662-415-1608 see https://x.org/p @handle"))
    assert "&lt;b&gt;" in out and "&amp;" in out and "<em>c</em>" in out
    assert 'href="tel:6624151608"' in out and 'href="https://x.org/p"' in out and "<strong>@handle</strong>" in out


def test_scaffold_pulls_calendar_and_roster_from_week(store):
    from builder.pipeline import week as weekmod
    week = weekmod.load_week("2026-11-01")
    doc = friday.scaffold("2026-11-01", week)
    friday.validate(doc)
    assert doc["calendar"] == week["calendar"]
    assert doc["serve"][0] == {"role": "Assisting Elder", "names": week["serve"]["assisting_elder"]}
    assert [s["key"] for s in doc["sections"]] == [k for k in friday.SECTION_ORDER if k != "other"]
    assert friday.render(doc)  # empty sections are skipped, the shell still renders


def test_validation_rejects_bad_shapes():
    doc = friday.load("2026-11-01")
    doc["sections"].append({"key": "prepare", "items": []})
    with pytest.raises(ValueError, match="repeated"):
        friday.validate(doc)
    doc = friday.load("2026-11-01")
    doc["sections"][0]["items"][0]["bogus"] = 1
    with pytest.raises(ValueError, match="invalid"):
        friday.validate(doc)
