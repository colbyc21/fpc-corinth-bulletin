"""Phase 2 acceptance (plan §9): rebuild Oct 18, Oct 25 and Nov 1 from their
original inputs with no human edits, in a scratch copy of the repo, and
compare with the committed booklets.

    BUILDER_REPO=<scratch clone> python -m builder.scripts.phase2_acceptance 2026-10-18 2026-10-25 2026-11-01 [--no-example DATE]

Reports per week: parse/content/fit cost, flags, overflow, and the text and
pixel comparison against the committed PDF (which was produced by hand).
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from builder import config
from builder.pipeline import fit as fitmod, intake, model, pdf as pdfmod
from builder.pipeline.store import Store


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dates", nargs="+")
    ap.add_argument("--committed", required=True, help="path to the committed repo, for the reference PDFs")
    ap.add_argument("--no-example", action="append", default=[], help="dates to parse without the worked example")
    args = ap.parse_args()
    config.ensure_dirs()
    store = Store()
    start_spend = store.month_spend()
    summary = []
    for d in args.dates:
        t0 = time.time()
        wdir = config.week_dir(d)
        ref_dir = Path(args.committed) / "bulletins" / d
        # start from the original inputs only
        keep = {f"{d} Liturgy.docx", f"{d} Sermon Notes.docx", f"bulletin-content-{d}.rtf", "Announcements.rtf", "logo-cover.png"}
        for p in wdir.iterdir():
            if p.name not in keep:
                p.unlink()
        files = {k: wdir / n for k, n in (("liturgy", f"{d} Liturgy.docx"), ("sermon", f"{d} Sermon Notes.docx"),
                                          ("content", f"bulletin-content-{d}.rtf"), ("announcements", "Announcements.rtf"))}
        files = {k: p for k, p in files.items() if p.exists()}
        row = {"date": d, "inputs": sorted(files)}
        try:
            res = intake.intake(d, store=store, example=None if d in args.no_example else "2026-11-01", **files)
            row["intake_cost"] = res["cost"]
            row["flags"] = res["flags"]
            row["changes"] = len(res["changes"])
            fr = fitmod.build_and_fit(d, store=store)
            row["fit"] = fr["fit"]
            row["overflow"] = [m["label"] for m in fr["report"]["overflow"]]
            ours = wdir / f"bulletin-{d}-legal.pdf"
            theirs = ref_dir / f"bulletin-{d}-legal.pdf"
            if theirs.exists():
                row["compare"] = pdfmod.compare_pdfs(ours, theirs, dpi=60)
                row["text_diff"] = pdfmod.text_diff(ours, theirs)[:80]
        except model.ModelError as e:
            row["error"] = str(e)
        row["seconds"] = round(time.time() - t0, 1)
        summary.append(row)
        print(json.dumps(row, ensure_ascii=False, indent=1))
        sys.stdout.flush()
    total = store.month_spend() - start_spend
    print(f"\nTOTAL model cost for {len(args.dates)} week(s): ${total:.3f}")
    out = config.VAR / "phase2-acceptance.json"
    out.write_text(json.dumps({"total_cost": total, "weeks": summary}, ensure_ascii=False, indent=1))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
