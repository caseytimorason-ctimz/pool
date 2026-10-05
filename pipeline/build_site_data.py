#!/usr/bin/env python3
"""
Assemble data.json (repo root, what index.html fetches) for the dashboard: analysis + league
baselines + current-season rosters + schedule + opponent rosters + this session's results.
Runs headless (Keychain refresh token on the Mac, APA_REFRESH_TOKEN in a cloud session).

Refuses to overwrite data.json with a bundle that lost data the last one had (see
check_bundle); pass --force to write it anyway.
"""
import csv as _csv0
import csv, json, os, sqlite3, sys, time, urllib.request, urllib.error
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = ROOT / "data.json"
LS = (Path.home() / "Library/Containers/league.poolplayers.com/Data/Library/WebKit/WebsiteData"
      "/Default/ORyOecTgL3hb22UNOIkn7DC33SbJKxT_s3tjRtJIy4A"
      "/ORyOecTgL3hb22UNOIkn7DC33SbJKxT_s3tjRtJIy4A/LocalStorage/localstorage.sqlite3")
MEMBER_ID = 3041011


def post(q, v=None, tok=None):
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = tok
    r = urllib.request.Request("https://gql.poolplayers.com/graphql",
                               data=json.dumps({"query": q, "variables": v or {}}).encode(), headers=h)
    # A transient reset is routine through this egress proxy (it is what emptied the 09-14
    # bundle), so retry with backoff instead of letting one request kill the build.
    for attempt in range(4):
        try:
            with urllib.request.urlopen(r, timeout=30) as x:
                return json.load(x)
        except urllib.error.HTTPError as e:
            return json.load(e)
        except Exception:
            if attempt == 3:
                raise
            time.sleep(0.5 * (2 ** attempt))


def token():
    # Cloud sessions have no Mac localStorage: mint from APA_REFRESH_TOKEN the way apa_pull does.
    if os.environ.get("APA_REFRESH_TOKEN", "").strip():
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from apa_pull import APA
        apa = APA(); apa.mint()
        return apa.token
    rt = sqlite3.connect(str(LS)).execute("SELECT value FROM ItemTable WHERE key='refreshToken'").fetchone()[0]
    rt = (rt.decode("utf-16-le") if isinstance(rt, (bytes, bytearray)) else str(rt)).strip().strip('"')
    return post("mutation($rt:String!){generateAccessToken(refreshToken:$rt){accessToken}}", {"rt": rt})["data"]["generateAccessToken"]["accessToken"]


def roster(tid, tok):
    d = post("query($id:Int!){ team(id:$id){ id name roster{ id displayName skillLevel __typename member{id} } "
             "matches{ id startTime isFinalized isScored home{id name} away{id name} } } }", {"id": tid}, tok)
    return (d.get("data") or {}).get("team")


def fmt_of(team):
    for p in (team.get("roster") or []):
        if p.get("__typename") == "NineBallPlayer":
            return "9"
        if p.get("__typename") == "EightBallPlayer":
            return "8"
    return "8"


def norm_roster(team):
    out = []
    for p in (team.get("roster") or []):
        if p.get("skillLevel"):
            out.append({"mid": (p.get("member") or {}).get("id"), "name": p.get("displayName"), "sl": p["skillLevel"]})
    return out


Q_MY_PLAYERS = ("query($id:Int!){ member(id:$id){ players(current:false){ __typename "
                "team{ id name } session{ name } } } }")
PLAYER_FMT = {"EightBallPlayer": "8", "NineBallPlayer": "9"}


def fill_qualifying_rosters(league, tok):
    """A tournament is played with the roster that QUALIFIED for it: our team in the event's
    qualifying session (its prevSession), not the new session's. At Tri-Cup #2 that meant
    Christian Sosa, not Fall's Taylor Barnes. Fill each event's ourRoster from APA; a roster set
    by hand in league.json always wins."""
    from tourney_sl import sess_key  # same session-name reader the SL check uses
    evs = [e for e in (league.get("events") or []) if e.get("prevSession") and not e.get("ourRoster")]
    if not evs:
        return
    d = post(Q_MY_PLAYERS, {"id": MEMBER_ID}, tok)
    players = (((d.get("data") or {}).get("member") or {}).get("players")) or []
    for ev in evs:
        ps = ev["prevSession"]
        label = ps.get("label") if isinstance(ps, dict) else ps
        want, fmt = sess_key(label), ev.get("fmt") or "8"
        tids = [p["team"]["id"] for p in players if p.get("team")
                and PLAYER_FMT.get(p.get("__typename")) == fmt
                and sess_key((p.get("session") or {}).get("name")) == want]
        if not tids:
            print("WARNING: no %s-ball team of ours in %s for %s; roster left to league.json"
                  % (fmt, label, ev.get("id")))
            continue
        t = roster(tids[0], tok)
        if t and norm_roster(t):
            ev["ourRoster"] = norm_roster(t)
            ev["ourRosterSource"] = "APA: %s, %s" % (t.get("name"), label)
            print("%s: qualifying roster from %s (%s), %d players"
                  % (ev.get("id"), t.get("name"), label, len(ev["ourRoster"])))


