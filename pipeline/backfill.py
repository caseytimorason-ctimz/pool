#!/usr/bin/env python3
"""
Phase 4 backfill — pull COMPLETE match history for every team we've ever faced (and every
team in our divisions), not just the games they played against us. This is what makes
"who does opponent X match up well against" answerable: it needs X's full record, not the
2-3 games we happened to play them.

Scope:
  1. Every division our teams have ever competed in (from data/matches_raw.json).
  2. Every team ever listed in those divisions (division(id){ teams{...} }).
  3. Every team that has appeared as home/away in any match we've already pulled
     (covers cross-division opponents, e.g. playoffs).
  For each team in that set: pull its FULL match list (team.matches has no season filter —
  it returns everything), then fetch any finalized match not already in our archive.

Merges into the SAME data/matches_raw.json, data/games.csv, data/meta.json that apa_pull.py
writes, so analyze.py and build_site_data.py run unchanged afterward.
"""
import csv, json, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apa_pull import APA, Q_TEAM_MATCHES, Q_MATCH, normalize_match, DATA  # noqa: E402

CONCURRENCY = 6
Q_DIVISION_TEAMS = """query($id:Int!){ division(id:$id){ id name format teams{ id name } } }"""


def load_raw():
    p = DATA / "matches_raw.json"
    return json.loads(p.read_text()) if p.exists() else {}


def known_team_ids(raw):
    ids = set()
    for m in raw.values():
        for side in ("home", "away"):
            t = m.get(side) or {}
            if t.get("id"):
                ids.add(t["id"])
    return ids


def known_division_ids(raw):
    return {(m.get("division") or {}).get("id") for m in raw.values() if (m.get("division") or {}).get("id")}


def main():
    api = APA(); api.mint()
    raw = load_raw()
    print("Starting corpus: %d matches already pulled" % len(raw))

    team_ids = known_team_ids(raw)
    div_ids = known_division_ids(raw)
    print("Seed: %d divisions, %d teams already touched" % (len(div_ids), len(team_ids)))

    # expand via Division.teams (division rosters shift slightly season to season)
    added_via_division = 0
    for did in sorted(div_ids):
        d = api.q(Q_DIVISION_TEAMS, {"id": did})
        dv = (d.get("data") or {}).get("division")
        if not dv:
            continue
        for t in (dv.get("teams") or []):
            if t.get("id") and t["id"] not in team_ids:
                team_ids.add(t["id"]); added_via_division += 1
    print("Division enumeration added %d new teams -> %d total target teams" % (added_via_division, len(team_ids)))

    # for each team, pull its FULL match list and find match ids not already in our archive
    def fetch_team_matches(tid):
        d = api.q(Q_TEAM_MATCHES, {"id": tid})
        t = (d.get("data") or {}).get("team")
        if not t:
            return tid, []
        ids = [m["id"] for m in (t.get("matches") or [])
               if (m.get("isFinalized") or m.get("isScored")) and str(m["id"]) not in raw]
        return tid, ids

    new_match_ids = set()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(fetch_team_matches, tid): tid for tid in team_ids}
        done = 0
        for fut in as_completed(futs):
            tid, ids = fut.result()
            new_match_ids.update(ids)
            done += 1
            if done % 25 == 0:
                print("  ...enumerated %d/%d teams, %d new match ids so far" % (done, len(team_ids), len(new_match_ids)))
    print("Total NEW finalized matches to pull: %d" % len(new_match_ids))

    if not new_match_ids:
        print("Nothing new to backfill — corpus is already complete for this team set.")
        return

    def fetch_match(mid):
        d = api.q(Q_MATCH, {"id": mid})
        return mid, (d.get("data") or {}).get("match")

    pulled = 0; failed = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(fetch_match, mid): mid for mid in new_match_ids}
        for fut in as_completed(futs):
            mid, m = fut.result()
            if m:
                raw[str(mid)] = m; pulled += 1
            else:
                failed += 1
            if (pulled + failed) % 100 == 0:
                print("  ...pulled %d/%d (failed %d)" % (pulled, len(new_match_ids), failed))
                (DATA / "matches_raw.json").write_text(json.dumps(raw))  # checkpoint

    (DATA / "matches_raw.json").write_text(json.dumps(raw))
    print("Backfill done: +%d matches pulled, %d failed. Corpus now %d matches total." % (pulled, failed, len(raw)))

    # re-normalize the FULL corpus (old + new) into games.csv / matches.json / meta.json
    matches, games = {}, []
    for mid, m in raw.items():
        meta, _rows, persp = normalize_match(m)
        matches[mid] = {"meta": meta}
        games.extend(persp)
    (DATA / "matches.json").write_text(json.dumps(matches, indent=1))
    cols = ["mid", "pid", "name", "team", "sl", "oppMid", "oppPid", "oppName", "oppSl",
            "win", "pts", "fmt", "date", "matchId"]
    with open(DATA / "games.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for g in games:
            w.writerow({k: g.get(k) for k in cols})
    old_meta = json.loads((DATA / "meta.json").read_text()) if (DATA / "meta.json").exists() else {}
    old_meta.update({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                     "counts": {"matches": len(matches), "games": len(games)},
                     "backfilledTeams": len(team_ids)})
    (DATA / "meta.json").write_text(json.dumps(old_meta, indent=1))
    print("Re-normalized: %d total matches, %d total game-rows -> %s" % (len(matches), len(games), DATA))


if __name__ == "__main__":
    main()
