"""Headless rendering: HTML → PDF, panel overflow measurement, PNG proofs, PDF comparison.

Uses Playwright's bundled Chromium through its API (not the CLI) because
Ubuntu 24.04 blocks the unprivileged user-namespace sandbox. PNGs come from
pymupdf, not pdftoppm. Both decisions are recorded in docs/basecamp-discovery.md.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

PANEL_ORDER = [(8, 1), (2, 7), (6, 3), (4, 5)]  # (left, right) booklet page per PDF sheet

_MEASURE_JS = """
() => [...document.querySelectorAll('.panel')].map((panel) => {
  const grid = panel.querySelector('.liturgy-grid');
  const inner = panel.firstElementChild;
  const pr = panel.getBoundingClientRect();
  const cs = getComputedStyle(panel);
  const bottomLimit = pr.bottom - parseFloat(cs.paddingBottom);
  let maxBottom = 0;
  panel.querySelectorAll('*').forEach(el => {
    const r = el.getBoundingClientRect();
    if (r.height > 0 && r.bottom > maxBottom) maxBottom = r.bottom;
  });
  return {
    id: panel.id,
    label: panel.dataset.pageLabel,
    overflow_px: Math.max(0, panel.scrollHeight - panel.clientHeight),
    grid_overflow_px: grid ? Math.max(0, grid.scrollHeight - grid.clientHeight) : 0,
    content_past_bottom_px: Math.max(0, Math.round((maxBottom - bottomLimit) * 100) / 100),
    inner_height_px: inner ? inner.getBoundingClientRect().height : null,
  };
})
"""


class Renderer:
    """One browser for many renders. Use as a context manager."""

    def __init__(self) -> None:
        self._pw = None
        self.browser = None

    def __enter__(self) -> "Renderer":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self.browser = self._pw.chromium.launch()
        return self

    def __exit__(self, *exc) -> None:
        if self.browser:
            self.browser.close()
        if self._pw:
            self._pw.stop()

    def _open(self, html_path: Path):
        page = self.browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        page.evaluate("() => document.fonts.ready")
        return page

    def pdf(self, html_path: Path, pdf_path: Path) -> Path:
        page = self._open(html_path)
        try:
            page.pdf(path=str(pdf_path), prefer_css_page_size=True, print_background=True)
        finally:
            page.close()
        return pdf_path

    def measure(self, html_path: Path) -> list[dict]:
        page = self._open(html_path)
        try:
            page.emulate_media(media="print")
            return page.evaluate(_MEASURE_JS)
        finally:
            page.close()

    def pdf_and_measure(self, html_path: Path, pdf_path: Path) -> list[dict]:
        page = self._open(html_path)
        try:
            page.pdf(path=str(pdf_path), prefer_css_page_size=True, print_background=True)
            page.emulate_media(media="print")
            return page.evaluate(_MEASURE_JS)
        finally:
            page.close()


def overflowing(measurements: Iterable[dict], tolerance_px: float = 0.5) -> list[dict]:
    return [m for m in measurements if max(m["overflow_px"], m["grid_overflow_px"], m["content_past_bottom_px"]) > tolerance_px]


def panel_pngs(pdf_path: Path, out_dir: Path, dpi: int = 110) -> dict[int, Path]:
    """Split each booklet sheet into its two panels. Returns {page_number: png}."""
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    out: dict[int, Path] = {}
    with pymupdf.open(pdf_path) as doc:
        for sheet_index, page in enumerate(doc):
            if sheet_index >= len(PANEL_ORDER):
                break
            left_no, right_no = PANEL_ORDER[sheet_index]
            rect = page.rect
            mid = rect.x0 + rect.width / 2
            for page_no, clip in ((left_no, pymupdf.Rect(rect.x0, rect.y0, mid, rect.y1)),
                                  (right_no, pymupdf.Rect(mid, rect.y0, rect.x1, rect.y1))):
                pix = page.get_pixmap(dpi=dpi, clip=clip)
                path = out_dir / f"panel-{page_no}.png"
                pix.save(path)
                out[page_no] = path
    return dict(sorted(out.items()))


def page_pngs(pdf_path: Path, out_dir: Path, dpi: int = 110, prefix: str = "page") -> list[Path]:
    import pymupdf

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    with pymupdf.open(pdf_path) as doc:
        for i, page in enumerate(doc, start=1):
            path = out_dir / f"{prefix}-{i}.png"
            page.get_pixmap(dpi=dpi).save(path)
            paths.append(path)
    return paths


def pdf_info(pdf_path: Path) -> dict:
    import pymupdf

    with pymupdf.open(pdf_path) as doc:
        fonts = set()
        for page in doc:
            for f in page.get_fonts(full=True):
                fonts.add(f[3])
        return {
            "pages": len(doc),
            "mediabox": [list(map(float, p.mediabox)) for p in doc],
            "fonts": sorted(fonts),
        }


def _norm_text(s: str) -> str:
    s = s.replace(" ", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def compare_pdfs(a: Path, b: Path, dpi: int = 60, threshold: int = 40) -> list[dict]:
    """Per page: whether the extracted text matches and what fraction of pixels differ."""
    import numpy as np
    import pymupdf

    results = []
    with pymupdf.open(a) as da, pymupdf.open(b) as db:
        for i in range(max(len(da), len(db))):
            if i >= len(da) or i >= len(db):
                results.append({"page": i + 1, "missing": True})
                continue
            pa, pb = da[i], db[i]
            ta, tb = _norm_text(pa.get_text()), _norm_text(pb.get_text())
            xa = pa.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            xb = pb.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
            ia = np.frombuffer(xa.samples, dtype=np.uint8).reshape(xa.height, xa.width)
            ib = np.frombuffer(xb.samples, dtype=np.uint8).reshape(xb.height, xb.width)
            h, w = min(ia.shape[0], ib.shape[0]), min(ia.shape[1], ib.shape[1])
            diff = np.abs(ia[:h, :w].astype(int) - ib[:h, :w].astype(int)) > threshold
            results.append({
                "page": i + 1,
                "text_equal": ta == tb,
                "text_a_words": len(ta.split()),
                "text_b_words": len(tb.split()),
                "pixel_diff_pct": round(float(diff.mean()) * 100, 2),
                "size_equal": ia.shape == ib.shape,
            })
    return results


def text_diff(a: Path, b: Path) -> list[str]:
    """Unified diff of extracted text, for the acceptance test's failure message."""
    import difflib

    import pymupdf

    def lines(p: Path) -> list[str]:
        with pymupdf.open(p) as d:
            return [_norm_text(ln) for pg in d for ln in pg.get_text().splitlines() if _norm_text(ln)]

    return list(difflib.unified_diff(lines(a), lines(b), fromfile=str(a), tofile=str(b), lineterm="", n=0))
