#!/usr/bin/env python3
"""
Targeted refresh of the CURRENT session.

The full pull walks every team in Casey's history and re-fetches ~54k matches, which takes
hours. During a live session that is the wrong shape: the only rows that change week to week
are the two active teams' match nights, and the eligibility counters in the Races tab are
worthless if they lag the schedule. This pulls just those teams' finalized matches, merges
anything new into games.csv, and leaves the historical corpus untouched.

Safe to re-run: rows are keyed on (matchId, mid, oppMid) so re-pulling a match that's already
in the file is a no-op rather than a double-count.
"""
import csv, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apa_pull import APA, MEMBER_ID, Q_MEMBER, Q_TEAM_MATCHES, Q_MATCH, normalize_match

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
COLS = ["mid", "pid", "name", "team", "sl", "oppMid", "oppPid", "oppName", "oppSl",
        "win", "pts", "fmt", "date", "matchId"]


def main():
    api = APA(); api.mint()
    mem = (api.q(Q_MEMBER, {"id": MEMBER_ID}).get("data") or {}).get("member")
    if not mem:
        sys.exit("member returned null — token/permissions issue")
    tids = [t["id"] for t in (mem.get("teams") or [])]
    print("current teams: %s" % tids)

    existing = set()
    with open(DATA / "games.csv") as f:
        for r in csv.DictReader(f):
            existing.add(r["matchId"])
    print("matches already in games.csv: %d" % len(existing))

    want = []
    for tid in tids:
        t = (api.q(Q_TEAM_MATCHES, {"id": tid}).get("data") or {}).get("team")
        if not t:
            continue
        fin = [m for m in (t.get("matches") or []) if m.get("isFinalized") or m.get("isScored")]
        new = [m["id"] for m in fin if str(m["id"]) not in existing]
        print("  %s (%s): %d finalized, %d new" % (t.get("name"), tid, len(fin), len(new)))
        want += new

    if not want:
        print("nothing new — games.csv is current")
        return

    rows = []
    for i, mid in enumerate(sorted(set(want)), 1):
        m = (api.q(Q_MATCH, {"id": mid}).get("data") or {}).get("match")
        if not m:
            continue
        _meta, _r, persp = normalize_match(m)
        rows += persp
        if i % 5 == 0:
            print("  ...%d/%d" % (i, len(set(want))))
        time.sleep(0.15)

    with open(DATA / "games.csv", "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        for g in rows:
            w.writerow({k: g.get(k) for k in COLS})
    print("appended %d game-rows from %d matches" % (len(rows), len(set(want))))


if __name__ == "__main__":
    main()
