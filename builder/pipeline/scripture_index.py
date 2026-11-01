"""Scripture index: every passage read in worship, how often, when last, and a link to the text.

The companion to ``hymn_index``: the published scroll pages (``src/scroll/<date>.html``)
are the record of what was read at each service, so the index is rebuilt from them
(plus the one older Markdown bulletin). A worship heading that carries a Bible
reference counts: the Scripture Reading, the calls to meditation and worship, the
assurance of pardon, the Scripture Response, the Words of Institution, the Old and
New Testament readings, the Psalter, the Good Friday readings. The sermon heading
(title and preacher) and the hymn headings do not.

A passage counts once per service however many headings quote it. Every publish
writes ``src/_data/scriptureIndex.json`` for the public ``/scripture/`` page; the
app's Scripture screen computes the same thing live.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from builder import config
from builder.pipeline import dates

BIBLE_VERSION = "ESV"
PASSAGE_URL = "https://www.biblegateway.com/passage/?search={search}&version=" + BIBLE_VERSION

# The 66 books in canonical order, with the spellings the bulletins use.
BOOKS = [
    "Genesis", "Exodus", "Leviticus", "Numbers", "Deuteronomy", "Joshua", "Judges", "Ruth",
    "1 Samuel", "2 Samuel", "1 Kings", "2 Kings", "1 Chronicles", "2 Chronicles", "Ezra", "Nehemiah",
    "Esther", "Job", "Psalm", "Proverbs", "Ecclesiastes", "Song of Solomon", "Isaiah", "Jeremiah",
    "Lamentations", "Ezekiel", "Daniel", "Hosea", "Joel", "Amos", "Obadiah", "Jonah", "Micah", "Nahum",
    "Habakkuk", "Zephaniah", "Haggai", "Zechariah", "Malachi",
    "Matthew", "Mark", "Luke", "John", "Acts", "Romans", "1 Corinthians", "2 Corinthians", "Galatians",
    "Ephesians", "Philippians", "Colossians", "1 Thessalonians", "2 Thessalonians", "1 Timothy", "2 Timothy",
    "Titus", "Philemon", "Hebrews", "James", "1 Peter", "2 Peter", "1 John", "2 John", "3 John", "Jude",
    "Revelation",
]
OLD_TESTAMENT = 39
ALIASES = {
    "Psalms": "Psalm", "Song of Songs": "Song of Solomon", "Canticles": "Song of Solomon",
    "Revelations": "Revelation", "I Samuel": "1 Samuel", "II Samuel": "2 Samuel", "I Kings": "1 Kings",
    "II Kings": "2 Kings", "I Chronicles": "1 Chronicles", "II Chronicles": "2 Chronicles",
    "I Corinthians": "1 Corinthians", "II Corinthians": "2 Corinthians", "I Thessalonians": "1 Thessalonians",
    "II Thessalonians": "2 Thessalonians", "I Timothy": "1 Timothy", "II Timothy": "2 Timothy",
    "I Peter": "1 Peter", "II Peter": "2 Peter", "I John": "1 John", "II John": "2 John", "III John": "3 John",
}
SINGLE_CHAPTER = {"Obadiah", "Philemon", "2 John", "3 John", "Jude"}
BOOK_ORDER = {b: i for i, b in enumerate(BOOKS)}

_BOOK_NAMES = sorted(list(BOOKS) + list(ALIASES), key=len, reverse=True)
_BOOK = re.compile(r"(?<![A-Za-z])(?P<qual>(?:taken from|based on|from|adapted from)\s+)?(?P<book>" + "|".join(re.escape(b) for b in _BOOK_NAMES) + r")\b")
# "1:1–11", "6b, 8", "22:66–23:24", "1–2, 14", "vv. 1–4", "5–16"
_VERSES = r"\d+[a-c]?(?:\s*[–-]\s*(?:\d+:)?\d+[a-c]?)?(?:,\s*\d+[a-c]?(?:\s*[–-]\s*(?:\d+:)?\d+[a-c]?)?)*"
_CHUNK = re.compile(r"\s*(?:vv?\.\s*)?(?P<chapter>\d+)(?P<verses>:" + _VERSES + r")?")
_ONLY_VERSES = re.compile(r"\s*(?:vv?\.\s*)?(?P<verses>" + _VERSES + r")")
_MORE = re.compile(r"\s*;\s*(?=(?:vv?\.\s*)?\d)")
_VERSION = re.compile(r"\((?P<v>ESV|NIV|KJV|NKJV|NASB|NLT|CSB|RSV|NRSV)\)")
_PEW = re.compile(r"Pew Bible,?\s*pp?\.\s*(?P<page>\d+)")
_HEADING = re.compile(r'<p class="worship-heading">(?P<inner>.*?)</p>', re.S)
_SPAN = re.compile(r"<span[^>]*>(?P<text>.*?)</span>", re.S)
_TAGS = re.compile(r"<[^>]+>")
_MD_ITEM = re.compile(r'^\s*(?:- type: "(?P<slot>[^"]*)"|(?P<top>[a-z_]+):)\s*\n(?:\s+(?!- type:)[a-z_]+:.*\n)*?\s+reference: "(?P<ref>[^"]*)"', re.M)


def _clean(s: str) -> str:
    return " ".join(html.unescape(_TAGS.sub("", s)).replace("\xa0", " ").split())


def parse_references(text: str) -> list[dict]:
    """Every Bible reference in a line of text, as printed.

    'Psalm 51:1–2, 14; 42:5' is one reference (two chunks of the same book);
    'Matthew 18:21–35; Matthew 6:12b' is two. A qualifier such as 'from' or
    'based on' is kept as a note, not part of the reference.
    """
    text = html.unescape(text).replace("\xa0", " ").replace("—", "–")
    found = []
    pos = 0
    while True:
        m = _BOOK.search(text, pos)
        if not m:
            break
        book = ALIASES.get(m.group("book"), m.group("book"))
        pos = m.end()
        chunks = []
        while True:
            if book in SINGLE_CHAPTER:
                c = _ONLY_VERSES.match(text, pos)
                if not c:
                    break
                chapter, verses = "1", c.group("verses")
            else:
                c = _CHUNK.match(text, pos)
                if not c:
                    break
                chapter, verses = c.group("chapter"), (c.group("verses") or "")[1:]
            pos = c.end()
            chunks.append((chapter, re.sub(r"\s+", "", verses).replace("-", "–")))
            more = _MORE.match(text, pos)
            if not more or _BOOK.match(text, more.end()):
                break
            pos = more.end()
        if not chunks:
            continue  # a bare book name in prose ("a Psalm of David")
        shown = [(ch, v.replace(",", ", ")) for ch, v in chunks]
        printed = "; ".join(f"{ch}:{v}" if v else ch for ch, v in shown)
        if book in SINGLE_CHAPTER:
            printed = "; ".join(v or ch for ch, v in shown)
        first_chapter, first_verses = chunks[0]
        first_verse = int(re.match(r"\d+", first_verses).group(0)) if first_verses else 0
        search = "; ".join(f"{book} {ch}:{re.sub(r'[a-c]', '', v)}" if v else f"{book} {ch}" for ch, v in chunks)
        found.append({
            "book": book,
            "ref": f"{book} {printed}",
            "chapter": int(first_chapter),
            "verse": first_verse,
            "note": (m.group("qual") or "").strip() or None,
            "url": PASSAGE_URL.format(search=quote(search.replace("–", "-"), safe="")),
        })
    return found


def _split_heading(inner: str) -> tuple[str, str] | None:
    """(slot, reference text) for one worship heading, or None if it carries no reference text."""
    if "#" in inner or "&ldquo;" in inner or "“" in inner:
        return None  # a hymn, an anthem, a sermon title, the catechism
    span = _SPAN.search(inner)
    if span:
        slot, text = inner[:span.start()], span.group("text")
        if ":" in slot:  # "Scripture Reading: John 12:12–15 <span>Claire Tull</span>"
            head, tail = slot.split(":", 1)
            if _clean(tail):
                slot, text = head, tail
    elif ":" in inner:
        slot, text = inner.split(":", 1)
    else:
        return None
    slot = _clean(slot).lstrip("*").strip().rstrip(":").strip()
    if not slot or slot.lower().startswith("sermon"):
        return None
    return slot, _clean(text)


def scan_scroll_page(text: str, date: str) -> tuple[list[dict], list[dict]]:
    """(readings, headings that name a reading but give no reference) on one page."""
    readings, unparsed = [], []
    for m in _HEADING.finditer(text):
        split = _split_heading(m.group("inner"))
        if not split:
            continue
        slot, body = split
        refs = parse_references(_PEW.sub("", _VERSION.sub("", body)))
        version = _VERSION.search(body)
        pew = _PEW.search(body)
        for r in refs:
            readings.append({**r, "date": date, "slot": slot, "version": version.group("v") if version else None,
                             "pew_page": int(pew.group("page")) if pew else None})
        if not refs and "reading" in slot.lower():
            unparsed.append({"date": date, "slot": slot, "text": body})
    return readings, unparsed


def scan_markdown_bulletin(text: str) -> list[dict]:
    m = re.search(r"^date: (\d{4}-\d{2}-\d{2})", text, re.M)
    if not m:
        return []
    date = m.group(1)
    out = []
    for item in _MD_ITEM.finditer(text):
        slot = item.group("slot") or item.group("top").replace("_", " ").title().replace(" To ", " to ").replace(" Of ", " of ")
        for r in parse_references(item.group("ref")):
            out.append({**r, "date": date, "slot": slot, "version": None, "pew_page": None})
    return out


def scan(repo: Path | None = None) -> tuple[list[dict], list[dict]]:
    repo = repo or config.REPO
    readings, unparsed = [], []
    for page in sorted((repo / "src/scroll").glob("????-??-??.html")):
        r, u = scan_scroll_page(page.read_text(), page.stem)
        readings += r
        unparsed += u
    for md in sorted((repo / "src/bulletins").glob("????-??-??.md")):
        readings += scan_markdown_bulletin(md.read_text())
    readings.sort(key=lambda r: r["date"])
    unparsed.sort(key=lambda r: r["date"])
    return readings, unparsed


def _label(d: str) -> str:
    return f"{dates.month_day(d)}, {d[:4]}"


def _sort_key(row: dict) -> tuple:
    return BOOK_ORDER[row["book"]], row["chapter"], row["verse"], row["ref"]


def build(repo: Path | None = None) -> dict:
    """The index: one row per passage ever read, in Bible order."""
    readings, unparsed = scan(repo)
    by_ref: dict[str, list[dict]] = {}
    for row in readings:
        uses = by_ref.setdefault(row["ref"], [])
        if uses and uses[-1]["date"] == row["date"]:
            prev = uses[-1]
            if row["slot"] not in prev["slot"].split(" / "):
                prev["slot"] += " / " + row["slot"]
            prev["pew_page"] = prev["pew_page"] or row["pew_page"]
            prev["version"] = prev["version"] or row["version"]
            continue
        uses.append(dict(row))
    rows = []
    for ref, uses in by_ref.items():
        first = uses[0]
        rows.append({
            "ref": ref,
            "book": first["book"],
            "testament": "OT" if BOOK_ORDER[first["book"]] < OLD_TESTAMENT else "NT",
            "chapter": first["chapter"],
            "verse": first["verse"],
            "times": len(uses),
            "first": uses[0]["date"],
            "last": uses[-1]["date"],
            "last_label": _label(uses[-1]["date"]),
            "pew_page": next((u["pew_page"] for u in reversed(uses) if u["pew_page"]), None),
            "slots": sorted({s for u in uses for s in u["slot"].split(" / ")}),
            "dates": [{"date": u["date"], "label": _label(u["date"]), "slot": u["slot"], "note": u["note"], "version": u["version"]} for u in uses],
            "url": first["url"],
        })
    rows.sort(key=_sort_key)
    books = []
    for row in rows:
        if books and books[-1]["book"] == row["book"]:
            books[-1]["passages"] += 1
            books[-1]["times"] += row["times"]
        else:
            books.append({"book": row["book"], "testament": row["testament"], "passages": 1, "times": row["times"]})
    services = sorted({r["date"] for r in readings})
    return {
        "generated": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "version": BIBLE_VERSION,
        "services": len(services),
        "since": services[0] if services else None,
        "through": services[-1] if services else None,
        "since_label": _label(services[0]) if services else None,
        "through_label": _label(services[-1]) if services else None,
        "passages": rows,
        "books": books,
        "unparsed": unparsed,
    }


def write_site_data(repo: Path | None = None) -> Path:
    repo = repo or config.REPO
    path = repo / "src/_data/scriptureIndex.json"
    path.write_text(json.dumps(build(repo), ensure_ascii=False, indent=2) + "\n")
    return path
