"""Sign-up inserts made on the Inserts screen: form ↔ document, the half-sheet HTML,
the fit to the half-sheet, the screen itself, and a week carrying one."""
import os

import pytest
from fastapi.testclient import TestClient

from builder.pipeline import forms, insert_chat, inserts, model, pdf as pdfmod, render
from builder.pipeline import week as weekmod

FORM = {
    "title": ["  Church  Fellowship Night "], "eyebrow": ["Sign-Up"], "when": ["Sunday, August 16 · 5:30 p.m."], "where": [""],
    "blurb": ["Chicken tenders\nwill be provided.\n\n\nBring a <dish> & a friend."],
    "row_type": ["line", "short", "heading", "checks", "check", "short", "line"],
    "row_text": ["Name(s):", "Number attending:", "We plan to bring:", "Main dish, Side, Dessert", "Nursery needed (ages 0–3)", "How many children?", ""],
    "row_indent": ["0", "0", "0", "0", "0", "1", "0"],
    "return_note": ["Please return by Sunday, August 9."], "logo": ["on"],
}


def test_form_to_doc_and_html():
    doc = inserts.form_to_doc(FORM)
    assert doc["title"] == "Church Fellowship Night" and doc["logo"] is True and doc["footer"] is False
    assert doc["blurb"] == "Chicken tenders will be provided.\n\nBring a <dish> & a friend."
    assert len(doc["rows"]) == 6 and doc["rows"][5] == {"type": "short", "text": "How many children?", "indent": True}  # the empty row is dropped
    html = inserts.render(doc)
    assert html.count("<h1>Church Fellowship Night</h1>") == 2  # the same half-sheet twice
    assert html.count('<p class="blurb justify">') == 4 and "Bring a &lt;dish&gt; &amp; a friend." in html
    assert '<span class="label">Name(s):</span>' in html  # "(s)" is part of the word, not a parenthetical
    assert 'Nursery needed <span class="paren">(ages 0–3)</span>' in html
    assert html.count('class="opt"') == 6 and 'class="row short indent"' in html
    assert html.count("logo-cover.png") == 2 and "919 East Shiloh" not in html
    with pytest.raises(ValueError, match="title"):
        inserts.form_to_doc({**FORM, "title": [" "]})
    with pytest.raises(ValueError, match="kind of row"):
        inserts.form_to_doc({**FORM, "row_type": ["bogus"] * 7})
    with pytest.raises(ValueError, match="too many rows"):
        inserts.form_to_doc({**FORM, "row_type": ["line"] * 30, "row_text": ["x"] * 30, "row_indent": ["0"] * 30})


def test_slugs_never_collide(store):
    assert inserts.slugify("37th Community Thanksgiving Meal!") == "37th-community-thanksgiving-meal"
    assert inserts.slugify("…") == "insert"
    assert inserts.new_slug("Thanksgiving Meal", store) == "thanksgiving-meal-2"  # the hand-built one keeps its name
    assert inserts.new_slug("New", store) == "new-2"                               # /inserts/new is the blank form
    store.save_insert("bake-sale", inserts.clean({"title": "Bake Sale"}))
    assert inserts.new_slug("Bake sale", store) == "bake-sale-2"
    assert inserts.available(store)["bake-sale"] == "Bake Sale" and "thanksgiving-meal" in inserts.available(store)
    store.delete_insert("bake-sale")


def test_build_fits_or_refuses(tmp_var):
    doc = inserts.form_to_doc(FORM)
    with pdfmod.Renderer() as r:
        res = inserts.build("fits", doc, r)
        assert res["scale"] == 1.0 and res["png"].exists() and "full size" in inserts.fit_state("fits")
        info = pdfmod.pdf_info(res["pdf"])
        assert info["pages"] == 1 and [round(x) for x in info["mediabox"][0]] == [0, 0, 792, 612]  # letter, landscape
        crowded = {**doc, "rows": doc["rows"] + [{"type": "line", "text": f"Question {i}:", "indent": False} for i in range(5)]}
        res = inserts.build("fits", crowded, r)
        assert 0.7 <= res["scale"] < 1.0 and "over-full" in inserts.fit_state("fits")  # shrunk together rather than cut off
        hopeless = {**doc, "blurb": "Words and more words. " * 150, "rows": crowded["rows"] * 2}
        with pytest.raises(ValueError, match="does not fit"):
            inserts.build("fits", hopeless, r)
        assert not res["pdf"].exists() and not res["png"].exists()  # no stale proof of the earlier version
        assert "does not fit" in inserts.fit_state("fits")


