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

Merges into the SAME corpus (data/matches.json, data/games.csv, data/meta.json) that
apa_pull.py writes, so analyze.py and build_site_data.py run unchanged afterward.

  python3 pipeline/backfill.py            every division and team the corpus has ever seen
  python3 pipeline/backfill.py --current  weekly: just the divisions our teams play in now
"""
import json, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apa_pull import (APA, Q_TEAM_MATCHES, Q_MATCH, DATA, RECENT_DAYS,  # noqa: E402
                      load_corpus, merge_matches, save_corpus)

CONCURRENCY = 6
CHECKPOINT = 1000
CURRENT_DAYS = 150   # --current: divisions we've played in this long ago count as "this season"
Q_DIVISION_TEAMS = """query($id:Int!){ division(id:$id){ id name format teams{ id name } } }"""


def main():
    current = "--current" in sys.argv[1:]
    api = APA(); api.mint()
    matches, games = load_corpus()
    print("Starting corpus: %d matches" % len(matches))
    metas = [v["meta"] for v in matches.values()]

    if current:
        # Weekly mode: only the divisions our teams are playing in now, and only their teams.
        ours = {int(t) for t in (json.loads((DATA / "meta.json").read_text()).get("teams") or {})}
        since = time.strftime("%Y-%m-%d", time.localtime(time.time() - CURRENT_DAYS * 86400))
        div_ids = {m["div"] for m in metas if m.get("div") and (m.get("start") or "")[:10] >= since
                   and ({m.get("homeTeam"), m.get("awayTeam")} & ours)}
        team_ids = set()
    else:
        div_ids = {m["div"] for m in metas if m.get("div")}
        team_ids = {t for m in metas for t in (m.get("homeTeam"), m.get("awayTeam")) if t}
    print("Seed: %d divisions, %d teams" % (len(div_ids), len(team_ids)))

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

    # for each team, pull its FULL match list and find matches the corpus lacks (or that are
    # recent enough that APA may still correct them)
    recent = time.strftime("%Y-%m-%d", time.localtime(time.time() - RECENT_DAYS * 86400))

    def fetch_team_matches(tid):
        d = api.q(Q_TEAM_MATCHES, {"id": tid})
        t = (d.get("data") or {}).get("team")
        if not t:
            return tid, []
        ids = [m["id"] for m in (t.get("matches") or [])
               if (m.get("isFinalized") or m.get("isScored"))
               and (str(m["id"]) not in matches or (m.get("startTime") or "")[:10] >= recent)]
        return tid, ids

    new_match_ids = set()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(fetch_team_matches, tid): tid for tid in team_ids}
        done = 0
        for fut in as_completed(futs):
            tid, ids = fut.result()
            new_match_ids.update(ids)
            done += 1
            if done % 100 == 0:
                print("  ...enumerated %d/%d teams, %d match ids to fetch so far" % (done, len(team_ids), len(new_match_ids)))
    print("Matches to fetch: %d" % len(new_match_ids))

    if not new_match_ids:
        print("Nothing new to backfill — corpus is already complete for this team set.")
        return

    def fetch_match(mid):
        d = api.q(Q_MATCH, {"id": mid})
        return mid, (d.get("data") or {}).get("match")

    pulled, failed, batch = 0, 0, []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(fetch_match, mid): mid for mid in new_match_ids}
        for fut in as_completed(futs):
            mid, m = fut.result()
            if m:
                batch.append(m); pulled += 1
            else:
                failed += 1
            if len(batch) >= CHECKPOINT:  # checkpoint: a dropped run keeps what it pulled
                games = merge_matches(matches, games, batch); batch = []
                save_corpus(matches, games)
                print("  ...pulled %d/%d (failed %d)" % (pulled, len(new_match_ids), failed))

    games = merge_matches(matches, games, batch)
    save_corpus(matches, games, **({} if current else {"backfilledTeams": len(team_ids)}))
    print("Backfill done: +%d matches pulled, %d failed." % (pulled, failed))


if __name__ == "__main__":
    main()
