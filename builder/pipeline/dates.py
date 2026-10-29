"""Church-year dates and date formatting.

The cover subtitle ("Twenty-third Sunday after Pentecost") is derived here
unless the content form supplies a real Subtitle. Labels follow the ones the
church has actually printed (see src/_data/scrollBulletins.json): Sundays
after Epiphany, Sundays of Lent, Palm Sunday, Easter Sundays, Pentecost,
Trinity Sunday, then Sundays after Pentecost counted from Pentecost itself.
"""
from __future__ import annotations

from datetime import date, timedelta

ORDINALS = [
    "Zeroth", "First", "Second", "Third", "Fourth", "Fifth", "Sixth", "Seventh", "Eighth", "Ninth",
    "Tenth", "Eleventh", "Twelfth", "Thirteenth", "Fourteenth", "Fifteenth", "Sixteenth",
    "Seventeenth", "Eighteenth", "Nineteenth", "Twentieth", "Twenty-first", "Twenty-second",
    "Twenty-third", "Twenty-fourth", "Twenty-fifth", "Twenty-sixth", "Twenty-seventh",
    "Twenty-eighth", "Twenty-ninth",
]


def ordinal_word(n: int) -> str:
    return ORDINALS[n] if 0 <= n < len(ORDINALS) else f"{n}th"


def parse_date(s: str) -> date:
    return date.fromisoformat(s)


def long_date(d: date | str) -> str:
    """'November 1, 2026'"""
    d = parse_date(d) if isinstance(d, str) else d
    return f"{d.strftime('%B')} {d.day}, {d.year}"


def month_day(d: date | str) -> str:
    """'November 3'"""
    d = parse_date(d) if isinstance(d, str) else d
    return f"{d.strftime('%B')} {d.day}"


def short_month_day(d: date | str) -> str:
    """'Nov 3' (used on the public calendar page)."""
    d = parse_date(d) if isinstance(d, str) else d
    return f"{d.strftime('%b')} {d.day}"


def easter(year: int) -> date:
    """Gregorian Easter (Anonymous algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def next_sunday(today: date | None = None) -> date:
    today = today or date.today()
    days = (6 - today.weekday()) % 7
    return today + timedelta(days=days)


def advent_start(year: int) -> date:
    christmas = date(year, 12, 25)
    fourth = christmas - timedelta(days=(christmas.weekday() + 1) % 7 or 7)
    return fourth - timedelta(weeks=3)


def liturgical_day(d: date | str) -> str:
    """Best-effort label for the cover. The volunteer can override it."""
    d = parse_date(d) if isinstance(d, str) else d
    y = d.year
    e = easter(y)
    ash = e - timedelta(days=46)
    palm = e - timedelta(days=7)
    pentecost = e + timedelta(days=49)
    advent = advent_start(y)

    if d == e:
        return "Easter Sunday"
    if d == palm:
        return "Palm Sunday"
    if d == e - timedelta(days=3):
        return "Maundy Thursday"
    if d == e - timedelta(days=2):
        return "Good Friday"
    if d == ash:
        return "Ash Wednesday"
    if d == pentecost:
        return "Pentecost Sunday"
    if d == pentecost + timedelta(days=7):
        return "Trinity Sunday"
    if d == date(y, 12, 25):
        return "Christmas Day"
    if d == date(y, 12, 24):
        return "Christmas Eve"

    if d.weekday() != 6:
        return long_date(d)

    if advent <= d < date(y, 12, 25):
        n = (d - advent).days // 7 + 1
        return f"{ordinal_word(n)} Sunday of Advent"
    if d > date(y, 12, 25):
        return "First Sunday after Christmas"
    if d < date(y, 1, 6):
        return "Second Sunday after Christmas" if d.day > 1 else "First Sunday after Christmas"
    if d < ash:
        first_after_epiphany = date(y, 1, 6) + timedelta(days=(6 - date(y, 1, 6).weekday()) % 7 or 7)
        if d < first_after_epiphany:
            return "Epiphany Sunday"
        n = (d - first_after_epiphany).days // 7 + 1
        return f"{ordinal_word(n)} Sunday after Epiphany"
    if d < palm:
        first_lent = ash + timedelta(days=(6 - ash.weekday()) % 7)
        n = (d - first_lent).days // 7 + 1
        return f"{ordinal_word(n)} Sunday of Lent"
    if d < pentecost:
        n = (d - e).days // 7 + 1
        return f"{ordinal_word(n)} Sunday of Easter"
    n = (d - pentecost).days // 7
    return f"{ordinal_word(n)} Sunday after Pentecost"
