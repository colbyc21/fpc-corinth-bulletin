"""Build a week: week.json → HTML, PDF, measurements and proof PNGs.

This is the deterministic half of §4 (render, measure, proof). No model call
happens here; the fit loop (Phase 2) wraps :func:`build` and edits
``week.layout`` between runs until :func:`pdf.overflowing` is empty.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from builder import config
from builder.pipeline import names, pdf as pdfmod, render
from builder.pipeline import week as weekmod
from builder.pipeline.store import Store


def build(date: str, *, store: Store | None = None, renderer: pdfmod.Renderer | None = None,
          proofs: bool = True, dpi: int = 110, fix_names: bool = True) -> dict:
    """Render everything for ``date`` into the repo and the proof directory.

    Returns a report dict with output paths, per-panel measurements and the
    name-spelling changes applied. The report is also written to
    ``<var>/proofs/<date>/build.json`` for the proof page.
    """
    t0 = time.time()
    store = store or Store()
    week = weekmod.load_week(date)
    changes = names.fix_week(week) if fix_names else []
    if changes:
        seen = {(c["where"], c["before"]) for c in week["copyedit"].get("changes", [])}
        week["copyedit"].setdefault("changes", []).extend(c for c in changes if (c["where"], c["before"]) not in seen)
        weekmod.save_week(week)

    outputs = render.write_outputs(week, store)
    wdir = config.week_dir(date)
    pdf_path = wdir / f"bulletin-{date}-legal.pdf"

    own = renderer is None
    r = renderer or pdfmod.Renderer().__enter__()
    try:
        measurements = r.pdf_and_measure(outputs["print_html"], pdf_path)
        outputs["print_pdf"] = pdf_path
        for key, html in list(outputs.items()):
            if key.startswith("insert:"):
                ipdf = html.with_suffix(".pdf")
                r.pdf(html, ipdf)
                outputs[key + ":pdf"] = ipdf
    finally:
        if own:
            r.__exit__(None, None, None)

    report = {
        "date": date,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "seconds": round(time.time() - t0, 2),
        "outputs": {k: str(v) for k, v in outputs.items()},
        "measurements": measurements,
        "overflow": pdfmod.overflowing(measurements),
        "name_changes": changes,
        "flags": week["copyedit"].get("flags", []),
        "pdf": pdfmod.pdf_info(pdf_path),
    }
    if proofs:
        proof_dir = config.PROOF_DIR / date
        proof_dir.mkdir(parents=True, exist_ok=True)
        panels = pdfmod.panel_pngs(pdf_path, proof_dir, dpi=dpi)
        report["panels"] = {str(k): str(v) for k, v in panels.items()}
        (proof_dir / "build.json").write_text(json.dumps(report, indent=2))
    store.set_week_status(date, "built", built_at=report["built_at"])
    store.log("build", detail=json.dumps({"date": date, "overflow": len(report["overflow"]), "seconds": report["seconds"]}))
    return report


def last_report(date: str) -> dict | None:
    p = config.PROOF_DIR / date / "build.json"
    return json.loads(p.read_text()) if p.exists() else None