def baselines():
    tabs = {"8": defaultdict(lambda: {"g": 0, "w": 0}), "9": defaultdict(lambda: {"g": 0, "w": 0})}
    with open(DATA / "games.csv") as f:
        for r in csv.DictReader(f):
            try:
                a, b = int(r["sl"]), int(r["oppSl"])
            except (ValueError, KeyError):
                continue
            if a <= 0 or b <= 0 or r["fmt"] not in tabs:
                continue
            c = tabs[r["fmt"]][(a, b)]; c["g"] += 1; c["w"] += 1 if r["win"] == "True" else 0
    return {fmt: {"%d-%d" % k: [round(v["w"] / v["g"], 4), v["g"]] for k, v in t.items()} for fmt, t in tabs.items()}


def results_feed(tid, team, tok):
    """This session's played matches for one of our teams, as the Team tab's results feed
    (rotation tracker, team record, match list, Last 8/4/2 windows)."""
    from apa_pull import Q_MATCH
    out = []
    for mm in sorted(team.get("matches") or [], key=lambda x: x.get("startTime") or ""):
        if not (mm.get("isFinalized") or mm.get("isScored")):
            continue
        m = (post(Q_MATCH, {"id": mm["id"]}, tok).get("data") or {}).get("match")
        if not m:
            continue
        home = (m.get("home") or {}).get("id") == tid
        res = {r["homeAway"]: r for r in (m.get("results") or [])}
        mine, theirs = res.get("HOME" if home else "AWAY"), res.get("AWAY" if home else "HOME")
        if not mine or not theirs:
            continue
        pm, pt = mine.get("points") or {}, theirs.get("points") or {}
        adj = lambda p: (p.get("total") or 0) - (p.get("won") or 0) - (p.get("bonus") or 0) + (p.get("penalty") or 0)
        pts = lambda sc: (sc.get("nineBallMatchPointsEarned") if sc.get("nineBallMatchPointsEarned") is not None
                          else sc.get("eightBallMatchPointsEarned"))
        opp_by_pos = {}
        for sc in theirs.get("scores") or []:
            opp_by_pos.setdefault(sc.get("matchPositionNumber"), []).append(sc)
        line = []
        for sc in sorted(mine.get("scores") or [], key=lambda x: (-(x.get("skillLevel") or 0), x.get("matchPositionNumber") or 0)):
            q = opp_by_pos.get(sc.get("matchPositionNumber")) or []
            o = q.pop(0) if q else {}
            line.append({"who": (sc.get("player") or {}).get("displayName"), "sl": sc.get("skillLevel"),
                         "pts": pts(sc), "opp": (o.get("player") or {}).get("displayName"),
                         "oppSL": o.get("skillLevel"), "oppPts": pts(o) if o else None,
                         "won": sc.get("winLoss") == "W"})
        us, them = pm.get("total") or 0, pt.get("total") or 0
        out.append({"date": (m.get("startTime") or "")[:10], "week": m.get("week"),
                    "opp": ((m.get("away") if home else m.get("home")) or {}).get("name"),
                    "us": us, "them": them, "won": us > them,
                    "bonus": pm.get("bonus") or 0, "oppBonus": pt.get("bonus") or 0,
                    "pen": pm.get("penalty") or 0, "oppPen": pt.get("penalty") or 0,
                    "adj": adj(pm), "oppAdj": adj(pt), "line": line})
    return out


