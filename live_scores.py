#!/usr/bin/env python3
"""
Live NBA scores -> Airtable Games rows, every 30 seconds while games are on.

Writes ONLY these Games fields (the nightly loader still owns Final Away/Home):
  Live Away, Live Home, Live State (pre/in/post), Live Clock ("Q3 4:12", "Half", "Final"), Live Updated

One run polls until there is nothing left to watch (no game in progress and the next tip
is more than LEAD_MIN minutes away), or until MAX_MINUTES, then exits.

Env:   AIRTABLE_TOKEN (required), AIRTABLE_BASE_ID (optional)
Usage: python live_scores.py [--once] [--dry-run] [--interval 30] [--max-minutes 345]
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appR3Vx7vTrll630D")
TOKEN = os.environ.get("AIRTABLE_TOKEN")
GAMES = "tblOJ5dyqjOTj87XZ"
F = {  # Games field IDs
    "game": "fldTEAyw4LUdjx8oF",
    "away": "fldGfrhkeOu3LW0lq",
    "home": "fldlF3mRVqJTtCpSS",
    "state": "fldVvQB9zw81JwzBZ",
    "clock": "fldIb6WUD2CLnJwlI",
    "updated": "fldWIv4Fzq2MYl2KD",
    "alerts": "fldgPLpR4roghMm7F",
}
PLAYERS = "tblQxQCXYDVlBW8Lc"  # Player Minutes (one row per player prop)
PF = {"entry": "fld3rcrQwPCXYQOzh", "game": "fldthRq1R9DOPrfCF", "player": "fldq7B2ep7hQpuR1O",
      "prop": "fldcKI185ES3rvchN", "live": "fldtT7lyu5mEDyM3K", "min": "fldBvfIY0H8sy1JBs"}
PAPER = "tblChusq3thgGio20"  # Paper Bets
BF = {"game": "fldzJIPoqpU2y4Lpx", "line": "fldOgkzsYpbPGh97E", "odds": "fldcjwwN0UiSFITGo", "stake": "fldiItuHztPbZZnE9",
      "result": "fldYtKCYZb6V0rnAD", "profit": "fld9L9RiSusoapVWi", "entry": "fldZVfNucLSNQchil", "notes": "fld1SbkfXzrfQXqEs"}
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
UA = {"User-Agent": "Mozilla/5.0 (cvpm-pull live scores)", "Accept": "application/json"}
TEAM_MAP = {"WAS": "WSH", "GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "PHO": "PHX"}
ET = ZoneInfo("America/New_York")
LEAD_MIN = 40  # start watching this many minutes before a tip


def log(*a):
    print(datetime.now(ET).strftime("%H:%M:%S"), *a, flush=True)


def tri(code):
    return TEAM_MAP.get(code, code)


def clock_text(status):
    t = status.get("type", {})
    state = t.get("state")
    if state == "post":
        d = t.get("shortDetail") or "Final"
        return "Final/OT" if "OT" in d else "Final"
    if state != "in":
        return ""
    name = t.get("name", "")
    period = status.get("period") or 0
    q = f"OT{period - 4}" if period > 4 else f"Q{period}"
    if q == "OT1":
        q = "OT"
    if name == "STATUS_HALFTIME":
        return "Half"
    if name == "STATUS_END_PERIOD":
        return f"End {q}"
    return f"{q} {status.get('displayClock', '')}".strip()


def scoreboard(day):
    for i in range(3):
        try:
            r = requests.get(SCOREBOARD, params={"dates": day.strftime("%Y%m%d")}, headers=UA, timeout=15)
            if r.status_code == 200:
                return r.json()
            log("ESPN", r.status_code)
        except requests.RequestException as e:
            log("ESPN error", e)
        time.sleep(3 * (i + 1))
    return None


def events_for(day):
    sb = scoreboard(day)
    out = []
    for ev in (sb or {}).get("events", []):
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        st = ev.get("status", {})
        out.append({
            "id": ev["id"],
            "name": f"{day.isoformat()} {tri(teams['away']['team']['abbreviation'])} @ {tri(teams['home']['team']['abbreviation'])}",
            "state": st.get("type", {}).get("state"),
            "away": int(teams["away"].get("score") or 0),
            "home": int(teams["home"].get("score") or 0),
            "clock": clock_text(st),
            "start": datetime.fromisoformat(ev["date"].replace("Z", "+00:00")),
        })
    return out, sb is not None


def box_stats(event_id):
    """player name -> {Points, Rebounds, Assists, PRA, Threes, min} from ESPN's live box score."""
    try:
        r = requests.get(SUMMARY, params={"event": event_id}, headers=UA, timeout=20)
        r.raise_for_status()
        js = r.json()
        teams = js.get("boxscore", {}).get("players", [])
    except Exception as ex:  # noqa: BLE001
        log("box score fetch failed", event_id, ex)
        return None
    out = {}
    for t in teams:
        for block in t.get("statistics", []):
            keys = block.get("keys", [])
            for a in block.get("athletes", []):
                st = dict(zip(keys, a.get("stats") or []))
                if not st:
                    continue
                def n(k):
                    try:
                        return int(str(st.get(k, "0")).split("-")[0] or 0)
                    except ValueError:
                        return 0
                pts, reb, ast = n("points"), n("rebounds"), n("assists")
                out[a["athlete"]["displayName"]] = {
                    "Points": pts, "Rebounds": reb, "Assists": ast, "PRA": pts + reb + ast,
                    "Threes": n("threePointFieldGoalsMade-threePointFieldGoalsAttempted"), "min": n("minutes")}
    out["__summary__"] = js
    return out


