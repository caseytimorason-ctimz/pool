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

## Weekly refresh (cloud or laptop)
Data via APA's GraphQL API, headless auth with a refresh token: in a cloud session from the
`APA_REFRESH_TOKEN` environment variable (needs `gql.poolplayers.com` in its allowed domains),
on the Mac from the Keychain (service `apa-refresh-token`). No secrets in this repo.
```
python3 pipeline/apa_pull.py && python3 pipeline/backfill.py --current \
  && python3 pipeline/analyze.py && python3 pipeline/build_site_data.py
git add -A && git commit -m "weekly refresh" && git push
```
- The match history is **committed** (`data/matches.json`, `data/games.csv`), so a fresh
  checkout has the whole league and each step only adds to it. `apa_pull.py` brings in our
  teams' matches, `backfill.py --current` the rest of this season's divisions. Both re-fetch the
  last few weeks so a score APA corrects later still lands.
- `build_site_data.py` writes `data.json` directly, including the Team tab's `results` feed.
  It **refuses to write** a bundle that lost data the current one has (an empty or shrunken
  feed, fewer players, a scheduled opponent with no roster) and prints what it lost. Re-run
  the pull rather than reaching for `--force`. A shorter `schedule` is normal: played matches
  leave it.
- Hand-maintained inputs live in `data/`: `league.json` (bylaws, events), `postseason.json`,
  `official_rules.json`. Edit those, not `data.json`, or the next refresh undoes the edit.
- Rebuilding the whole history from scratch (rarely needed): `python3 pipeline/backfill.py`
  then `python3 pipeline/backfill_members.py --all-known` — tens of thousands of requests.

There is no HTML rebuild step — see the warning above.

### Before a tournament (Tri-Cup, playoffs)
Tournaments play everyone at the **higher of the SL they ended last session at and their SL
this session**. The posted roster can be wrong, and our game history for other divisions can
be a whole session behind, so check it against APA directly:
```
python3 pipeline/tourney_sl.py
git add data/tourney_sl.json data.json && git commit -m "tournament SL check" && git push
```
It reads the tournament event in `data/league.json` (set `prevSession.label` and
`currentSession` on it) and checks every player on both sides. The Match tab then raises anyone
listed too low and names anyone it still couldn't verify.
