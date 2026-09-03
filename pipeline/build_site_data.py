#!/usr/bin/env python3
"""
Assemble site/data.json for the dashboard: analysis + league baselines + current-season
rosters + schedule + opponent rosters. Runs headless (Keychain refresh token).
"""
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

    bundle = {"generatedAt": analysis.get("generatedFrom"), "memberId": MEMBER_ID,
              "myActiveTeams": my_active, "teams": teams, "schedule": schedule,
              "baselines": baselines(), "players": players}
    (SITE / "data.json").write_text(json.dumps(bundle, separators=(",", ":")))
    print("site/data.json: %d teams, %d scheduled matches, %d/%d player profiles embedded, %.2f MB" % (
        len(teams), len(schedule), len(players), len(analysis["players"]),
        (SITE / "data.json").stat().st_size / 1024 / 1024))
    print("active teams:", [(teams[t]["name"], teams[t]["fmt"]) for t in my_active])


if __name__ == "__main__":
    main()
