"""Phase 0 seed extraction: published scroll pages + liturgy docx -> builder/data.

Usage: python -I seed.py <repo> <outdir>
"""
import csv, glob, html, json, os, re, sys, zipfile
from datetime import date

repo, out = sys.argv[1], sys.argv[2]
os.makedirs(f"{out}/seed", exist_ok=True)
os.makedirs(f"{out}/standing", exist_ok=True)

SPELL = [  # docs/knowledge/name-spellings.md
    (r"\bLauren Porter\b", "Laurin Porter"), (r"\bLauren\b(?=[^.]{0,20}Porter)", "Laurin"),
    (r"\bMalachi\b", "Malechi"), (r"\bDoran\b", "Doren"),
    (r"\bMcclamroch\b", "McClamroch"), (r"\bStephen Blair\b", "Steve Blair"),
    (r"\bGiaccaligini\b", "Giaccaglini"), (r"\bJoy ?Riders?\b|\bJoyRiders?\b", "Joy Riders"),
    (r"\bLegoff\b", "LeGoff"),
]
fixes = []

def spell(s, where):
    for pat, rep in SPELL:
        n = re.sub(pat, rep, s)
        if n != s:
            fixes.append((where, s, n)); s = n
    return s

def text(frag):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", frag))).strip()

MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
MONTHS.update({k[:3]: v for k, v in MONTHS.items()}); MONTHS["Sept"] = 9

def role_key(r):
    return re.sub(r"[^a-z]+", "_", r.lower().replace("’", "")).strip("_")

rosters, events, hymns, pew = [], {}, {}, {}
roles_seen = []
for path in sorted(glob.glob(f"{repo}/src/scroll/2026-*.html")):
    sunday = os.path.basename(path)[:-5]
    h = open(path, encoding="utf-8").read()
    # hymns: '#10 <span class="worship-attr">&ldquo;O Worship the King&rdquo;'
    for num, title in re.findall(r"#(\d+)\s*<span[^>]*>\s*&ldquo;(.+?)&rdquo;", h):
        hymns.setdefault(int(num), {}).setdefault(text(title), []).append(sunday)
    for ref, page in re.findall(r'worship-attr">([^<]*?)\s*&middot;\s*Pew Bible,? p\. ?(\d+)', h):
        pew.setdefault(text(ref), {"page": int(page), "sundays": []})["sundays"].append(sunday)
    if sunday < "2026-06-01":
        continue
    row = {"sunday": sunday}
    for role, names in re.findall(r'serve-line"><span class="role">(.*?)</span>\s*&mdash;\s*(.*?)</p>', h):
        k = role_key(text(role))
        if k not in roles_seen:
            roles_seen.append(k)
        row[k] = spell(text(names), f"{sunday} serve/{k}")
    rosters.append(row)
    year = int(sunday[:4])
    for d, t in re.findall(r'cal-line"><span class="cal-date">(.*?)</span>(.*?)</p>', h):
        d = text(d); m = re.match(r"([A-Z][a-z]+)\.? (\d+)", d)
        iso = sunday if d == "Today" else None
        if m and m.group(1) in MONTHS:
            mon = MONTHS[m.group(1)]
            y = year + (1 if mon < int(sunday[5:7]) - 6 else 0)
            iso = date(y, mon, int(m.group(2))).isoformat()
            if "\u2013" in d or " - " in d:
                t = f"({d}) " + t
        t = spell(text(t).lstrip("  "), f"{sunday} calendar")
        events.setdefault((iso or d, t), []).append(sunday)

with open(f"{out}/seed/rosters.csv", "w", newline="") as f:
    w = csv.DictWriter(f, ["sunday"] + roles_seen); w.writeheader(); w.writerows(rosters)
with open(f"{out}/seed/events.csv", "w", newline="") as f:
    w = csv.writer(f); w.writerow(["date", "text", "listed_in_bulletins"])
    for (d, t), s in sorted(events.items()):
        w.writerow([d, t, " ".join(s)])

# hymns from liturgy docx too: lines like '#10 "O Worship the King"'
for path in sorted(glob.glob(f"{repo}/bulletins/*/*[Ll][Ii]turgy*.docx")):
    sunday = path.split("/")[-2]
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8")
    t = re.sub(r"<[^>]+>", "", re.sub(r"</w:p>", "\n", re.sub(r"<w:tab/>", "\t", xml)))
    t = html.unescape(t)
    for num, title in re.findall(r"#\s*(\d+)\s*[\"“](.+?)[\"”]", t):
        hymns.setdefault(int(num), {}).setdefault(title.strip(), []).append(sunday + " (docx)")

hymn_out = {}
for n, titles in sorted(hymns.items()):
    best = max(titles, key=lambda k: len(titles[k]))
    hymn_out[str(n)] = {"title": best, "seen": sorted({s for v in titles.values() for s in v})}
    if len(titles) > 1:
        hymn_out[str(n)]["variants"] = sorted(titles)
json.dump(hymn_out, open(f"{out}/standing/hymns.json", "w"), indent=2, ensure_ascii=False)
json.dump(dict(sorted(pew.items(), key=lambda kv: kv[1]["page"])),
          open(f"{out}/standing/pew-bible-pages.json", "w"), indent=2, ensure_ascii=False)

# staff + welcome from the latest published week, school-break variant from 2026-10-11
def blocks(sunday):
    h = open(f"{repo}/src/scroll/{sunday}.html", encoding="utf-8").read()
    return h, {text(t): [text(p) for p in re.findall(r"<p[^>]*>(.*?)</p>", b, re.S)]
               for t, b in re.findall(r'welcome-block">\s*<h3>(.*?)</h3>(.*?)</div>', h, re.S)}
h, latest = blocks("2026-11-01")
_, brk = blocks("2026-10-11")
staff = [{"name": spell(text(n), "staff"), "role": text(r)} for n, r in
         re.findall(r'staff-line"><span class="name">(.*?)</span>\s*&mdash;\s*(.*?)</p>', h)]
json.dump(staff, open(f"{out}/standing/staff.json", "w"), indent=2, ensure_ascii=False)
welcome = {
    "welcome": latest.get("We Are Glad You Are Here!"),
    "children": {"regular": latest.get("Children & Nursery"), "school_break": brk.get("Children & Nursery")},
    "vision_mission": latest.get("Our Vision & Mission"),
    "_source": "regular + vision from src/scroll/2026-11-01.html; school_break from src/scroll/2026-10-11.html",
}
json.dump(welcome, open(f"{out}/standing/welcome.json", "w"), indent=2, ensure_ascii=False)

print(f"rosters: {len(rosters)} weeks, roles {roles_seen}")
print(f"events: {len(events)} distinct lines; hymns: {len(hymn_out)} "
      f"({sum('variants' in v for v in hymn_out.values())} with title variants); pew refs: {len(pew)}")
print(f"staff: {len(staff)}; welcome variants regular={bool(welcome['children']['regular'])} "
      f"break={bool(welcome['children']['school_break'])}")
print("spelling fixes applied:", len(fixes))
for f_ in fixes[:20]:
    print("  ", f_)
