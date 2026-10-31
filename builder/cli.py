"""Command line for the pipeline.

    python -m builder init-db                 create the SQLite store and load the seeds
    python -m builder new 2026-11-08          scaffold bulletins/<date>/week.json from rosters + calendar
    python -m builder intake 2026-11-08 --liturgy f.docx [--sermon f.docx] [--content f.rtf] [--announcements f.rtf]
                                              parse the inputs with the model into week.json (shows flags, changes, cost)
    python -m builder build 2026-11-01 [--fit] render HTML/PDF, measure overflow, write proof PNGs; --fit runs the model fit loop on overflow
    python -m builder spend                   this month's model spend by kind and the last calls
    python -m builder measure 2026-11-01      measure only (no PDF)
    python -m builder publish 2026-11-01 [--branch basecamp-test] [--no-push]
    python -m builder compare a.pdf b.pdf     text + pixel comparison per page
    python -m builder extract file.docx       print the docx as text
    python -m builder calendar [--sunday D]   print the back-cover lines and write src/calendar.ics
    python -m builder hymns [--write]         hymn index from the published bulletins; --write refreshes src/_data/hymnIndex.json
    python -m builder add-user email role     create an app user (editor|publisher|admin)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from builder import config


def cmd_init_db(args) -> int:
    from builder.pipeline.store import Store

    config.ensure_dirs()
    s = Store()
    done = s.seed_if_empty()
    print(f"store: {s.path}\nseeded: {done}")
    return 0


def cmd_new(args) -> int:
    from builder.pipeline import calendar_feed, week as weekmod
    from builder.pipeline.store import Store

    s = Store()
    path = weekmod.week_path(args.date)
    if path.exists() and not args.force:
        print(f"{path} exists; use --force to overwrite", file=sys.stderr)
        return 1
    week = weekmod.scaffold(args.date, serve=s.serve_for(args.date), calendar=calendar_feed.week_calendar(s, args.date))
    weekmod.save_week(week, path)
    print(path)
    return 0


def cmd_intake(args) -> int:
    from builder.pipeline import intake, model
    from builder.pipeline.store import Store

    config.ensure_dirs()
    paths = {k: Path(v) for k, v in (("liturgy", args.liturgy), ("sermon", args.sermon), ("content", args.content), ("announcements", args.announcements)) if v}
    for k, p in paths.items():
        if not p.exists():
            print(f"{k}: {p} does not exist", file=sys.stderr)
            return 2
    try:
        res = intake.intake(args.date, store=Store(), example=None if args.no_example else "2026-11-01", **paths)
    except model.ModelError as e:
        print(f"model step failed: {e}", file=sys.stderr)
        return 1
    print(f"wrote {config.week_dir(args.date) / 'week.json'}  cost ${res['cost']:.3f}")
    for j in res["jobs"]:
        for line in j["log"]:
            print("  " + line)
    if res["changes"]:
        print("changes:")
        for c in res["changes"]:
            print(f"  {c['where']}: {c['before']} -> {c['after']}  ({c['reason']})")
    if res["flags"]:
        print("flags:")
        for f in res["flags"]:
            print(f"  {f['where']}: {f['message']}")
    return 0


def cmd_spend(args) -> int:
    from builder.pipeline import model
    from builder.pipeline.store import Store

    s = Store()
    st = model.spend_status(s)
    print(f"this month: ${st['month']:.2f} of ${st['cap']:.0f}  model calls {'on' if st['model_calls'] else 'OFF'}")
    for row in s.month_ledger_by_kind():
        print(f"  {row['kind']:<8} {row['calls']:>3} calls  in {row['input_tokens']:>7}  cached {row['cache_read_tokens']:>7}  out {row['output_tokens']:>6}  ${row['cost_usd']:.3f}")
    for r in s.ledger(limit=args.last):
        print(f"  {r['ts']}  {r['job']:<40} {r['model']:<18} in {r['input_tokens']:>6} w {r['cache_write_tokens']:>6} r {r['cache_read_tokens']:>6} out {r['output_tokens']:>5}  ${r['cost_usd']:.4f}")
    return 0


def cmd_build(args) -> int:
    from builder.pipeline import build

    config.ensure_dirs()
    if args.fit:
        from builder.pipeline import fit as fitmod, model
        from builder.pipeline.store import Store

        try:
            res = fitmod.build_and_fit(args.date, store=Store())
        except model.ModelError as e:
            print(f"fit loop failed: {e}", file=sys.stderr)
            return 1
        report = res["report"]
        if res["fit"]:
            print("fit:", json.dumps(res["fit"], indent=2))
    else:
        report = build.build(args.date, proofs=not args.no_proofs)
    print(json.dumps({k: report[k] for k in ("date", "seconds", "outputs", "pdf")}, indent=2))
    for m in report["measurements"]:
        flag = "  <-- OVERFLOW" if m in report["overflow"] else ""
        print(f"{m['label']:<32} overflow={m['overflow_px']:>5} grid={m['grid_overflow_px']:>5} past_bottom={m['content_past_bottom_px']:>6}{flag}")
    if report["name_changes"]:
        print("name corrections:")
        for c in report["name_changes"]:
            print(f"  {c['where']}: {c['before']} -> {c['after']}")
    return 1 if report["overflow"] else 0


def cmd_measure(args) -> int:
    from builder.pipeline import pdf as pdfmod

    html = config.week_dir(args.date) / f"bulletin-{args.date}-legal.html"
    with pdfmod.Renderer() as r:
        ms = r.measure(html)
    for m in ms:
        print(f"{m['label']:<32} overflow={m['overflow_px']:>5} grid={m['grid_overflow_px']:>5} past_bottom={m['content_past_bottom_px']:>6}")
    return 1 if pdfmod.overflowing(ms) else 0


def cmd_publish(args) -> int:
    from builder.pipeline import publish

    res = publish.publish(args.date, branch=args.branch, push=not args.no_push, correction=args.correction)
    print(json.dumps(res, indent=2))
    return 0


def cmd_compare(args) -> int:
    from builder.pipeline import pdf as pdfmod

    for row in pdfmod.compare_pdfs(Path(args.a), Path(args.b), dpi=args.dpi):
        print(row)
    if args.diff:
        print("\n".join(pdfmod.text_diff(Path(args.a), Path(args.b))))
    return 0


def cmd_extract(args) -> int:
    from builder.pipeline import extract

    p = Path(args.file)
    text = extract.docx_text(p) if p.suffix.lower() == ".docx" else extract.rtf_text(p)
    sys.stdout.write(text)
    return 0


def cmd_calendar(args) -> int:
    from builder.pipeline import calendar_feed, dates
    from builder.pipeline.store import Store

    s = Store()
    sunday = args.sunday or dates.next_sunday().isoformat()
    for line in calendar_feed.week_calendar(s, sunday):
        print(f"{dates.month_day(line['date']):<14} {' · '.join(line['items'])}")
    if args.write:
        for p in calendar_feed.write_site_files(s):
            print("wrote", p)
    return 0


def cmd_hymns(args) -> int:
    from builder.pipeline import hymn_index

    idx = hymn_index.build()
    print(f"{len(idx['hymns'])} hymns at {idx['services']} services, {idx['since']} to {idx['through']}")
    for h in sorted(idx["hymns"], key=lambda h: (-h["times"], h["last"])):
        print(f"  #{h['number']:<5} {h['title']:<44} {h['times']:>2}x  last {h['last']}  {h['url']}")
    for u in idx["unnumbered"]:
        print(f"  (no number) {u['title']}  {u['date']}")
    if args.write:
        print("wrote", hymn_index.write_site_data())
    return 0


def cmd_add_user(args) -> int:
    from builder.pipeline.store import Store

    s = Store()
    uid = s.add_user(args.email, args.name, args.role)
    s.log("user.add", None, f"{args.email} {args.role}")
    print(f"user {uid}: {args.email} ({args.role})")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="builder", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db").set_defaults(fn=cmd_init_db)
    s = sub.add_parser("new"); s.add_argument("date"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_new)
    s = sub.add_parser("intake"); s.add_argument("date")
    for opt in ("--liturgy", "--sermon", "--content", "--announcements"):
        s.add_argument(opt)
    s.add_argument("--no-example", action="store_true", help="do not show the model the 2026-11-01 worked example")
    s.set_defaults(fn=cmd_intake)
    s = sub.add_parser("build"); s.add_argument("date"); s.add_argument("--no-proofs", action="store_true")
    s.add_argument("--fit", action="store_true"); s.set_defaults(fn=cmd_build)
    s = sub.add_parser("spend"); s.add_argument("--last", type=int, default=10); s.set_defaults(fn=cmd_spend)
    s = sub.add_parser("measure"); s.add_argument("date"); s.set_defaults(fn=cmd_measure)
    s = sub.add_parser("publish"); s.add_argument("date"); s.add_argument("--branch"); s.add_argument("--no-push", action="store_true")
    s.add_argument("--correction", help="what was corrected, for the commit subject"); s.set_defaults(fn=cmd_publish)
    s = sub.add_parser("compare"); s.add_argument("a"); s.add_argument("b"); s.add_argument("--dpi", type=int, default=60)
    s.add_argument("--diff", action="store_true"); s.set_defaults(fn=cmd_compare)
    s = sub.add_parser("extract"); s.add_argument("file"); s.set_defaults(fn=cmd_extract)
    s = sub.add_parser("calendar"); s.add_argument("--sunday"); s.add_argument("--write", action="store_true"); s.set_defaults(fn=cmd_calendar)
    s = sub.add_parser("hymns"); s.add_argument("--write", action="store_true"); s.set_defaults(fn=cmd_hymns)
    s = sub.add_parser("add-user"); s.add_argument("email"); s.add_argument("role", choices=["editor", "publisher", "admin"])
    s.add_argument("--name"); s.set_defaults(fn=cmd_add_user)

    args = p.parse_args(argv)
    return args.fn(args)
