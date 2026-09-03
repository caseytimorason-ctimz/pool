#!/usr/bin/env python3
"""
Re-normalize an interim snapshot of matches_raw.json into games.csv/matches.json/meta.json
WITHOUT touching the live matches_raw.json a background pull may still be writing to.
Lets Casey see progress from a long-running backfill before it fully completes.
"""
import csv, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apa_pull import normalize_match, DATA  # noqa: E402


def main():
    raw = json.loads((DATA / "matches_raw_snapshot.json").read_text())
    matches, games = {}, []
    for mid, m in raw.items():
        meta, _rows, persp = normalize_match(m)
        matches[mid] = {"meta": meta}
        games.extend(persp)
    (DATA / "matches.json").write_text(json.dumps(matches, indent=1))
    cols = ["mid", "pid", "name", "team", "sl", "oppMid", "oppPid", "oppName", "oppSl",
            "win", "fmt", "date", "matchId"]
    with open(DATA / "games.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for g in games:
            w.writerow({k: g.get(k) for k in cols})
    old_meta = json.loads((DATA / "meta.json").read_text()) if (DATA / "meta.json").exists() else {}
    old_meta.update({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S") + " (interim snapshot)",
                     "counts": {"matches": len(matches), "games": len(games)}})
    (DATA / "meta.json").write_text(json.dumps(old_meta, indent=1))
    print("Interim snapshot normalized: %d matches, %d game-rows" % (len(matches), len(games)))


if __name__ == "__main__":
    main()
