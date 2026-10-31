"""The Friday email's web side: form round trip, photos, chat edits, the routes."""
import io
import zipfile

import pytest

from builder.pipeline import friday, friday_chat, friday_forms, media


def _png(w=1600, h=900, alpha=False):
    from PIL import Image
    im = Image.new("RGBA" if alpha else "RGB", (w, h), (120, 140, 130, 128) if alpha else (120, 140, 130))
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()


def test_form_round_trip_keeps_the_example():
    doc = friday.load("2026-11-01")
    form = friday_forms.form_of(doc)
    back = friday_forms.form_to_doc(doc, form)
    assert [len(s["items"]) for s in back["sections"]] == [len(s["items"]) for s in doc["sections"]]
    assert back["serve"] == doc["serve"] and back["calendar"] == doc["calendar"] and back["birthdays"] == doc["birthdays"]
    assert back["sections"][0]["items"][0]["body"] == doc["sections"][0]["items"][0]["body"]
    # edit one field, blank an item out, remove another: the rest is untouched
    form["s0_i0_title"] = "New Heading"
    si = next(i for i, s in enumerate(doc["sections"]) if i > 0 and len(s["items"]) >= 2)
    for k in list(form):
        if k.startswith(f"s{si}_i0_"):
            form[k] = ""
    form[f"s{si}_i1_remove"] = "on"
    index_map = {}
    out = friday_forms.form_to_doc(doc, form, index_map)
    assert out["sections"][0]["items"][0]["title"] == "New Heading"
    assert len(out["sections"][si]["items"]) == len(doc["sections"][si]["items"]) - 2
    assert (si, 0) not in index_map and (si, 1) not in index_map and index_map.get((0, 0)) == 0


def test_serve_and_dated_parsers():
    lines = friday_forms.parse_serve("Ushers: A, B\n- Infants: C\nAcolytes:")
    assert lines == [{"role": "Ushers", "names": "A, B"}, {"role": None, "names": "Infants: C"}, {"role": "Acolytes", "names": ""}]
    assert friday_forms.serve_text(lines).splitlines() == ["Ushers: A, B", "- Infants: C", "Acolytes:"]
    with pytest.raises(ValueError):
        friday_forms.parse_serve("no colon here")
    rows = friday_forms.parse_dated("2026-11-05: Bob & Sue Jones, Al\n2026-11-03: Zed", "Birthdays")
    assert rows == [{"date": "2026-11-03", "names": ["Zed"]}, {"date": "2026-11-05", "names": ["Bob & Sue Jones", "Al"]}]
    with pytest.raises(ValueError):
        friday_forms.parse_dated("Nov 3: Zed", "Birthdays")
    assert friday_forms.paragraphs("a\n b\n\n\nc") == ["a b", "c"]


def test_media_resizes_and_names_by_content(tmp_var):
    img = media.save_image("2026-11-08", _png(), "Chili Supper.PNG")
    assert img["name"].startswith("2026-11-08-") and img["name"].endswith(".jpg") and img["alt"] == "Chili Supper"
    assert img["src"].endswith("/media/" + img["name"]) and media.path_of(img["name"]).exists()
    from PIL import Image
    im = Image.open(media.path_of(img["name"]))
    assert im.size == (1200, 675) and img["width"] == 520
    again = media.save_image("2026-11-08", _png(), "other.png")
    assert again["name"] == img["name"]  # same bytes, same file
    png = media.save_image("2026-11-08", _png(300, 400, alpha=True), "logo.png")
    assert png["name"].endswith(".png") and png["width"] == 400
    with pytest.raises(ValueError):
        media.save_image("2026-11-08", b"not an image", "x.jpg")
    with pytest.raises(ValueError):
        media.save_image("2026-11-08", b"%PDF-1.4", "x.pdf")
    with pytest.raises(ValueError):
        media.path_of("../../etc/passwd")
    assert img["name"] in media.week_media("2026-11-08")