def test_week_carries_a_made_insert(store, tmp_path):
    store.save_insert("fellowship-night", inserts.form_to_doc(FORM))
    week = weekmod.load_week("2026-11-01")
    week = forms.form_to_week(week, {"sermon_title": ["T"], "sermon_scripture": ["Nehemiah 1"], "calendar_lines": [""],
                                     "insert_fellowship-night": ["on"], "insert_thanksgiving-meal": ["on"], "insert_../etc": ["on"]})
    assert week["inserts"] == ["fellowship-night", "thanksgiving-meal"]
    out = render.write_outputs(week, store, out_dir=tmp_path)
    assert "Church Fellowship Night" in out["insert:fellowship-night"].read_text()
    assert "Thanksgiving Meal" in out["insert:thanksgiving-meal"].read_text()  # the hand-built file still renders
    store.delete_insert("fellowship-night")
    assert "insert:fellowship-night" not in render.write_outputs(week, store, out_dir=tmp_path)


@pytest.fixture()
def client(tmp_var, store):
    os.environ.setdefault("SESSION_SECRET", "test-secret")
    from builder.app import main
    main._store = store
    c = TestClient(main.app, follow_redirects=False)
    store.add_user("inserts@example.com", None, "editor")
    c.get(f"/auth/{store.create_magic_link(store.user_by_email('inserts@example.com')['id'])}")
    return c


def test_inserts_screen(client, store, monkeypatch):
    anon = TestClient(client.app, follow_redirects=False)
    for path in ("/inserts", "/inserts/new", "/inserts/x/insert.pdf"):
        assert anon.get(path).status_code == 303, path
    saved = []
    monkeypatch.setattr(weekmod, "save_week", lambda w, *a: saved.append(w))  # the real 2026-11-01 week.json is not touched

    r = client.get("/inserts/new?week=2026-11-01&ann=0")
    first = weekmod.load_week("2026-11-01")["announcements"][0]
    assert r.status_code == 200 and f'name="title" value="{first["title"]}"'.replace("’", "&#39;") in r.text.replace("’", "&#39;")
    assert 'name="week" value="2026-11-01"' in r.text

    data = {k: v if k.startswith("row_") else v[0] for k, v in FORM.items()}
    r = client.post("/inserts", data={**data, "week": "2026-11-01"})
    assert r.status_code == 303 and r.headers["location"].startswith("/inserts/church-fellowship-night?m=Saved.+Added+to")
    assert saved[-1]["inserts"][-1] == "church-fellowship-night"
    page = client.get("/inserts/church-fellowship-night").text
    assert "proof.png" in page and 'value="Number attending:"' in page and 'value="1" selected' in page
    assert client.get("/inserts/church-fellowship-night/insert.pdf").content[:4] == b"%PDF"
    assert client.get("/inserts/church-fellowship-night/proof.png").headers["content-type"] == "image/png"
    assert "Church Fellowship Night" in client.get("/inserts").text
    assert 'name="insert_church-fellowship-night"' in client.get("/weeks/2026-11-01").text

    r = client.post("/inserts/church-fellowship-night", data={**data, "title": "Fellowship Supper"})
    assert r.status_code == 303 and store.insert_doc("church-fellowship-night")["title"] == "Fellowship Supper"  # the address stays put
    r = client.post("/inserts/church-fellowship-night", data={**data, "title": ""})
    assert "Not+saved" in r.headers["location"]

    r = client.post("/inserts/church-fellowship-night/attach", data={"date": "2026-11-01"})
    assert r.status_code == 303 and r.headers["location"].startswith("/weeks/2026-11-01?")
    # an earlier insert, or the hand-built one, fills the new-insert form; nothing is saved until Save
    r = client.get("/inserts/new?source=church-fellowship-night&week=2026-11-01")
    assert 'name="title" value="Fellowship Supper"' in r.text and 'value="Number attending:"' in r.text and 'action="/inserts"' in r.text
    assert 'name="week" value="2026-11-01"' in r.text and "This is a copy of" in r.text and len(store.inserts()) == 1
    r = client.get("/inserts/new?source=thanksgiving-meal")
    assert 'value="37th Community Thanksgiving Meal"' in r.text and 'value="How many from your family?"' in r.text
    assert 'name="title" value=""' in client.get("/inserts/new?source=../nope").text
    assert "/inserts/new?source=church-fellowship-night" in client.get("/inserts").text and "/inserts/new?source=thanksgiving-meal" in client.get("/inserts").text
    r = client.post("/inserts", data={**data, "title": "Fellowship Supper"})
    assert r.headers["location"].startswith("/inserts/fellowship-supper?")
    assert store.insert_doc("church-fellowship-night")["title"] == "Fellowship Supper"  # the original is untouched
    for slug in ("church-fellowship-night", "fellowship-supper"):
        assert client.post(f"/inserts/{slug}/delete").status_code == 303
    assert store.inserts() == [] and client.get("/inserts/fellowship-supper").status_code == 404
    assert any(a["action"] == "insert.delete" and "Fellowship Supper" in a["detail"] for a in store.audit(limit=20))


