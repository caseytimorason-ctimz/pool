#!/usr/bin/env python3
"""
APA history pipeline (headless) — Phase 2.

Auth model (reverse-engineered, introspection off):
  mutation generateAccessToken(refreshToken: String!) : AccessTokenPayload!{ accessToken }
  - The refresh token is long-lived and NOT rotated (payload returns only accessToken),
    so we store it once and mint 15-min access tokens as needed. Fully headless.

Refresh token source (one-time, kept OFF Google Drive and out of any transcript):
  In Chrome on https://league.poolplayers.com while logged in, DevTools console:
      copy(localStorage.refreshToken)
  then store it in the macOS Keychain:
      security add-generic-password -a "$USER" -s apa-refresh-token -w   # paste when prompted, ⌃D
  (Rotate/replace with `security add-generic-password ... -U`.)

Outputs (durable, in this project, NOT in Drive/.secrets and NOT in any SP repo):
  data/matches.json        raw match+scores, normalized
  data/games.csv           one row per player per game (analysis-ready "perspectives")
  data/meta.json           run metadata + team/session index
"""
import json, subprocess, sys, time, urllib.request, os, csv, io
from pathlib import Path

GQL = "https://gql.poolplayers.com/graphql"
DATA = Path(__file__).resolve().parent.parent / "data"
DATA.mkdir(exist_ok=True)
KEYCHAIN_SERVICE = "apa-refresh-token"

# ---- confirmed field shapes (from live Apollo cache, 2026-09-02) ----
MEMBER_ID = 3041011  # Casey; `viewer` resolves null server-side, so query member(id) directly

Q_MEMBER = """query($id:Int!){ member(id:$id) {
  id firstName lastName
  players(current:false) { __typename id skillLevel memberNumber displayName
            team { id name number } session { id name } }
  teams { id name number } } }"""

Q_TEAM_MATCHES = """query($id:Int!){ team(id:$id){ id name number
  matches { id startTime week isFinalized isScored } } }"""

Q_MATCH = """query($id:Int!){ match(id:$id){
  id startTime week isFinalized isScored
  division { id } home { id name } away { id name }
  results { homeAway points { total won bonus penalty sportsmanship }
    scores {
      skillLevel teamSlot matchPositionNumber playerPosition winLoss
      matchForfeited doublesMatch incompleteMatch dateTimeStamp
      nineBallPoints nineBallMatchPointsEarned nineBallBreakAndRun nineOnSnap
      eightBallWins eightBallMatchPointsEarned eightBallBreakAndRun eightOnBreak
      player { __typename id displayName skillLevel memberNumber member { id } }
    } } } }"""


def keychain_refresh_token():
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except subprocess.CalledProcessError:
        sys.exit(
            "No refresh token in Keychain. One-time setup:\n"
            "  1) Chrome console on league.poolplayers.com:  copy(localStorage.refreshToken)\n"
            "  2) security add-generic-password -a \"$USER\" -s %s -w   (paste, then Ctrl-D)\n"
            % KEYCHAIN_SERVICE)


class APA:
    def __init__(self):
        self.refresh = keychain_refresh_token()
        self.token = None

    def _post(self, query, variables=None, auth=True):
        body = json.dumps({"query": query, "variables": variables or {}}).encode()
        headers = {"Content-Type": "application/json"}
        if auth and self.token:
            # APA GraphQL expects the raw token WITHOUT a "Bearer " scheme prefix.
            headers["Authorization"] = self.token
        req = urllib.request.Request(GQL, data=body, headers=headers)
        # At thousands-of-requests scale a transient timeout/connection-reset is expected,
        # not exceptional — retry with backoff instead of letting it kill the whole run.
        last_err = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.load(r)
            except urllib.error.HTTPError as e:
                return json.load(e)
            except Exception as e:
                # Broad on purpose: seen in practice include OSError/URLError/TimeoutError
                # AND http.client.HTTPException subclasses (e.g. BadStatusLine) that do NOT
                # inherit from OSError — this environment's egress goes through an HTTP CONNECT
                # proxy that returns malformed responses under thousands-of-requests concurrency.
                # A single failed request must never be allowed to kill a large batch run.
                last_err = e
                time.sleep(0.5 * (2 ** attempt))
        return {"errors": [{"message": "NETWORK_ERROR after retries: %s" % last_err}]}

    def mint(self):
        m = ("mutation($rt:String!){ generateAccessToken(refreshToken:$rt){ accessToken } }")
        d = self._post(m, {"rt": self.refresh}, auth=False)
        if d.get("errors"):
            sys.exit("Token mint failed: %s" % d["errors"][0]["message"])
        self.token = d["data"]["generateAccessToken"]["accessToken"]

    def q(self, query, variables=None, _retry=True):
        if not self.token:
            self.mint()
        d = self._post(query, variables)
        errs = d.get("errors") or []
        if any(e.get("extensions", {}).get("code") == "TOKEN_EXPIRED" for e in errs) and _retry:
            self.mint()
            return self.q(query, variables, _retry=False)
        return d