RUN_STEPS = (12, 16, 20, 25)   # alert when an unanswered run reaches each of these
THREE_STREAK = 3                # alert when a player hits this many threes in a row (then each one after)


def momentum(js):
    """[(alert_id, text)] for scoring runs and three-point streaks in this game so far."""
    comp = (js.get("header", {}).get("competitions") or [{}])[0]
    abbr = {c["team"]["id"]: tri(c["team"]["abbreviation"]) for c in comp.get("competitors", []) if c.get("team")}
    names = {}
    for t in js.get("boxscore", {}).get("players", []):
        for b in t.get("statistics", []):
            for a in b.get("athletes", []):
                names[a["athlete"]["id"]] = a["athlete"].get("shortName") or a["athlete"].get("displayName")
    alerts = []
    run_team, run_pts, run_start, opp_pts_at = None, 0, None, None
    streak = {}  # athlete id -> (count, first play id)
    for pl in js.get("plays", []):
        txt = (pl.get("text") or "").lower()
        team = (pl.get("team") or {}).get("id")
        q = (pl.get("period") or {}).get("number")
        clock = (pl.get("clock") or {}).get("displayValue", "")
        if pl.get("scoringPlay") and team:
            pts = int(pl.get("scoreValue") or 0)
            if team == run_team:
                run_pts += pts
            else:
                run_team, run_pts, run_start = team, pts, pl.get("id")
            for step in RUN_STEPS:
                if run_pts >= step and run_pts - pts < step:
                    alerts.append((f"run-{run_start}-{step}",
                                   f"🔥 {abbr.get(team, 'Team')} on a {run_pts}-0 run (Q{q} {clock}) · {pl.get('awayScore')}-{pl.get('homeScore')}"))
        if pl.get("shootingPlay") and "three point" in txt and "free throw" not in txt:
            who = ((pl.get("participants") or [{}])[0].get("athlete") or {}).get("id")
            if not who:
                continue
            if pl.get("scoringPlay"):
                c, first = streak.get(who, (0, None))
                if c == 0 or not first:
                    first = pl.get("id")
                c += 1
                streak[who] = (c, first)
                if c >= THREE_STREAK:
                    alerts.append((f"3s-{who}-{first}-{c}",
                                   f"🎯 {names.get(who, 'Player')} ({abbr.get(team, '')}) has hit {c} threes in a row (Q{q} {clock})"))
            else:
                streak[who] = (0, None)
    return alerts


def write_alerts(at, row, found, dry, game):
    """Append any new momentum alerts to the game's Live Alerts field (last 15 kept)."""
    if not row or not found:
        return
    rid, cur = row
    have = (cur.get(F["alerts"]) or "").strip().splitlines()
    seen = {ln.split("|")[1].strip() for ln in have if ln.count("|") >= 2}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new = [f"{stamp} | {aid} | {txt}" for aid, txt in found if aid not in seen]
    if not new:
        return
    lines = (have + new)[-15:]
    for ln in new:
        log(game, "ALERT", ln.split("|", 2)[2].strip())
    if not dry:
        at.update([{"id": rid, "fields": {F["alerts"]: "\n".join(lines)}}])
    cur[F["alerts"]] = "\n".join(lines)