def test_look_settings_and_title_break():
    doc = inserts.form_to_doc({**FORM, "title": ["37th Community |  Thanksgiving Meal"], "title_size": ["large"], "text_size": ["small"],
                               "spacing": ["roomy"], "blurb_align": ["left"]})
    assert doc["title"] == "37th Community Thanksgiving Meal" and doc["title_print"] == "37th Community|Thanksgiving Meal"
    html = inserts.render(doc)
    assert "<h1>37th Community<br>Thanksgiving Meal</h1>" in html and '<p class="blurb left">' in html
    assert 'style="--title: 20pt; --t: 0.92; --g: 1.3"' in html
    plain = inserts.form_to_doc(FORM)
    assert "title_print" not in plain and plain["spacing"] == "normal" and 'style="--title: 17pt; --t: 1; --g: 1"' in inserts.render(plain)
    assert 'style="--title: 17pt; --t: 1; --g: 1"' in inserts.render({k: v for k, v in plain.items() if k not in inserts.LOOK})  # saved before the look settings
    with pytest.raises(ValueError, match="title size"):
        inserts.form_to_doc({**FORM, "title_size": ["huge"]})
    assert insert_chat.for_model(doc)["title"] == "37th Community|Thanksgiving Meal" and "title_print" not in insert_chat.for_model(doc)


class _Resp:
    def __init__(self, out):
        self.content = [type("B", (), {"type": "text", "text": __import__("json").dumps(out)})()]


