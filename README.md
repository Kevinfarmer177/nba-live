# nba-live

Live data jobs for Kevin's NBA OVERLOAD board (Airtable base "NBA Totals Tracker 2026-27").

| Workflow | Schedule | What it does |
|---|---|---|
| `live-scores.yml` | wakes every 30 min, 10 AM – midnight CT | While a game is live (or tips within 40 min), polls ESPN every 30 seconds and writes Live Away / Live Home / Live State / Live Clock / Live Updated on the Games rows, and each player's running total (Live Stat, Live Min) on their prop rows in Player Minutes. Exits when nothing is on. |
| `dk-props-refs.yml` | hourly at :17, 9 AM – 9 PM CT | `refs_today.py` fills Referee Crew from official.nba.com; `dk_props.py` writes DraftKings player prop lines (via ESPN's odds feed) into Player Minutes. Lines freeze at tip; games loaded after tip get the pregame opening line. |
| `game-feed.yml` | every 30 min, 9 AM – midnight CT | `game_feed.py`: current DraftKings moneyline / spread / total and ESPN's injury report for today's and tomorrow's games → Live ML Away/Home, Live Spread, Live Total, Live Injuries (display only). Lines freeze at tip. The morning runs keep owning the official ML / Spread / Total / Absences fields that picks, v3 and grading use. |

Secret required: `AIRTABLE_TOKEN` (Airtable personal access token, `data.records:read` + `data.records:write` on the NBA base).

Run any job by hand from the Actions tab (Run workflow). Scripts accept `--dry-run`; `live_scores.py` also takes `--once`.

Research and paper tracking only — nothing here places bets.
