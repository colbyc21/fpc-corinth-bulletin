from builder.pipeline import names

CASES = [
    ("Lauren Porter", "Laurin Porter"),
    ("Malachi Doran", "Malechi Doren"),
    ("Malachi Doren", "Malechi Doren"),
    ("Malechi Doran", "Malechi Doren"),
    ("Mike and Shirley Mcclamroch", "Mike and Shirley McClamroch"),
    ("Dr. Stephen Blair", "Dr. Steve Blair"),
    ("Stephen Blair & Cindy Blair", "Steve Blair & Cindy Blair"),
    ("Brandon Giaccaligini", "Brandon Giaccaglini"),
    ("Jason & Jennifer Legoff", "Jason & Jennifer LeGoff"),
    ("JoyRiders Lunch", "Joy Riders Lunch"),
    ("Joy Rider Trip", "Joy Riders Trip"),
]


def test_every_documented_misspelling_is_corrected():
    for bad, good in CASES:
        fixed, changes = names.fix_text(bad, "t")
        assert fixed == good, (bad, fixed)
        assert changes, bad


def test_correct_text_is_untouched():
    for _, good in CASES:
        fixed, changes = names.fix_text(good, "t")
        assert fixed == good
        assert not changes


def test_fix_week_reports_where():
    week = {
        "serve": {"circle_of_prayer": "Jason & Jennifer Legoff"},
        "announcements": [{"title": "Joy Rider Lunch", "body": "x"}],
        "flowers": None, "calendar": [], "liturgy": {"items": []}, "sermon": {"preacher": "Rev. Waring Porter"},
    }
    changes = names.fix_week(week)
    assert week["serve"]["circle_of_prayer"] == "Jason & Jennifer LeGoff"
    assert week["announcements"][0]["title"] == "Joy Riders Lunch"
    assert {c["where"] for c in changes} == {"serve.circle_of_prayer", "announcements[0].title"}
