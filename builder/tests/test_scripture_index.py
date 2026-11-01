"""The scripture index is read from the published scroll pages."""
from builder.pipeline import scripture_index as si

PAGE = """
<p class="worship-heading">Call to Meditation: &nbsp;Colossians 3:1</p>
<p class="worship-heading">*Call to Worship: <span style="font-weight:normal;">taken from Psalm 116:12&ndash;14, 17&ndash;19</span></p>
<p class="worship-heading">*The Hymn of Praise: &nbsp;#8 <span class="worship-attr">&ldquo;Praise to the Lord, the Almighty&rdquo; (vv. 1&ndash;3)</span></p>
<p class="worship-heading">*Responsive Reading: <span style="font-weight:normal;">from Psalm 40, a Psalm of David</span></p>
<p class="worship-heading">Assurance of Divine Pardon: <span style="font-weight:normal;">Romans 8:1; 1 John 1:9</span></p>
<p class="worship-heading">Scripture Reading <span class="worship-attr">2 Corinthians 8:7 (NIV) &middot; Pew Bible, p. 967</span></p>
<p class="worship-heading">Scripture Reading: John 12:12&ndash;15 <span class="worship-attr">Claire Tull</span></p>
<p class="worship-heading">Scripture Reading <span class="worship-attr">(see outline)</span></p>
<p class="worship-heading">Scripture Response: &nbsp;Isaiah 40:6b, 8</p>
<p class="worship-heading">Sermon: <span class="worship-attr">&ldquo;Excel in Giving&rdquo; &middot; Rev. Waring Porter</span></p>
<p class="worship-heading">The Trial before Pilate: <span style="font-weight:normal;">Luke 22:66&ndash;23:24</span></p>
<p class="worship-heading">Westminster Shorter Catechism #12 <span class="worship-attr">&ldquo;Q.&rdquo;</span></p>
"""


def test_parse_references():
    one = lambda s: [(r["ref"], r["chapter"], r["verse"], r["note"]) for r in si.parse_references(s)]
    assert one("Psalm 51:1–2, 14; 42:5") == [("Psalm 51:1–2, 14; 42:5", 51, 1, None)]
    assert one("Matthew 6:12a; 1 John 1:5–10") == [("Matthew 6:12a", 6, 12, None), ("1 John 1:5–10", 1, 5, None)]
    assert one("Jude vv. 1–4") == [("Jude 1–4", 1, 1, None)]
    assert one("Jude 5-16") == [("Jude 5–16", 1, 5, None)]
    assert one("Ezra 4") == [("Ezra 4", 4, 0, None)]
    assert one("based on Revelation 5:12") == [("Revelation 5:12", 5, 12, "based on")]
    assert one("Psalms 100") == [("Psalm 100", 100, 0, None)]
    assert one("a Psalm of David") == []
    assert si.parse_references("Luke 22:66–23:24")[0]["url"] == "https://www.biblegateway.com/passage/?search=Luke%2022%3A66-23%3A24&version=ESV"


def test_scan_page_reads_references_and_skips_hymns_sermon_and_catechism():
    readings, unparsed = si.scan_scroll_page(PAGE, "2026-10-25")
    assert [(r["ref"], r["slot"]) for r in readings] == [
        ("Colossians 3:1", "Call to Meditation"),
        ("Psalm 116:12–14, 17–19", "Call to Worship"),
        ("Psalm 40", "Responsive Reading"),
        ("Romans 8:1", "Assurance of Divine Pardon"),
        ("1 John 1:9", "Assurance of Divine Pardon"),
        ("2 Corinthians 8:7", "Scripture Reading"),
        ("John 12:12–15", "Scripture Reading"),
        ("Isaiah 40:6b, 8", "Scripture Response"),
        ("Luke 22:66–23:24", "The Trial before Pilate"),
    ]
    cor = next(r for r in readings if r["book"] == "2 Corinthians")
    assert cor["version"] == "NIV" and cor["pew_page"] == 967
    assert readings[1]["note"] == "taken from" and readings[2]["note"] == "from"
    assert unparsed == [{"date": "2026-10-25", "slot": "Scripture Reading", "text": "(see outline)"}]


def test_a_passage_quoted_twice_in_a_service_counts_once():
    page = PAGE + '<p class="worship-heading">Call to Meditation: &nbsp;2 Corinthians 8:7</p>'
    orig = si.scan
    try:
        si.scan = lambda repo=None: (sorted([r for d, t in {"2026-10-25": page, "2026-11-01": PAGE}.items() for r in si.scan_scroll_page(t, d)[0]], key=lambda r: r["date"]), [])
        idx = si.build()
    finally:
        si.scan = orig
    cor = next(p for p in idx["passages"] if p["ref"] == "2 Corinthians 8:7")
    assert cor["times"] == 2 and cor["dates"][0]["slot"] == "Scripture Reading / Call to Meditation" and cor["pew_page"] == 967
    assert [p["book"] for p in idx["passages"]] == ["Psalm", "Psalm", "Isaiah", "Luke", "John", "Romans", "2 Corinthians", "Colossians", "1 John"]
    assert idx["books"][0] == {"book": "Psalm", "testament": "OT", "passages": 2, "times": 4}
    assert idx["services"] == 2 and idx["passages"][0]["testament"] == "OT" and idx["passages"][-1]["testament"] == "NT"


def test_markdown_bulletin():
    md = 'layout: x\ndate: 2026-01-25\n\ncall_to_worship:\n  reference: "Psalm 34:1-3"\n\nworship_order:\n  - type: "Scripture Reading"\n    reference: "Luke 6:27-36"\n    bible_link: "x"\n  - type: "Responsive Reading"\n    reference: "Responsive Reading"\n'
    assert [(r["ref"], r["slot"]) for r in si.scan_markdown_bulletin(md)] == [("Psalm 34:1–3", "Call to Worship"), ("Luke 6:27–36", "Scripture Reading")]


def test_index_from_the_repo():
    idx = si.build()
    assert idx["services"] >= 41 and idx["since"] == "2026-01-25"
    rows = {p["ref"]: p for p in idx["passages"]}
    response = rows["Isaiah 40:6b, 8"]  # the weekly Scripture Response
    assert response["times"] >= 36 and response["slots"] == ["Scripture Response"]
    assert rows["Nehemiah 1:1–11"]["pew_page"] == 398 and rows["Luke 6:27–36"]["times"] == 2
    assert [p["ref"] for p in idx["passages"]] == [p["ref"] for p in sorted(idx["passages"], key=si._sort_key)]
    assert all(u["text"] == "(see outline)" for u in idx["unparsed"]), idx["unparsed"]