def normalize_match(m):
    """match dict -> (meta, [score rows], [perspective rows])."""
    home = m.get("home") or {}; away = m.get("away") or {}
    meta = {"id": m["id"], "start": m.get("startTime"), "week": m.get("week"),
            "homeTeam": home.get("id"), "awayTeam": away.get("id"),
            "homeName": home.get("name"), "awayName": away.get("name"),
            "finalized": m.get("isFinalized"), "div": (m.get("division") or {}).get("id")}
    rows, persp = [], []
    sides = {"HOME": [], "AWAY": []}
    for res in (m.get("results") or []):
        side = res["homeAway"]; team = home if side == "HOME" else away
        meta[("homePoints" if side == "HOME" else "awayPoints")] = (res.get("points") or {}).get("total")
        for s in (res.get("scores") or []):
            p = s.get("player") or {}
            sides[side].append(s)
            rows.append({"matchId": m["id"], "side": side, "teamId": team.get("id"),
                         "start": m.get("startTime"), "playerType": p.get("__typename"),
                         "playerId": p.get("id"), "name": p.get("displayName"),
                         "memberId": (p.get("member") or {}).get("id"),
                         "sl": s.get("skillLevel"), "slot": s.get("teamSlot"),
                         "pos": s.get("matchPositionNumber"), "result": s.get("winLoss"),
                         "forfeit": s.get("matchForfeited"), "doubles": s.get("doublesMatch"),
                         "incomplete": s.get("incompleteMatch"),
                         "p9": s.get("nineBallPoints"), "mpe9": s.get("nineBallMatchPointsEarned"),
                         "br9": s.get("nineBallBreakAndRun"), "nos": s.get("nineOnSnap"),
                         "w8": s.get("eightBallWins"), "mpe8": s.get("eightBallMatchPointsEarned"),
                         "br8": s.get("eightBallBreakAndRun"), "eob": s.get("eightOnBreak")})
    # pair opponents by matchPositionNumber -> perspective rows (both directions)
    fmt = "9" if (sides["HOME"] and sides["HOME"][0].get("nineBallPoints") is not None) else "8"
    byp = {"HOME": {}, "AWAY": {}}
    for side in ("HOME", "AWAY"):
        for s in sides[side]:
            byp[side].setdefault(s.get("matchPositionNumber"), []).append(s)
    for pos in set(byp["HOME"]) | set(byp["AWAY"]):
        H, A = byp["HOME"].get(pos, []), byp["AWAY"].get(pos, [])
        for i in range(max(len(H), len(A))):
            if i >= len(H) or i >= len(A):
                continue
            h, a = H[i], A[i]; hp = h.get("player") or {}; ap = a.get("player") or {}
            hmid = (hp.get("member") or {}).get("id"); amid = (ap.get("member") or {}).get("id")
            date = (m.get("startTime") or "")[:10]
            pts = lambda sc: (sc.get("nineBallMatchPointsEarned")
                              if sc.get("nineBallMatchPointsEarned") is not None
                              else sc.get("eightBallMatchPointsEarned"))
            persp.append({"mid": hmid, "pid": hp.get("id"), "name": hp.get("displayName"),
                          "team": home.get("id"), "sl": h.get("skillLevel"),
                          "oppMid": amid, "oppPid": ap.get("id"), "oppName": ap.get("displayName"),
                          "oppSl": a.get("skillLevel"), "win": h.get("winLoss") == "W",
                          "pts": pts(h), "fmt": fmt, "date": date, "matchId": m["id"]})
            persp.append({"mid": amid, "pid": ap.get("id"), "name": ap.get("displayName"),
                          "team": away.get("id"), "sl": a.get("skillLevel"),
                          "oppMid": hmid, "oppPid": hp.get("id"), "oppName": hp.get("displayName"),
                          "oppSl": h.get("skillLevel"), "win": a.get("winLoss") == "W",
                          "pts": pts(a), "fmt": fmt, "date": date, "matchId": m["id"]})
    return meta, rows, persp


def main():
    api = APA(); api.mint()
    v = api.q(Q_MEMBER, {"id": MEMBER_ID})
    mem = (v.get("data") or {}).get("member")
    if not mem:
        sys.exit("member returned null — token/permissions issue: %s" % v.get("errors"))
    print("Viewer: %s %s (member %s)" % (mem.get("firstName"), mem.get("lastName"), mem.get("id")))

    # every team the member has played on, across all sessions (full history)
    team_ids = {t["id"] for t in (mem.get("teams") or [])}
    for p in (mem.get("players") or []):
        if p.get("team"):
            team_ids.add(p["team"]["id"])
    print("Teams in history: %d" % len(team_ids))

    # enumerate all match ids across those teams
    match_ids, team_index = set(), {}
    for tid in sorted(team_ids):
        d = api.q(Q_TEAM_MATCHES, {"id": tid})
        t = (d.get("data") or {}).get("team")
        if not t:
            continue
        team_index[tid] = {"name": t.get("name"), "number": t.get("number")}
        for mm in (t.get("matches") or []):
            if mm.get("isFinalized") or mm.get("isScored"):
                match_ids.add(mm["id"])
    print("Finalized matches to pull: %d" % len(match_ids))

    matches, games, raw = {}, [], {}
    for i, mid in enumerate(sorted(match_ids), 1):
        d = api.q(Q_MATCH, {"id": mid})
        m = (d.get("data") or {}).get("match")
        if not m:
            continue
        raw[mid] = m  # keep raw so re-normalization never needs a re-pull
        meta, _rows, persp = normalize_match(m)
        matches[mid] = {"meta": meta}
        games.extend(persp)
        if i % 10 == 0:
            print("  ...%d/%d" % (i, len(match_ids)))
        time.sleep(0.15)

    (DATA / "matches.json").write_text(json.dumps(matches, indent=1))
    (DATA / "matches_raw.json").write_text(json.dumps(raw))
    cols = ["mid", "pid", "name", "team", "sl", "oppMid", "oppPid", "oppName", "oppSl",
            "win", "pts", "fmt", "date", "matchId"]
    with open(DATA / "games.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for g in games:
            w.writerow({k: g.get(k) for k in cols})
    (DATA / "meta.json").write_text(json.dumps({
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "memberId": mem.get("id"), "teams": team_index,
        "counts": {"matches": len(matches), "games": len(games)}}, indent=1))
    print("Wrote %d matches, %d game-rows -> %s" % (len(matches), len(games), DATA))


if __name__ == "__main__":
    main()
