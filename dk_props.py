#!/usr/bin/env python3
"""
DraftKings player prop lines (via ESPN's odds feed) -> Airtable Player Minutes rows.

For every game today that hasn't finished, logs each player's DraftKings line for
Points, Rebounds, Assists, PRA (points+rebounds+assists) and Threes.
One row per player per prop per day ("YYYY-MM-DD Player Prop"); re-runs update the line
and note the move. The NBA Mock Board draws these as the tick on each player's bar.

Env:   AIRTABLE_TOKEN (required), AIRTABLE_BASE_ID (optional)
Usage: python dk_props.py [--date YYYY-MM-DD] [--dry-run]
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appR3Vx7vTrll630D")
TOKEN = os.environ.get("AIRTABLE_TOKEN")
PM = "tblQxQCXYDVlBW8Lc"  # Player Minutes
P = {
    "entry": "fld3rcrQwPCXYQOzh", "date": "fldKhc0Pn1ozA1Sh4", "game": "fldthRq1R9DOPrfCF",
    "player": "fldq7B2ep7hQpuR1O", "team": "fldU0A3PymmiyCXfh", "prop": "fldcKI185ES3rvchN",
    "line": "fldRpbsSwIuPxQRRx", "notes": "fldPhmPZcZKEr8csA",
    "l10hits": "fldfeDI64bZEcqO3p", "l10games": "fldOAlqQBrms2zG4c", "avg": "fldgxmqp0ut2aicYB",
}
GAMELOG = "https://site.web.api.espn.com/apis/common/v3/sports/basketball/nba/athletes/{}/gamelog"
STATKEY = {"Points": ("points",), "Rebounds": ("totalRebounds",), "Assists": ("assists",),
           "PRA": ("points", "totalRebounds", "assists"),
           "Threes": ("threePointFieldGoalsMade-threePointFieldGoalsAttempted",)}
_logs = {}


def game_log(aid, before_event):
    """Regular-season games before today's game, newest first: list of {stat name: value}."""
    if aid not in _logs:
        try:
            d = get(GAMELOG.format(aid)) or {}
        except RuntimeError:
            d = {}
        rows = []
        names = d.get("names") or []
        for st in d.get("seasonTypes") or []:
            if "Regular" not in (st.get("displayName") or ""):
                continue
            for c in st.get("categories") or []:
                for e in c.get("events") or []:
                    meta = (d.get("events") or {}).get(e["eventId"], {})
                    rows.append((meta.get("gameDate", ""), e["eventId"], dict(zip(names, e["stats"]))))
        rows.sort(reverse=True)
        _logs[aid] = rows
    return [r[2] for r in _logs[aid] if r[1] != before_event]


def prop_value(stats, prop):
    tot = 0.0
    for k in STATKEY[prop]:
        v = str(stats.get(k) or "0").split("-")[0]
        try:
            tot += float(v)
        except ValueError:
            return None
    return tot


