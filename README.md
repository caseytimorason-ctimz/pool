# Rack Sheet — APA Pool Matchup Console

Private tool for our APA teams: point it at an upcoming opponent and it ranks each of our
players against theirs, plus explore any player's 8-year skill-level history and head-to-heads.

**Dashboard:** `index.html`, hand-maintained. It fetches `./data.json` at runtime — the data is
*not* inlined at build time any more. Connect this repo to Netlify — publish directory `.`, no
build command.

> **Do not regenerate `index.html` from `pipeline/template.html`.** The template stopped being
> the source in early September and is frozen at 2026-09-03 (959 lines, against `index.html`'s
> ~2,960). Rebuilding from it silently reverts the app by two weeks — the Train tab, the
> opponent scout card, the rotation tracker, the game-tree optimiser and the player match log
> all disappear — and re-inlines a 20 MB `data.json` that `index.html` now loads on its own.
> Edit `index.html` directly.

## Matchup engine
For each pairing (our A vs their Q) it blends, weighted by sample size:
1. League baseline — empirical win rate for A's SL vs Q's SL across all our matches.
2. A's edge vs that SL — using only A's games at A's *current* SL (SL is time-dependent).
3. Direct head-to-head A vs Q (by member id), if any.
Thin evidence falls back to baseline and is flagged low-confidence. Full spec: ANALYSIS-DESIGN.md.

## Weekly refresh (laptop)
Data via APA's GraphQL API, headless auth with a refresh token in macOS Keychain
(service `apa-refresh-token`) — no secrets in this repo.
```
python3 pipeline/apa_pull.py && python3 pipeline/analyze.py && python3 pipeline/build_site_data.py
git add -A && git commit -m "weekly refresh" && git push
```
There is no HTML rebuild step — see the warning above.

### Check the bundle before you push
Every refresh since 09-11 has shipped a damaged `data.json` — 09-12 (`teams` 0, `results` 0),
09-15 (`teams` 7, `results` 0), 09-16 (`teams` 19, `results` 0) — and the app fails quietly when
they do: an empty `results` feed takes out the rotation tracker, the team record and the match
list, and makes the Last 8/4/2 chips silently no-op; a short `teams` map takes out opponent
scouting for whichever nights those teams are on. All three needed repairing by hand. Until the
builder refuses to emit a shrunken bundle, diff it against the last good one before committing:

```
python3 - <<'PY'
import json, subprocess
new = json.load(open('data.json'))
old = json.loads(subprocess.run(['git','show','HEAD:data.json'],capture_output=True,text=True).stdout)
for k in ('teams','results','schedule','players','sessionStats'):
    a, b = len(old.get(k) or {}), len(new.get(k) or {})
    print(f'{"FAIL" if b < a else "ok  "} {k:<14} {a} -> {b}')
missing = [m['oppTeam'] for m in new['schedule']
           if not (new['teams'].get(str(m['oppTeamId'])) or {}).get('roster')]
print(('FAIL scheduled opponents with no roster: ' + ', '.join(sorted(set(missing)))) if missing
      else 'ok   every scheduled opponent has a roster')
PY
```
Any `FAIL` means the pull came back short — re-run it rather than committing.
