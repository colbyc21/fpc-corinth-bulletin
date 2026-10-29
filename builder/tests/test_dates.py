import json
from datetime import date

import pytest

from builder import config
from builder.pipeline import dates


def test_easter_known_years():
    assert dates.easter(2026) == date(2026, 4, 5)
    assert dates.easter(2027) == date(2027, 3, 28)
    assert dates.easter(2025) == date(2025, 4, 20)


def test_ordinals():
    assert dates.ordinal_word(23) == "Twenty-third"
    assert dates.ordinal_word(1) == "First"


def test_long_and_short_dates():
    assert dates.long_date("2026-11-01") == "November 1, 2026"
    assert dates.month_day("2026-11-03") == "November 3"


def test_next_sunday():
    assert dates.next_sunday(date(2026, 10, 29)) == date(2026, 11, 1)
    assert dates.next_sunday(date(2026, 11, 1)) == date(2026, 11, 1)


# Every Sunday the church has published that follows the computable pattern.
HISTORY = {
    "2026-02-15": "Sixth Sunday after Epiphany",
    "2026-02-18": "Ash Wednesday",
    "2026-02-22": "First Sunday of Lent",
    "2026-03-01": "Second Sunday of Lent",
    "2026-03-22": "Fifth Sunday of Lent",
    "2026-03-29": "Palm Sunday",
    "2026-04-02": "Maundy Thursday",
    "2026-04-05": "Easter Sunday",
    "2026-04-12": "Second Sunday of Easter",
    "2026-05-10": "Sixth Sunday of Easter",
    "2026-05-31": "Trinity Sunday",
    "2026-06-07": "Second Sunday after Pentecost",
    "2026-10-18": "Twenty-first Sunday after Pentecost",
    "2026-11-01": "Twenty-third Sunday after Pentecost",
    "2026-11-29": "First Sunday of Advent",
    "2026-12-20": "Fourth Sunday of Advent",
}


@pytest.mark.parametrize("d,label", HISTORY.items())
def test_liturgical_day_matches_history(d, label):
    assert dates.liturgical_day(d) == label


def test_published_index_is_consistent_where_computable():
    """Dates whose published label is a Pentecost/Lent/Easter count must agree with the formula."""
    entries = json.loads((config.REPO / "src/_data/scrollBulletins.json").read_text())
    checked = 0
    for e in entries:
        label = e["liturgical_day"].replace("The ", "")
        if any(k in label for k in ("after Pentecost", "of Lent", "of Easter", "after Epiphany")):
            assert dates.liturgical_day(e["date"]) == label, e
            checked += 1
    assert checked >= 25