def form(aid, event_id, prop, line):
    """(hits over this line in the last 10, games counted, season avg) from games before today."""
    vals = [v for v in (prop_value(g, prop) for g in game_log(aid, event_id)) if v is not None]
    if not vals:
        return None, 0, None
    last = vals[:10]
    return sum(1 for v in last if v > line), len(last), round(sum(vals) / len(vals), 1)
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
DK = 100  # ESPN's provider id for DraftKings
TYPES = {
    "Total Points": "Points",
    "Total Rebounds": "Rebounds",
    "Total Assists": "Assists",
    "Total Points, Rebounds, and Assists": "PRA",
    "Total 3-Point Field Goals": "Threes",
}
UA = {"User-Agent": "Mozilla/5.0 (cvpm-pull dk props)", "Accept": "application/json"}
TEAM_MAP = {"WAS": "WSH", "GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "PHO": "PHX"}
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")


def get(url, params=None):
    url = url.replace("http://", "https://")
    for i in range(4):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    raise RuntimeError(f"GET failed: {url}")


def props_for(event_id):
    items, page = [], 1
    while True:
        d = get(f"{CORE}/events/{event_id}/competitions/{event_id}/odds/{DK}/propBets", {"limit": 25, "page": page})
        if not d:
            return items
        items += d.get("items", [])
        if page >= d.get("pageCount", 1):
            return items
        page += 1


class Airtable:
    def __init__(self, token):
        self.url = f"https://api.airtable.com/v0/{BASE_ID}/{PM}"
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}"})

    def _req(self, method, **kw):
        for i in range(5):
            time.sleep(0.25)
            r = self.s.request(method, self.url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"Airtable {method}: {r.status_code} {r.text[:300]}")
            return r.json()
        raise RuntimeError("Airtable rate limited")

    def existing(self, day):
        out, offset = {}, None
        while True:
            params = {"filterByFormula": f"IS_SAME({{Date}}, '{day}', 'day')", "pageSize": 100,
                      "returnFieldsByFieldId": "true", "fields[]": [P["entry"], P["line"], P["notes"], P["l10games"]]}
            if offset:
                params["offset"] = offset
            data = self._req("GET", params=params)
            for rec in data["records"]:
                out[rec["fields"].get(P["entry"])] = (rec["id"], rec["fields"])
            offset = data.get("offset")
            if not offset:
                return out

    def write(self, creates, updates):
        for i in range(0, len(creates), 10):
            self._req("POST", json={"records": [{"fields": f} for f in creates[i:i + 10]], "typecast": True})
        for i in range(0, len(updates), 10):
            self._req("PATCH", json={"records": updates[i:i + 10], "typecast": True})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="Eastern date YYYY-MM-DD (default today)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", help="dry run: write all lines to this JSON file")
    args = ap.parse_args()
    if not TOKEN and not args.dry_run:
        sys.exit("AIRTABLE_TOKEN is not set")
    day = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(ET).date()

    sb = get(SCOREBOARD, {"dates": day.strftime("%Y%m%d")}) or {}
    athletes, lines = {}, {}
    for ev in sb.get("events", []):
        state = ev.get("status", {}).get("type", {}).get("state")
        if state == "post":
            continue
        started = state != "pre"  # after tip ESPN's 'current' is an in-game line; use the pregame open
        comp = ev["competitions"][0]
        teams = {c["homeAway"]: c for c in comp["competitors"]}
        team_by_id = {c["team"]["id"]: TEAM_MAP.get(c["team"]["abbreviation"], c["team"]["abbreviation"]) for c in comp["competitors"]}
        game = f"{day.isoformat()} {team_by_id[teams['away']['team']['id']]} @ {team_by_id[teams['home']['team']['id']]}"
        for it in props_for(ev["id"]):
            prop = TYPES.get(it.get("type", {}).get("name"))
            ref = (it.get("athlete") or {}).get("$ref")
            line = ((it.get("open" if started else "current") or {}).get("target") or {}).get("value")
            if not prop or not ref or line is None:
                continue
            aid = re.search(r"athletes/(\d+)", ref).group(1)
            if aid not in athletes:
                a = get(ref) or {}
                tid = re.search(r"teams/(\d+)", (a.get("team") or {}).get("$ref", "") or "")
                athletes[aid] = (a.get("displayName") or a.get("fullName"), team_by_id.get(tid.group(1)) if tid else None)
            name, team = athletes[aid]
            if name:
                lines[(name, prop)] = (float(line), game, team, started, aid, ev["id"])
        print(f"{game}: {sum(1 for k, v in lines.items() if v[1] == game)} lines", flush=True)

    stamp = datetime.now(CT).strftime("%-I:%M %p")
    if args.dry_run:
        for (name, prop), (line, game, team, started, aid, eid) in sorted(lines.items())[:40]:
            h, n, avg = form(aid, eid, prop, line)
            print(f"  {game} | {team} | {name} {prop} {line} | last {n}: {h} over | avg {avg}")
        print(f"(dry run) {len(lines)} lines")
        if args.json:
            import json
            json.dump([dict(name=n, prop=pr, line=l, game=g, team=t, started=st) for (n, pr), (l, g, t, st, _a, _e) in lines.items()], open(args.json, "w"))
        return

    at = Airtable(TOKEN)
    have = at.existing(day.isoformat())
    creates, updates = [], []
    for (name, prop), (line, game, team, started, aid, eid) in lines.items():
        entry = f"{day.isoformat()} {name} {prop}"
        if entry in have:
            rid, cur = have[entry]
            old = cur.get(P["line"])
            f = {}
            if old != line and not started:  # lines freeze at tip
                note = (cur.get(P["notes"]) or "").rstrip()
                f.update({P["line"]: line, P["notes"]: (note + f"\n{stamp} CT: was {old}").strip()})
            if f or cur.get(P["l10games"]) is None:  # (re)compute form vs the current line
                h, n, avg = form(aid, eid, prop, f.get(P["line"], old if old is not None else line))
                f.update({P["l10hits"]: h, P["l10games"]: n, P["avg"]: avg})
            if f:
                updates.append({"id": rid, "fields": f})
            continue
        h, n, avg = form(aid, eid, prop, line)
        creates.append({P["l10hits"]: h, P["l10games"]: n, P["avg"]: avg, P["entry"]: entry, P["date"]: day.isoformat(), P["game"]: game, P["player"]: name,
                        P["team"]: team or "", P["prop"]: prop, P["line"]: line,
                        P["notes"]: ("DraftKings opening line (loaded after tip)" if started else f"DraftKings line via ESPN odds feed at {stamp} CT")})
    at.write(creates, updates)
    print(f"DraftKings props: {len(creates)} new, {len(updates)} moved, {len(lines)} total for {day}")


if __name__ == "__main__":
    main()