def test_notes_zip_yields_rtf_and_pictures():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Notes.rtfd/TXT.rtf", b"{\\rtf1 hello}")
        z.writestr("Notes.rtfd/photo.png", _png(10, 10))
        z.writestr("__MACOSX/Notes.rtfd/._photo.png", b"junk")
    rtf, name, images = media.extract_notes_zip(buf.getvalue())
    assert rtf == b"{\\rtf1 hello}" and name == "TXT.rtf" and [n for n, _ in images] == ["photo.png"]


def test_chat_edits_by_pointer(monkeypatch, tmp_path):
    from builder import config
    monkeypatch.setattr(config, "BULLETINS_DIR", tmp_path)
    doc = friday.load(config.REPO / "bulletins/2026-11-01/friday.json")
    new, note = friday_chat.apply_edit(doc, "/sections/0/items/0/when", '"Sunday, 5:30 p.m."')
    assert new["sections"][0]["items"][0]["when"] == "Sunday, 5:30 p.m." and note.startswith("set ")
    new, _ = friday_chat.apply_edit(new, "/calendar/-", '{"date": "2026-11-20", "items": ["Thing"]}')
    assert new["calendar"][-1]["date"] == "2026-11-20"
    n = len(new["sections"][0]["items"])
    new, _ = friday_chat.apply_edit(new, "/sections/0/items/0", "__delete__")
    assert len(new["sections"][0]["items"]) == n - 1
    with pytest.raises(ValueError):
        friday_chat.apply_edit(new, "/push/campaign_id", '"x"')
    with pytest.raises(ValueError):
        friday_chat.apply_edit(new, "/subject", "42")  # fails validation: not a string
    assert "Subject:" in friday_chat.outline(new)


@pytest.fixture
def email_client(tmp_var, store, monkeypatch, tmp_path):
    import os
    import shutil
    os.environ.setdefault("SESSION_SECRET", "test-secret")
    from builder import config
    from builder.app import main
    from fastapi.testclient import TestClient
    bdir = tmp_path / "bulletins"
    shutil.copytree(config.BULLETINS_DIR / "2026-11-01", bdir / "2026-11-01")
    monkeypatch.setattr(config, "BULLETINS_DIR", bdir)
    main._store = store
    client = TestClient(main.app, follow_redirects=False)
    store.add_user("em@example.com", None, "editor")
    token = store.create_magic_link(store.user_by_email("em@example.com")["id"])
    assert client.get(f"/auth/{token}").status_code == 303
    yield client
    client.cookies.clear()


