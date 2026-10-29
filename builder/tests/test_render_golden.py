"""Golden build: regenerate 2026-11-01 from week.json and compare with the committed files.

Acceptance for Phase 1 (plan §9): a pixel-near-identical PDF and scroll page.
The committed HTML is rendered on the same box so font differences between
the Mac and Ubuntu do not count; only template differences do.
"""
import html
import re
import subprocess
from pathlib import Path

import pytest

from builder import config
from builder.pipeline import pdf as pdfmod, render, week as weekmod

DATE = "2026-11-01"
KNOWN_TEXT_DIFFS = {("Legoff", "LeGoff")}  # confirmed correction applied by the name rules


def _git_show(path: str) -> str:
    return subprocess.run(["git", "show", f"HEAD:{path}"], cwd=config.REPO, capture_output=True, text=True, check=True).stdout


def _norm(doc: str) -> list[str]:
    t = html.unescape(doc)
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<style>.*?</style>", "", t, flags=re.S)
    t = re.sub(r' id="page-\d"', "", t)
    t = re.sub(r">\s+", ">", t)
    t = re.sub(r"\s+<", "<", t)
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"(<[^>]+>)", r"\n\1", t)
    return [ln.strip() for ln in t.split("\n") if ln.strip()]


def _only_known_diffs(a: list[str], b: list[str]) -> bool:
    import difflib
    for line in difflib.unified_diff(a, b, lineterm="", n=0):
        if line.startswith(("---", "+++", "@@")):
            continue
        body = line[1:]
        if line.startswith("-") and not any(k[0] in body for k in KNOWN_TEXT_DIFFS):
            return False
        if line.startswith("+") and not any(k[1] in body for k in KNOWN_TEXT_DIFFS):
            return False
    return True


@pytest.fixture(scope="module")
def built(tmp_path_factory, store):
    out = tmp_path_factory.mktemp("nov1")
    week = weekmod.load_week(DATE)
    from builder.pipeline import names
    names.fix_week(week)
    return render.write_outputs(week, store, out_dir=out), out


def test_print_html_matches_committed(built):
    outputs, _ = built
    committed = _git_show(f"bulletins/{DATE}/bulletin-{DATE}-legal.html")
    assert _only_known_diffs(_norm(committed), _norm(outputs["print_html"].read_text()))


def test_scroll_html_matches_committed(built):
    outputs, _ = built
    committed = _git_show(f"src/scroll/{DATE}.html")
    assert _only_known_diffs(_norm(committed), _norm(outputs["scroll_html"].read_text()))


def test_pdf_page_size_fonts_and_no_overflow(built):
    outputs, out = built
    pdf = out / "print.pdf"
    with pdfmod.Renderer() as r:
        ms = r.pdf_and_measure(outputs["print_html"], pdf)
    assert pdfmod.overflowing(ms) == []
    info = pdfmod.pdf_info(pdf)
    assert info["pages"] == 4
    assert all(box == [0.0, 0.0, 1008.0, 612.0] for box in info["mediabox"])
    assert all("LiberationSerif" in f or "TimesNewRoman" in f for f in info["fonts"])


def test_pdf_pixels_match_committed_html_rendered_here(built):
    outputs, out = built
    golden_html = out / "golden.html"
    golden_html.write_text(_git_show(f"bulletins/{DATE}/bulletin-{DATE}-legal.html"))
    (out / "logo-cover.png").write_bytes((config.ASSETS_DIR / "logo-cover.png").read_bytes())
    golden_pdf, new_pdf = out / "golden.pdf", out / "new.pdf"
    with pdfmod.Renderer() as r:
        r.pdf(golden_html, golden_pdf)
        r.pdf(outputs["print_html"], new_pdf)
    rows = pdfmod.compare_pdfs(golden_pdf, new_pdf, dpi=80)
    assert all(row["pixel_diff_pct"] <= 0.05 for row in rows), rows
    assert all(row["text_a_words"] == row["text_b_words"] for row in rows), rows