def standings(tid, since):
    """Division standings this session for our team `tid`, from the match corpus: every team in
    our division, total match points (bonuses and penalties included, as APA scores them) from
    its finalized matches. A division id is one session, so the division alone scopes it;
    `since` only picks out our current division. backfill.py --current keeps the division's
    matches in the corpus. -> [{"team", "points", "played"}], best first, or None if there is nothing to show."""
    mp = DATA / "matches.json"
    if not mp.exists() or not since:
        return None
    metas = [v["meta"] for v in json.loads(mp.read_text()).values()]
    divs = {m.get("div") for m in metas if (m.get("start") or "")[:10] >= since and m.get("div")
            and tid in (m.get("homeTeam"), m.get("awayTeam"))}
    if not divs:
        return None
    rows = {}
    for m in metas:
        if m.get("div") not in divs or not m.get("finalized"):
            continue
        for side in ("home", "away"):
            t = m.get(side + "Team")
            r = rows.setdefault(t, {"team": m.get(side + "Name"), "points": 0, "played": 0})
            r["points"] += m.get(side + "Points") or 0
            r["played"] += 1
    return sorted(rows.values(), key=lambda r: -r["points"]) or None


def check_bundle(new, old):
    """Every refresh from 09-12 to 09-16 shipped a damaged bundle (empty results, missing
    teams) and the app failed quietly. Compare against the bundle being replaced and list
    anything lost. The schedule shrinking is normal (played matches leave it), so is a past
    opponent's team dropping out; what is not normal is a feed going empty or shrinking, or a
    scheduled opponent without a roster."""
    problems = []
    if not new.get("myActiveTeams"):
        problems.append("myActiveTeams is empty")
    for k in ("players", "results"):
        a, b = len(old.get(k) or {}), len(new.get(k) or {})
        if b < a:
            problems.append("%s shrank %d -> %d" % (k, a, b))
    for tid, r in (old.get("results") or {}).items():
        nr = (new.get("results") or {}).get(tid)
        if nr is not None and len(nr["matches"]) < len(r["matches"]):
            problems.append("results for %s lost matches %d -> %d" % (r.get("name"), len(r["matches"]), len(nr["matches"])))
    missing = sorted({m["oppTeam"] for m in new.get("schedule") or []
                      if not ((new.get("teams") or {}).get(str(m["oppTeamId"])) or {}).get("roster")})
    if missing:
        problems.append("scheduled opponents with no roster: " + ", ".join(missing))
    return problems