def test_email_page_start_form_photo_items_and_media(email_client, store, monkeypatch):
    c = email_client
    d = "2026-11-08"  # no week.json: the scaffold uses the tables
    r = c.get(f"/weeks/{d}/email")
    assert r.status_code == 200 and "Start this week" in r.text
    assert c.post(f"/weeks/{d}/email/new").status_code == 303
    html = c.get(f"/weeks/{d}/email").text
    assert 'name="s0_intro"' in html and 'name="calendar_lines"' in html and "Friday Email" in html and "Sending needs the publisher role" in html
    doc = friday.load(d)
    assert doc["friday"] == "2026-11-06" and doc["serve"] and doc["calendar"]

    form = friday_forms.form_of(doc)
    r = c.post(f"/weeks/{d}/email/item?action=add&si=0", data=form)
    assert r.status_code == 303 and r.headers["location"].endswith("#s0_i0")
    doc = friday.load(d)
    form = friday_forms.form_of(doc)
    form["s0_i0_title"] = "Chili Supper"
    form["s0_i0_body"] = "Bring a pot.\n\nAnd a bowl."
    form["s0_i0_link_url"] = "https://fpccorinth.org/chili"
    files = {"s0_i0_photo": ("Chili Night.png", _png(800, 600), "image/png")}
    r = c.post(f"/weeks/{d}/email/form", data=form, files=files)
    assert r.status_code == 303 and "Saved" in r.headers["location"], r.headers["location"]
    doc = friday.load(d)
    it = doc["sections"][0]["items"][0]
    assert it["title"] == "Chili Supper" and it["body"] == ["Bring a pot.", "And a bowl."] and it["link"] == {"label": "Learn more", "url": "https://fpccorinth.org/chili"}
    assert it["image"]["alt"] == "Chili Night" and it["image"]["src"].startswith("http") and doc["media"][0]["src"] == it["image"]["src"]
    assert friday.html_path(d).exists() and "Chili Supper" in friday.html_path(d).read_text()

    # the photo is public (mail clients fetch it without a login) and immutable
    name = it["image"]["src"].rsplit("/", 1)[1]
    c.cookies.clear()
    r = c.get(f"/media/{name}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/") and "immutable" in r.headers["cache-control"]
    assert c.get("/media/../etc/passwd").status_code in (404, 400)
    assert c.get("/media/2026-11-08-0000000000.jpg").status_code == 404
    assert c.get(f"/weeks/{d}/email").status_code == 303  # the page itself still needs a login
    token = store.create_magic_link(store.user_by_email("em@example.com")["id"])
    c.get(f"/auth/{token}")

    # preview, remove the photo, remove the item
    assert "Chili Supper" in c.get(f"/weeks/{d}/email/preview.html").text
    form = friday_forms.form_of(doc)
    form["s0_i0_image_remove"] = "on"
    c.post(f"/weeks/{d}/email/form", data=form)
    doc = friday.load(d)
    assert doc["sections"][0]["items"][0]["image"] is None and doc["media"]  # the shelf keeps it
    r = c.post(f"/weeks/{d}/email/item?action=attach&si=0&ii=0&src={doc['media'][0]['src']}", data=friday_forms.form_of(doc))
    assert friday.load(d)["sections"][0]["items"][0]["image"]["src"] == doc["media"][0]["src"]
    r = c.post(f"/weeks/{d}/email/item?action=remove&si=0&ii=0", data=friday_forms.form_of(friday.load(d)))
    assert r.status_code == 303 and friday.load(d)["sections"][0]["items"] == []

    # bad serve line: nothing saved
    form = friday_forms.form_of(friday.load(d))
    form["serve"] = "nonsense"
    r = c.post(f"/weeks/{d}/email/form", data=form)
    assert "Not+saved" in r.headers["location"]

    # refresh pulls from the tables; push needs a publisher
    r = c.post(f"/weeks/{d}/email/refresh/calendar", data=friday_forms.form_of(friday.load(d)))
    assert r.status_code == 303 and "Refreshed" in r.headers["location"]
    assert c.post(f"/weeks/{d}/email/push", data={"test_to": "a@b.co"}).status_code == 403


def test_email_push_route_for_publisher(email_client, store, monkeypatch):
    c = email_client
    d = "2026-11-01"
    store.add_user("pub2@example.com", None, "publisher")
    token = store.create_magic_link(store.user_by_email("pub2@example.com")["id"])
    c.cookies.clear()
    c.get(f"/auth/{token}")
    from builder.app import main
    calls = {}

    def fake_push(sunday, *, store, test_to=None, user=None, list_name=None):
        calls["args"] = (sunday, test_to, user)
        doc = friday.load(sunday)
        doc["push"] = {"campaign_id": "c1", "activity_id": "a1", "name": "The Friday Email · October 30, 2026", "created_at": "2026-10-29T15:00:00+00:00",
                       "test_sent_to": list(test_to or []), "scheduled": None, "html_sha": "abc"}
        friday.save(doc)
        return doc["push"]
    monkeypatch.setattr(main.friday_push, "push", fake_push)
    friday.build(d)
    r = c.post(f"/weeks/{d}/email/push", data={"test_to": "one@example.com, two@example.com"})
    assert r.status_code == 303 and "Draft" in r.headers["location"], r.headers["location"]
    assert calls["args"] == (d, ["one@example.com", "two@example.com"], "pub2@example.com")
    html = c.get(f"/weeks/{d}/email").text
    assert "Open in Constant Contact" in html and "content has changed" in html  # sha differs: stale
    r = c.post(f"/weeks/{d}/email/push", data={"test_to": "bad address"})
    assert "bad+address" in r.headers["location"]