def grade_props(bt, game, box):
    """Grade pending over bets on player props from the final box score."""
    try:
        bets = bt.pending_prop_bets(game)
    except Exception as ex:  # noqa: BLE001
        log("could not read pending bets", game, ex)
        return
    ups = []
    for rid, f in bets:
        entry = f.get(BF["entry"]) or ""
        parts = entry.split(" ", 1)
        if len(parts) < 2:
            continue
        name_prop = parts[1].rsplit(" ", 1)
        if len(name_prop) < 2 or name_prop[0] not in box or name_prop[1] not in box[name_prop[0]]:
            log("can't grade", entry, "- player/prop not in box score")
            continue
        stat, line = box[name_prop[0]][name_prop[1]], f.get(BF["line"])
        stake, odds = f.get(BF["stake"]) or 0, f.get(BF["odds"]) or 0
        if line is None or not odds:
            continue
        if stat > line:
            res, profit = "Win", round(stake * (odds / 100 if odds > 0 else 100 / abs(odds)), 2)
        elif stat < line:
            res, profit = "Loss", -stake
        else:
            res, profit = "Push", 0
        note = (f.get(BF["notes"]) or "").rstrip() + f"\nGraded by nba-live: final {name_prop[1]} {stat} vs {line}."
        ups.append({"id": rid, "fields": {BF["result"]: res, BF["profit"]: profit, BF["notes"]: note.strip()}})
        log(f"graded {entry}: {stat} vs {line} -> {res} {profit:+.2f}")
    if ups:
        bt.update(ups)


