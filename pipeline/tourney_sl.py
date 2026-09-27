#!/usr/bin/env python3
"""
Tournament skill-level check (anti-sandbagging).

APA tournaments play everyone at the HIGHER of (a) the SL they ENDED the last session at and
(b) their SL in the current session. Neither the posted tournament roster nor our game history
can be trusted alone: at the Sep 2026 Tri-Cup the league listed Kisha Abernathy as a 3 though
she was a 4, and our history for other divisions can trail a whole session (it had no Summer
2026 games for any opponent). So this asks APA directly: a member's player records carry the
format, session and skill level of every team-season they have played.

Reads the tournament events in data/league.json that name "prevSession" and "currentSession",
collects every member on both sides (hand-entered event rosters, opponents referenced by
oppTeamId, and our own team, from data.json), and writes data/tourney_sl.json:

  {"generatedAt": ..., "prevSession": "Summer 2026", "currentSession": "Fall 2026",
   "members": {"<mid>": {"name", "fmt", "last", "lastTeam", "current", "currentTeam"}}}

It also stamps that object into ./data.json as "tourneySL" so the site picks it up without a
full rebuild, and build_site_data.py carries it forward on the weekly refresh. The Match tab
plays each player at max(listed, last, current) and names anyone it could not verify.

ASSUMPTION, not confirmed against APA documentation: a past session's player record holds the
SL the player finished that session at. If a player had two teams in one session (same format),
the higher SL is used.

Usage: python3 pipeline/tourney_sl.py               every member in league.json tournament events
       python3 pipeline/tourney_sl.py <memberId> ... just these members (spot check)
Then commit data/tourney_sl.json and data.json.
"""
import json, re, sys, time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from apa_pull import APA, DATA  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
Q_SESSIONS = """query($id:Int!){ member(id:$id){ id firstName lastName
  players(current:false){ __typename skillLevel team{ id name } session{ id name } } } }"""
FMT = {"EightBallPlayer": "8", "NineBallPlayer": "9"}


def sess_key(name):
    """'Summer 2026' / '2026 Summer Session' -> ('summer', '2026'); None if unrecognisable."""
    n = (name or "").lower()
    season = re.search(r"spring|summer|fall|autumn|winter", n)
    year = re.search(r"(19|20)\d\d", n)
    if not (season and year):
        return None
    return ({"autumn": "fall"}.get(season.group(0), season.group(0)), year.group(0))


def members_from_events(league, site):
    """{mid: fmt} for everyone on either side of every tournament match."""
    out = {}
    teams = site.get("teams") or {}
    for ev in league.get("events") or []:
        if not (ev.get("prevSession") and ev.get("currentSession")):
            continue
        fmt = ev.get("fmt") or "8"
        for m in ev.get("matches") or []:
            rosters = [m.get("roster") or [], ev.get("ourRoster") or []]
            ours = None if ev.get("ourRoster") else m.get("ourTeamId")  # qualifying roster wins
            for tid in (m.get("oppTeamId"), ours):
                if tid is not None:
                    rosters.append((teams.get(str(tid)) or {}).get("roster") or [])
            for r in rosters:
                for p in r:
                    if p.get("mid"):
                        out[int(p["mid"])] = fmt
    return out


def main():
    league = json.loads((DATA / "league.json").read_text())
    site_path = ROOT / "data.json"
    site = json.loads(site_path.read_text())
    evs = [e for e in league.get("events") or [] if e.get("prevSession") and e.get("currentSession")]
    if not evs:
        sys.exit("No event in data/league.json has both prevSession and currentSession.")
    ev = evs[-1]
    prev_label = ev["prevSession"]["label"] if isinstance(ev["prevSession"], dict) else ev["prevSession"]
    cur_label = ev["currentSession"]
    prev_k, cur_k = sess_key(prev_label), sess_key(cur_label)
    if not (prev_k and cur_k):
        sys.exit("Could not read session names %r / %r" % (prev_label, cur_label))

    # data.json's copy of the events carries the qualifying roster the build filled from APA
    wanted = members_from_events(site.get("league") or league, site)
    if sys.argv[1:]:
        wanted = {int(a): wanted.get(int(a), ev.get("fmt") or "8") for a in sys.argv[1:]}
    print("Checking %d member(s): last session %s, current %s" % (len(wanted), prev_label, cur_label))

    api = APA(); api.mint()
    members, seen_sessions = {}, set()
    for i, (mid, fmt) in enumerate(sorted(wanted.items()), 1):
        d = api.q(Q_SESSIONS, {"id": mid})
        m = (d.get("data") or {}).get("member")
        if not m:
            print("  %d: no member record (%s)" % (mid, (d.get("errors") or [{}])[0].get("message")))
            continue
        rec = {"name": "%s %s" % (m.get("firstName") or "", m.get("lastName") or ""), "fmt": fmt,
               "last": None, "lastTeam": None, "current": None, "currentTeam": None}
        for p in m.get("players") or []:
            if FMT.get(p.get("__typename")) != fmt or not p.get("skillLevel"):
                continue
            sname = (p.get("session") or {}).get("name")
            seen_sessions.add(sname)
            k = sess_key(sname)
            team = (p.get("team") or {}).get("name")
            for slot, want in (("last", prev_k), ("current", cur_k)):
                if k == want and p["skillLevel"] > (rec[slot] or 0):
                    rec[slot], rec[slot + "Team"] = p["skillLevel"], team
        members[str(mid)] = rec
        print("  %-24s last %-4s current %s" % (rec["name"].strip(), rec["last"], rec["current"]))
        if i % 10 == 0:
            time.sleep(0.5)

    unmatched = sorted(s for s in seen_sessions if s and sess_key(s) is None)
    if unmatched:
        print("NOTE: session names not understood, ignored: %s" % unmatched)
    out = {"generatedAt": datetime.now().isoformat(timespec="seconds"),
           "prevSession": prev_label, "currentSession": cur_label, "members": members}
    (DATA / "tourney_sl.json").write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n")
    site["tourneySL"] = out
    site_path.write_text(json.dumps(site, separators=(",", ":")))
    print("Wrote data/tourney_sl.json and data.json (tourneySL): %d member(s)" % len(members))


if __name__ == "__main__":
    main()
