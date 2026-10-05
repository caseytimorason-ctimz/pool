#!/usr/bin/env python3
"""
Member-centric backfill — closes a real gap the division-scoped backfill.py left behind.

The bug: backfill.py discovered teams by walking divisions CASEY's own teams had been in.
Any season where CASEY sat out (even if his own teammates kept playing) never surfaces a
division/team id, so that teammate's data for that season is silently missing. Confirmed:
Casey sat out Summer 2024 8-ball; Patrick Conlon, Stefano Cabrini, and Keelan von Homan all
played that season on a team our division-scoped pass never found.

Fix: for each given member id, walk THEIR OWN full player history via
member(id){ players(current:false) } — exactly what apa_pull.py does for Casey — to find
every team-season regardless of whether Casey ever shared a division with them. Pull any
newly-discovered team's full match list, then any newly-discovered match.

Usage: python3 pipeline/backfill_members.py <memberId> [<memberId> ...]
       python3 pipeline/backfill_members.py --teammates   (our active-roster teammates only)
"""
import csv, json, sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apa_pull import (APA, Q_TEAM_MATCHES, Q_MATCH, DATA,  # noqa: E402
                      load_corpus, merge_matches, save_corpus)

CONCURRENCY = 6
CHECKPOINT = 1000
Q_MEMBER_PLAYERS = """query($id:Int!){ member(id:$id){ id firstName lastName
  players(current:false){ team{ id } } } }"""


def all_known_member_ids():
    """Every member id (ours + every opponent) currently present anywhere in games.csv."""
    ids = set()
    with open(DATA / "games.csv") as f:
        for r in csv.DictReader(f):
            for k in ("mid", "oppMid"):
                v = r.get(k)
                if v and v.isdigit():
                    ids.add(int(v))
    return ids


def teammate_ids():
    site = DATA.parent / "data.json"
    d = json.loads(site.read_text())
    ids = set()
    for tid in d.get("myActiveTeams", []):
        for p in (d["teams"].get(str(tid)) or d["teams"].get(tid) or {}).get("roster", []):
            if p.get("mid"):
                ids.add(int(p["mid"]))
    return ids


def main():
    args = sys.argv[1:]
    if args == ["--teammates"]:
        member_ids = teammate_ids()
    elif args == ["--all-known"]:
        member_ids = all_known_member_ids()
    else:
        member_ids = {int(a) for a in args}
    if not member_ids:
        sys.exit("No member ids given. Use --teammates or list ids.")
    print("Member-centric sweep for %d member(s): %s" % (len(member_ids), sorted(member_ids)))

    api = APA(); api.mint()
    matches, games = load_corpus()
    known_teams = {t for v in matches.values() for t in (v["meta"].get("homeTeam"), v["meta"].get("awayTeam")) if t}
    print("Teams already in corpus: %d" % len(known_teams))

    def fetch_member_teams(mid):
        d = api.q(Q_MEMBER_PLAYERS, {"id": mid})
        m = (d.get("data") or {}).get("member")
        if not m:
            return mid, [], (None, None)
        tids = [p["team"]["id"] for p in (m.get("players") or []) if p.get("team")]
        return mid, tids, (m.get("firstName"), m.get("lastName"))

    new_team_ids = set()
    done = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for fut in as_completed({ex.submit(fetch_member_teams, mid): mid for mid in member_ids}):
            mid, tids, name = fut.result()
            fresh = [t for t in tids if t not in known_teams]
            new_team_ids.update(fresh)
            done += 1
            if done % 50 == 0:
                print("  ...discovered %d/%d members, %d new teams so far" % (done, len(member_ids), len(new_team_ids)))

    print("New teams discovered beyond existing corpus: %d" % len(new_team_ids))
    if not new_team_ids:
        print("Nothing new — these members' histories were already fully covered.")
        return

    def fetch_team_matches(tid):
        d = api.q(Q_TEAM_MATCHES, {"id": tid})
        t = (d.get("data") or {}).get("team")
        if not t:
            return tid, []
        return tid, [m["id"] for m in (t.get("matches") or [])
                     if (m.get("isFinalized") or m.get("isScored")) and str(m["id"]) not in matches]

    new_match_ids = set()
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for fut in as_completed({ex.submit(fetch_team_matches, tid): tid for tid in new_team_ids}):
            tid, ids = fut.result()
            new_match_ids.update(ids)
    print("New finalized matches to pull: %d" % len(new_match_ids))

    def fetch_match(mid):
        d = api.q(Q_MATCH, {"id": mid})
        return mid, (d.get("data") or {}).get("match")

    pulled = failed = 0
    batch = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for fut in as_completed({ex.submit(fetch_match, mid): mid for mid in new_match_ids}):
            mid, m = fut.result()
            if m:
                batch.append(m); pulled += 1
            else:
                failed += 1
            # Checkpoint often (crash safety) but print rarely — at this scale (tens of
            # thousands of matches) a log line every 100 floods the caller with notifications.
            if len(batch) >= CHECKPOINT:
                games = merge_matches(matches, games, batch); batch = []
                save_corpus(matches, games)
            if (pulled + failed) % 5000 == 0:
                print("  ...pulled %d/%d (failed %d)" % (pulled, len(new_match_ids), failed))
    games = merge_matches(matches, games, batch)
    save_corpus(matches, games)
    print("Pulled +%d matches (%d failed)." % (pulled, failed))

if __name__ == "__main__":
    main()
