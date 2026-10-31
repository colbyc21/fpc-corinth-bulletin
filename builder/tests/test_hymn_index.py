"""The hymn index is read from the published scroll pages."""
from builder.pipeline import hymn_index

PAGE = """
<p class="worship-heading">*The Hymn of Praise: &nbsp;#8 <span class="worship-attr">&ldquo;Praise to the Lord, the Almighty&rdquo; (vv. 1&ndash;3)</span></p>
<p class="worship-heading">Westminster Shorter Catechism #12 <span class="worship-attr">&ldquo;Q.&rdquo;</span></p>
<p class="worship-heading">*Congregational Response: &nbsp;#26 <span class="worship-attr">&ldquo;A Mighty Fortress&rdquo; (v. 4)</span></p>
<p class="worship-heading">*Hymn of Lament: <span class="worship-attr">&ldquo;God, Be Merciful to Me&rdquo;</span></p>
<p class="worship-heading">*Hymn: &nbsp;#59 <span class="worship-attr">&ldquo;I Sing the Mighty Power of God&rdquo;</span></p>
"""


def test_scan_page_reads_numbered_hymns_and_skips_the_catechism():
    sung, unnumbered = hymn_index.scan_scroll_page(PAGE, "2026-03-01")
    assert [(s["number"], s["slot"], s["verses"]) for s in sung] == [
        ("8", "The Hymn of Praise", "vv. 1–3"), ("26", "Congregational Response", "v. 4"), ("59", "Hymn", None)]
    assert unnumbered == [{"date": "2026-03-01", "title": "God, Be Merciful to Me", "slot": "Hymn of Lament", "verses": None}]


def test_a_hymn_split_across_a_service_counts_once():
    page = PAGE + """
<p class="worship-heading">*Hymn: &nbsp;#8 <span class="worship-attr">&ldquo;Praise to the Lord, the Almighty&rdquo; (vv. 4&ndash;5)</span></p>
"""
    repo_pages = {"2026-03-01": page, "2026-03-08": PAGE}
    import builder.pipeline.hymn_index as hi

    orig = hi.scan
    try:
        hi.scan = lambda repo=None: (sorted([r for d, t in repo_pages.items() for r in hi.scan_scroll_page(t, d)[0]], key=lambda r: r["date"]), [])
        idx = hi.build(hymns={"8": {"title": "Praise to the Lord, the Almighty", "tune": "LOBE DEN HERREN"}})
    finally:
        hi.scan = orig
    eight = next(h for h in idx["hymns"] if h["number"] == "8")
    assert eight["times"] == 2 and [d["date"] for d in eight["dates"]] == ["2026-03-01", "2026-03-08"]
    assert eight["dates"][0]["verses"] == "vv. 1–3, 4–5" and eight["dates"][0]["slot"] == "The Hymn of Praise / Hymn"
    assert eight["dates"][0]["parts"] == 2 and eight["dates"][1]["parts"] == 1


def test_markdown_bulletin():
    md = 'layout: x\ndate: 2026-01-25\n\nworship_order:\n  - type: "Hymn"\n    title: "Amazing Grace"\n    number: 202\n    verses: "1,2,3,4"\n'
    assert hymn_index.scan_markdown_bulletin(md) == [{"date": "2026-01-25", "number": "202", "title": "Amazing Grace", "slot": "Hymn", "verses": "vv. 1, 2, 3, 4"}]


def test_index_from_the_repo():
    idx = hymn_index.build()
    rows = {h["number"]: h for h in idx["hymns"]}
    assert idx["services"] >= 40 and idx["since"] == "2026-01-25"
    two = rows["2"]  # Come, Thou Fount: sung four times through Nov 1
    assert two["title"] == "Come, Thou Fount of Every Blessing" and two["times"] >= 4 and two["last"] >= "2026-11-01"
    assert two["url"] == "https://hymnary.org/hymn/HWC1986/2" and two["tune"] == "NETTLETON"
    assert two["dates"] == sorted(two["dates"], key=lambda d: d["date"]) and two["dates"][-1]["label"].endswith(", 2026")
    assert [h["number"] for h in idx["hymns"]] == sorted((h["number"] for h in idx["hymns"]), key=hymn_index._number_key)
    assert all(h["in_hymnal"] for h in idx["hymns"]), [h["number"] for h in idx["hymns"] if not h["in_hymnal"]]