def test_ai_revise_and_draft(store, tmp_var, monkeypatch):
    built = []
    monkeypatch.setattr(inserts, "build", lambda slug, doc, renderer=None: built.append(slug) or {"scale": 0.92})
    monkeypatch.setattr(inserts, "fit_state", lambda slug: "It fits the half-sheet at full size.")
    start = inserts.form_to_doc(FORM)
    store.save_insert("ai-night", start)
    seen = {}

    def fake(job, **kw):
        job.record("claude-opus-5-5", {"output_tokens": 500})
        seen.update(kw, text=kw["messages"][0]["content"])
        return _Resp(seen["answer"])

    monkeypatch.setattr(insert_chat.model, "complete", fake)
    new = insert_chat.for_model(start)
    new["rows"].append({"type": "check", "text": "I can bring a dessert", "indent": False})
    new["spacing"] = "tight"
    seen["answer"] = {"reply": "Added a dessert checkbox and tightened the spacing.", "changed": True, "doc": new}
    res = insert_chat.revise("ai-night", "add a dessert checkbox, and tighten it up", store=store, user="ed@example.com")
    saved = store.insert_doc("ai-night")
    assert res["changed"] and res["cost"] > 0 and saved["rows"][-1]["text"] == "I can bring a dessert" and saved["spacing"] == "tight"
    assert "92% size" in res["reply"] and built == ["ai-night"]
    assert seen["schema"] is insert_chat.SCHEMA and "Request: add a dessert checkbox" in seen["text"] and '"Number attending:"' in seen["text"] and "fits the half-sheet at full size" in seen["text"]
    hist = store.chat_history(insert_chat.chat_key("ai-night"))
    assert [h["role"] for h in hist] == ["user", "assistant"] and hist[-1]["cost_usd"] > 0

    # a request the form cannot meet changes nothing, and the earlier turns go along as context
    seen["answer"] = {"reply": "The form cannot carry a photo.", "changed": False, "doc": new}
    res = insert_chat.revise("ai-night", "add a photo of the church", store=store)
    assert not res["changed"] and store.insert_doc("ai-night") == saved and built == ["ai-night"]
    assert "Earlier in this conversation" in seen["text"] and "tightened the spacing" in seen["text"]

    # a document that does not pass the form's own rules is refused, not saved
    seen["answer"] = {"reply": "x", "changed": True, "doc": {**new, "title": " "}}
    with pytest.raises(model.ModelError, match="Nothing was saved"):
        insert_chat.revise("ai-night", "remove the title", store=store)
    assert store.insert_doc("ai-night") == saved

    monkeypatch.setattr(insert_chat, "MAX_MESSAGES", 3)
    with pytest.raises(model.SpendCap):
        insert_chat.revise("ai-night", "again", store=store)

    seen["answer"] = {"reply": "Drafted from the announcement.", "changed": True,
                      "doc": {**insert_chat.for_model(inserts.blank()), "title": "Chili|Cook-Off", "when": "Sunday, January 10 · noon"}}
    res = insert_chat.draft("Join us for the chili cook-off on January 10 at noon. Sign up to bring a pot!", store=store, user="ed@example.com")
    assert res["slug"] == "chili-cook-off" and store.insert_doc("chili-cook-off")["title_print"] == "Chili|Cook-Off"
    assert "The text:" in seen["text"] and "chili cook-off on January 10" in seen["text"]
    with pytest.raises(ValueError, match="paste"):
        insert_chat.draft("too short", store=store)
    for slug in ("ai-night", "chili-cook-off"):
        store.delete_insert(slug)
    assert store.chat_history(insert_chat.chat_key("ai-night")) == []


def test_ai_routes(client, store, monkeypatch):
    from builder.app import main
    store.save_insert("route-night", inserts.form_to_doc(FORM))
    page = client.get("/inserts/route-night").text
    assert 'action="/inserts/route-night/chat"' in page and 'name="title_size"' in page
    assert 'action="/inserts/draft"' in client.get("/inserts/new").text
    monkeypatch.setattr(main.insert_chat, "revise", lambda slug, message, **kw: {"reply": "ok", "cost": 0.03, "changed": True})
    r = client.post("/inserts/route-night/chat", data={"message": "bigger title"})
    assert r.status_code == 303 and "Changed" in r.headers["location"] and r.headers["location"].endswith("#chat")

    def off(*a, **k):
        raise model.ModelOff("Model calls are switched off")
    monkeypatch.setattr(main.insert_chat, "revise", off)
    monkeypatch.setattr(main.insert_chat, "draft", off)
    assert "switched+off" in client.post("/inserts/route-night/chat", data={"message": "x"}).headers["location"]
    r = client.post("/inserts/draft", data={"source": "A long enough announcement to draft from.", "week": "2026-11-01"})
    assert r.headers["location"].startswith("/inserts/new?week=2026-11-01&m=Not+drafted")
    monkeypatch.setattr(main.insert_chat, "draft", lambda source, **kw: {"slug": "route-night", "reply": "ok", "cost": 0.04})
    r = client.post("/inserts/draft", data={"source": "A long enough announcement to draft from."})
    assert r.headers["location"].startswith("/inserts/route-night?m=Drafted")
    store.delete_insert("route-night")
