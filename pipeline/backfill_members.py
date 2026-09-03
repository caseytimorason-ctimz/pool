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
import csv, json, sys, time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apa_pull import APA, Q_TEAM_MATCHES, Q_MATCH, normalize_match, DATA  # noqa: E402

CONCURRENCY = 6
Q_MEMBER_PLAYERS = """query($id:Int!){ member(id:$id){ id firstName lastName
  players(current:false){ team{ id } } } }"""


def load_raw():
    p = DATA / "matches_raw.json"
    return json.loads(p.read_text()) if p.exists() else {}


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
    site = DATA.parent / "site" / "data.json"
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
    raw = load_raw()
    known_teams = {t.get("id") for m in raw.values() for t in (m.get("home"), m.get("away")) if t and t.get("id")}
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
                     if (m.get("isFinalized") or m.get("isScored")) and str(m["id"]) not in raw]

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
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        for fut in as_completed({ex.submit(fetch_match, mid): mid for mid in new_match_ids}):
            mid, m = fut.result()
            if m:
                raw[str(mid)] = m; pulled += 1
            else:
                failed += 1
            # Checkpoint-save often (crash safety) but print rarely — at this scale (tens of
            # thousands of matches) a log line every 100 floods the caller with hundreds of
            # notifications. Save every 500, print progress only every 5000.
            if (pulled + failed) % 500 == 0:
                (DATA / "matches_raw.json").write_text(json.dumps(raw))
            if (pulled + failed) % 5000 == 0:
                print("  ...pulled %d/%d (failed %d)" % (pulled, len(new_match_ids), failed))
    (DATA / "matches_raw.json").write_text(json.dumps(raw))
    print("Pulled +%d matches (%d failed). Corpus now %d matches total." % (pulled, failed, len(raw)))

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
                     "counts": {"matches": len(matches), "games": len(games)}})
    (DATA / "meta.json").write_text(json.dumps(old_meta, indent=1))
    print("Re-normalized: %d total matches, %d total game-rows" % (len(matches), len(games)))


if __name__ == "__main__":
    main()