class Airtable:
    def __init__(self, token, table=GAMES):
        self.url = f"https://api.airtable.com/v0/{BASE_ID}/{table}"
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}"})

    def _req(self, method, **kw):
        for i in range(5):
            r = self.s.request(method, self.url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Airtable {method}: {r.status_code} {r.text[:300]}")
            return r.json()
        raise RuntimeError("Airtable rate limited")

    def rows_for(self, names):
        """name -> (record id, current live fields) for the given Game names."""
        if not names:
            return {}
        formula = "OR(" + ",".join(f"{{Game}}='{n}'" for n in names) + ")"
        out, offset = {}, None
        while True:
            params = {"filterByFormula": formula, "pageSize": 100, "returnFieldsByFieldId": "true",
                      "fields[]": list(F.values())}
            if offset:
                params["offset"] = offset
            data = self._req("GET", params=params)
            for rec in data["records"]:
                f = rec["fields"]
                out[f.get(F["game"])] = (rec["id"], f)
            offset = data.get("offset")
            if not offset:
                return out

    def props_for(self, game):
        """(player, prop) -> [record id, live stat, live min] for one game's prop rows."""
        out, offset = {}, None
        while True:
            params = {"filterByFormula": f"{{Game}}='{game}'", "pageSize": 100, "returnFieldsByFieldId": "true",
                      "fields[]": [PF["player"], PF["prop"], PF["live"], PF["min"]]}
            if offset:
                params["offset"] = offset
            data = self._req("GET", params=params)
            for rec in data["records"]:
                f = rec["fields"]
                out[(f.get(PF["player"]), f.get(PF["prop"]))] = [rec["id"], f.get(PF["live"]), f.get(PF["min"])]
            offset = data.get("offset")
            if not offset:
                return out

    def pending_prop_bets(self, game):
        """Paper Bets still Pending for this game that point at a Player Minutes prop row."""
        params = {"filterByFormula": f"AND({{Game}}='{game}', {{Result}}='Pending', {{Prop Entry}}!='')",
                  "pageSize": 100, "returnFieldsByFieldId": "true", "fields[]": list(BF.values())}
        return [(r["id"], r["fields"]) for r in self._req("GET", params=params)["records"]]

    def update(self, updates):
        for i in range(0, len(updates), 10):
            self._req("PATCH", json={"records": updates[i:i + 10]})
            time.sleep(0.25)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--max-minutes", type=int, default=345)
    args = ap.parse_args()
    if not TOKEN and not args.dry_run:
        sys.exit("AIRTABLE_TOKEN is not set")
    at = Airtable(TOKEN) if TOKEN else None
    pt = Airtable(TOKEN, PLAYERS) if TOKEN else None
    bt = Airtable(TOKEN, PAPER) if TOKEN else None
    prop_rows, prop_loaded, box_done = {}, {}, set()

    started = time.time()
    rows, rows_day, missing_warned = {}, None, set()
    while True:
        now_et = datetime.now(ET)
        days = [now_et.date()]
        if now_et.hour < 4:  # late games that started "yesterday" in Eastern time
            days.insert(0, now_et.date() - timedelta(days=1))
        events, ok = [], True
        for d in days:
            evs, good = events_for(d)
            events += evs
            ok = ok and good

        # (re)load the matching Airtable rows when the slate changes or every ~10 min
        names = sorted(e["name"] for e in events)
        if at and (rows_day != names or int(time.time() - started) % 600 < args.interval):
            rows = at.rows_for(names)
            rows_day = names
            for n in names:
                if n not in rows and n not in missing_warned:
                    log("no Airtable row for", n)
                    missing_warned.add(n)

        updates = []
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        for e in events:
            if e["state"] not in ("in", "post"):
                continue
            want = {F["away"]: e["away"], F["home"]: e["home"], F["state"]: e["state"], F["clock"]: e["clock"]}
            rid, cur = rows.get(e["name"], (None, {}))
            if not rid:
                continue
            if all(cur.get(k) == v for k, v in want.items()):
                continue
            updates.append({"id": rid, "fields": {**want, F["updated"]: stamp}})
            cur.update(want)
            log(f"{e['name']}: {e['away']}-{e['home']} {e['clock']}")
        if updates and not args.dry_run:
            at.update(updates)
        elif updates:
            log(f"(dry run) would update {len(updates)} rows")

        # live player stats on the prop rows (in-progress games every cycle; finished games one last time)
        p_updates = []
        for e in events:
            if e["state"] not in ("in", "post") or e["name"] in box_done:
                continue
            if pt and (e["name"] not in prop_rows or time.time() - prop_loaded.get(e["name"], 0) > 600):
                prop_rows[e["name"]] = pt.props_for(e["name"])
                prop_loaded[e["name"]] = time.time()
            rows_p = prop_rows.get(e["name"]) or {}
            if not rows_p and pt:
                if e["state"] == "post":
                    box_done.add(e["name"])
                continue
            box = box_stats(e["id"])
            if box is None:
                continue
            js = box.pop("__summary__", {})
            if e["state"] == "in" and at:
                write_alerts(at, rows.get(e["name"]), momentum(js), args.dry_run, e["name"])
            for name, st in box.items():
                for prop in ("Points", "Rebounds", "Assists", "PRA", "Threes"):
                    hit = rows_p.get((name, prop))
                    if not hit or (hit[1] == st[prop] and hit[2] == st["min"]):
                        continue
                    p_updates.append({"id": hit[0], "fields": {PF["live"]: st[prop], PF["min"]: st["min"]}})
                    hit[1], hit[2] = st[prop], st["min"]
            if e["state"] == "post":
                box_done.add(e["name"])
                if bt and not args.dry_run:
                    grade_props(bt, e["name"], box)
            if args.dry_run and not pt:
                some = sorted(box.items(), key=lambda kv: -kv[1]["Points"])[:3]
                log(e["name"], "box:", ", ".join(f"{k} {v['Points']}p {v['Rebounds']}r {v['Assists']}a" for k, v in some))
        if p_updates and not args.dry_run:
            pt.update(p_updates)
            log(f"player stats: {len(p_updates)} prop rows updated")

        # keep going?
        now = datetime.now(timezone.utc)
        live = [e for e in events if e["state"] == "in"]
        soon = [e for e in events if e["state"] == "pre" and e["start"] - now <= timedelta(minutes=LEAD_MIN)]
        if args.once:
            break
        if ok and not live and not soon:
            later = [e for e in events if e["state"] == "pre"]
            log("nothing live" + (f"; next tip {min(e['start'] for e in later).astimezone(ET):%I:%M %p} ET" if later else "; slate done"))
            break
        if time.time() - started > args.max_minutes * 60:
            log("max run time reached; the next scheduled run picks up")
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
