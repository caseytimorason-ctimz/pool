#!/usr/bin/env python3
"""
Assemble site/data.json for the dashboard: analysis + league baselines + current-season
rosters + schedule + opponent rosters. Runs headless (Keychain refresh token).
"""
import csv as _csv0
import csv, json, sqlite3, urllib.request, urllib.error
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"; SITE = ROOT / "site"; SITE.mkdir(exist_ok=True)
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
    try:
        with urllib.request.urlopen(r, timeout=30) as x:
            return json.load(x)
    except urllib.error.HTTPError as e:
        return json.load(e)


def token():
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

    base = {"generatedAt": analysis.get("generatedFrom"), "memberId": MEMBER_ID,
            "myActiveTeams": my_active, "teams": teams, "schedule": schedule,
            "sessionStats": sess, "sessionSource": session_source,
            "postseason": postseason, "league": league, "baselines": baselines()}

    # site/data.json: SCOPED, for the artifact (hard single-file size ceiling).
    scoped = dict(base, players=players)
    (SITE / "data.json").write_text(json.dumps(scoped, separators=(",", ":")))
    print("site/data.json (artifact, scoped): %d/%d player profiles, %.2f MB" % (
        len(players), len(analysis["players"]), (SITE / "data.json").stat().st_size / 1024 / 1024))

    # site/data.full.json: EVERY player in the league, but tiered by depth. Head-to-head is
    # 85% of the payload and its per-meeting game logs alone are ~11MB. Those logs only matter
    # for people we'd actually scout, so keep them for the relevant set and drop them for the
    # rest — everyone still keeps their vs-SL records, trajectory and H2H win/loss totals, so
    # you can still look up any player in the league. Keeps the file inside GitHub's ~25MB
    # web-upload ceiling with real headroom.
    full_players = {}
    for k, v in analysis["players"].items():
        if k.split(":")[0] in relevant_mids:
            full_players[k] = v
        else:
            trimmed = dict(v)
            trimmed["headToHead"] = [{kk: o[kk] for kk in ("oppMid", "oppName", "meetings", "wins") if kk in o}
                                     for o in v.get("headToHead", [])]
            full_players[k] = trimmed
    full = dict(base, players=full_players)
    (SITE / "data.full.json").write_text(json.dumps(full, separators=(",", ":")))
    print("site/data.full.json (deploy, full): %d player profiles, %.2f MB" % (
        len(analysis["players"]), (SITE / "data.full.json").stat().st_size / 1024 / 1024))
    print("active teams:", [(teams[t]["name"], teams[t]["fmt"]) for t in my_active])


if __name__ == "__main__":
    main()
