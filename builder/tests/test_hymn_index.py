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
    assert two["dates"] == sorted(two["dates"], key=lambda d: d["date"])
    assert [h["number"] for h in idx["hymns"]] == sorted((h["number"] for h in idx["hymns"]), key=hymn_index._number_key)
    assert all(h["in_hymnal"] for h in idx["hymns"]), [h["number"] for h in idx["hymns"] if not h["in_hymnal"]]