def main():
    tok = token()
    mem = post("query($id:Int!){ member(id:$id){ teams{ id name } } }", {"id": MEMBER_ID}, tok)["data"]["member"]
    my_team_ids = [t["id"] for t in (mem.get("teams") or [])]
    # Every team the member currently sits on, INCLUDING the just-finished session. A new
    # session's team record appears (with a schedule but no results) before the old one stops
    # mattering, and postseason eligibility is earned in the session that just ended — so the
    # counters must not follow the empty new team.
    my_current_ids = {str(t) for t in my_team_ids}

    teams = {}          # tid -> {name, fmt, roster}
    schedule = []       # {date, ourTeam, ourTeamId, oppTeam, oppTeamId, fmt, matchId}
    my_active = []
    to_fetch = set(my_team_ids)
    fetched = {}
    for tid in list(to_fetch):
        t = roster(tid, tok)
        if t:
            fetched[tid] = t

    for tid, t in list(fetched.items()):
        ups = [m for m in (t.get("matches") or []) if not (m.get("isFinalized") or m.get("isScored"))]
        if not ups:
            continue  # not an active team
        fmt = fmt_of(t)
        teams[tid] = {"name": t["name"], "fmt": fmt, "roster": norm_roster(t)}
        my_active.append(tid)
        for m in sorted(ups, key=lambda x: x.get("startTime") or ""):
            opp = m["away"] if m["home"]["id"] == tid else m["home"]
            schedule.append({"date": (m.get("startTime") or "")[:10], "ourTeamId": tid,
                             "ourTeam": t["name"], "oppTeamId": opp["id"], "oppTeam": opp["name"],
                             "fmt": fmt, "matchId": m["id"]})
            if opp["id"] not in teams and opp["id"] not in fetched:
                ot = roster(opp["id"], tok)
                if ot:
                    teams[opp["id"]] = {"name": ot["name"], "fmt": fmt_of(ot), "roster": norm_roster(ot)}

    analysis = json.load(open(DATA / "analysis.json"))

    # Scope what's EMBEDDED in the artifact to who's actually relevant: our roster, plus
    # everyone our players have ever faced, plus the rosters of scheduled opponents (even if
    # not yet played). The full league-wide corpus (analysis.json on disk) stays complete —
    # this trims only what ships in the single-file mobile dashboard, which has a hard size
    # ceiling. Without this, embedding all 3,800+ league-wide profiles (most of them people
    # our team has never played and never will) blows well past that ceiling for zero benefit.
    our_mids = {str(p["mid"]) for tid in my_active for p in teams[tid]["roster"]}
    relevant_mids = set(our_mids)
    import csv as _csv
    with open(DATA / "games.csv") as f:
        for r in _csv.DictReader(f):
            if r.get("mid") in our_mids and r.get("oppMid"):
                relevant_mids.add(r["oppMid"])
    for tid, t in teams.items():
        for p in t.get("roster", []):
            if p.get("mid"):
                relevant_mids.add(str(p["mid"]))
    players = {k: v for k, v in analysis["players"].items() if k.split(":")[0] in relevant_mids}

    # Session stats. Which team's session to count is not obvious: at a session boundary the
    # member is rostered on both the new team (schedule, no results) and the old one (a full
    # session of results, and the one a Tri-Cup at the end of it pays off). So tally EVERY
    # team the member is currently on, then per format keep the one with actual play. Counting
    # the new empty team instead reports the whole roster at zero matches and nobody eligible.
    per_team = {}
    with open(DATA / "games.csv") as f:
        for r in _csv0.DictReader(f):
            if r.get("team") not in my_current_ids or not r.get("mid"):
                continue
            t = per_team.setdefault(r["team"], {"fmt": r["fmt"], "players": {}, "last": ""})
            t["last"] = max(t["last"], r.get("date") or "")
            e = t["players"].setdefault(r["mid"], {"mid": r["mid"], "fmt": r["fmt"], "team": r["team"],
                                                  "games": 0, "wins": 0, "points": 0, "nights": [],
                                                  "first": None, "last": None})
            e["games"] += 1
            if r["win"] == "True":
                e["wins"] += 1
            try:
                e["points"] += int(r["pts"] or 0)
            except ValueError:
                pass
            if r["matchId"] not in e["nights"]:
                e["nights"].append(r["matchId"])
            d = r.get("date") or ""
            if d:
                e["first"] = min(e["first"] or d, d); e["last"] = max(e["last"] or d, d)

    chosen, sess = {}, {}
    for tid, t in per_team.items():
        cur = chosen.get(t["fmt"])
        if cur is None or (t["last"], len(t["players"])) > (per_team[cur]["last"], len(per_team[cur]["players"])):
            chosen[t["fmt"]] = tid
    # The team whose session we counted may not be in `teams` (it has no upcoming matches, so
    # the active-team loop skipped it). Add it so the UI can name the session, and use its
    # roster for that format when the new session's roster isn't set yet.
    for fmt, tid in chosen.items():
        ft = fetched.get(int(tid))
        if ft and int(tid) not in teams:
            teams[int(tid)] = {"name": ft["name"], "fmt": fmt_of(ft), "roster": norm_roster(ft)}
        for at in my_active:
            if teams[at]["fmt"] == fmt and not teams[at]["roster"] and ft:
                teams[at]["roster"] = norm_roster(ft)

    session_source = {}
    for fmt, tid in chosen.items():
        t = per_team[tid]
        dates = [p["first"] for p in t["players"].values() if p["first"]] + \
                [p["last"] for p in t["players"].values() if p["last"]]
        session_source[fmt] = {"teamId": tid, "name": (teams.get(int(tid)) or {}).get("name"),
                               "first": min(dates) if dates else None,
                               "last": max(dates) if dates else None,
                               "matchNights": len({n for p in t["players"].values() for n in p["nights"]})}
        for mid, e in t["players"].items():
            e["matchNights"] = len(e.pop("nights"))
            sess["%s:%s" % (mid, fmt)] = e

    # Hand-maintained postseason results (playoffs/tournaments are scored on paper and do
    # not exist in the API). Optional — the app renders without it.
    postseason = None
    pf = DATA / "postseason.json"
    if pf.exists():
        try:
            postseason = json.loads(pf.read_text())
            postseason.pop("_README", None)
        except Exception as e:
            print("WARNING: postseason.json unreadable (%s) — skipping" % e)

    # Hand-maintained league rules + scheduled events (bylaws transcription, tournament dates).
    league = None
    lf = DATA / "league.json"
    if lf.exists():
        try:
            league = json.loads(lf.read_text())
            league.pop("_README", None)
        except Exception as e:
            print("WARNING: league.json unreadable (%s) — skipping" % e)
    if league:
        fill_qualifying_rosters(league, tok)

    # Tournament skill levels from APA (pipeline/tourney_sl.py): what each tournament player
    # ended last session at and holds this session. Optional; carried forward when present.
    tourney_sl = None
    tf = DATA / "tourney_sl.json"
    if tf.exists():
        try:
            tourney_sl = json.loads(tf.read_text())
        except Exception as e:
            print("WARNING: tourney_sl.json unreadable (%s) — skipping" % e)

    # Unrated players come through APA as SL 0. Show the level they actually play at, from
    # whichever current roster lists them, rather than "SL 0".
    roster_sl = {}
    for t in teams.values():
        for p in t.get("roster") or []:
            if p.get("mid") and p.get("sl"):
                roster_sl.setdefault((str(p["mid"]), t["fmt"]), p["sl"])
    for k, v in analysis["players"].items():
        if not v.get("currentSL"):
            sl = roster_sl.get((str(v["mid"]), v["format"]))
            if sl:
                v["currentSL"] = sl

    # This session's results for the team counted in each format (same choice as sessionStats).
    results = {}
    for fmt, tid in chosen.items():
        ft = fetched.get(int(tid))
        if not ft:
            continue
        ms = results_feed(int(tid), ft, tok)
        results[str(tid)] = {"name": ft["name"], "fmt": fmt, "matches": ms,
                             "wins": sum(1 for m in ms if m["won"]),
                             "losses": sum(1 for m in ms if not m["won"]),
                             "pointsFor": sum(m["us"] for m in ms),
                             "pointsAgainst": sum(m["them"] for m in ms)}

    # Division standings for the Team tab's chase-first line. A standings block kept by hand in
    # league.json wins; otherwise compute it from the corpus.
    if league is not None:
        st = dict(league.get("standings") or {})
        for fmt, src in session_source.items():
            if fmt not in st:
                rows = standings(int(src["teamId"]), src.get("first"))
                if rows:
                    st[fmt] = rows
        if st:
            league["standings"] = st

    of = DATA / "official_rules.json"
    if league is not None and of.exists():
        official = json.loads(of.read_text())
        official.pop("_README", None)
        league["official"] = official

    base = {"generatedAt": analysis.get("generatedFrom"), "memberId": MEMBER_ID,
            "myActiveTeams": my_active, "teams": teams, "schedule": schedule,
            "sessionStats": sess, "sessionSource": session_source,
            "postseason": postseason, "league": league, "baselines": baselines(),
            "tourneySL": tourney_sl, "results": results}

    # data.json: EVERY player in the league, but tiered by depth. Head-to-head is 85% of the
    # payload and its per-meeting game logs alone are ~11MB. Those logs only matter for people
    # we'd actually scout, so keep them for the relevant set and drop them for the rest —
    # everyone still keeps their vs-SL records, trajectory and H2H totals, so you can still
    # look up any player in the league.
    full_players = {}
    for k, v in analysis["players"].items():
        if k.split(":")[0] in relevant_mids:
            full_players[k] = v
        else:
            trimmed = dict(v)
            trimmed["headToHead"] = [{kk: o[kk] for kk in ("oppMid", "oppName", "meetings", "wins", "avgPts", "ptsN") if kk in o}
                                     for o in v.get("headToHead", [])]
            full_players[k] = trimmed
    full = dict(base, players=full_players)
    # JSON object keys are strings; match what index.html reads (teams["13139509"]).
    full["teams"] = {str(k): v for k, v in full["teams"].items()}

    old = json.loads(OUT.read_text()) if OUT.exists() else {}
    problems = check_bundle(full, old)
    for p in problems:
        print("FAIL  " + p)
    if problems and "--force" not in sys.argv[1:]:
        sys.exit("Not writing %s: the new bundle lost data the current one has (above). "
                 "Re-run the pull, or pass --force if the loss is real." % OUT.name)
    OUT.write_text(json.dumps(full, separators=(",", ":")))
    print("%s: %d player profiles (%d with full head-to-head), %d results feeds, %.2f MB" % (
        OUT.name, len(full_players), len(players), len(results), OUT.stat().st_size / 1024 / 1024))
    print("active teams:", [(teams[t]["name"], teams[t]["fmt"]) for t in my_active])


if __name__ == "__main__":
    main()
