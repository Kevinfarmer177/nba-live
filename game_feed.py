#!/usr/bin/env python3
"""
Fresh game lines and injury reports -> Airtable Games rows (display fields only).

  --lines      current DraftKings moneyline / spread / total (via ESPN's odds feed)
               -> Live ML Away, Live ML Home, Live Spread (home side), Live Total, Lines Updated.
               Lines stop updating at tip, so the last value is the closing line.
  --injuries   ESPN's league injury report -> Live Injuries (same "TEAM Out: ...; Questionable: ..."
               format the board already reads), Injuries Updated.

The morning runs own the official fields (ML Home/Away, Spread, Total, Absences) that the picks,
v3 and grading use. This script never writes those.

Covers today's and tomorrow's slates (Eastern dates). Only writes when something changed.
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
F = {
    "game": "fldTEAyw4LUdjx8oF",
    "ml_a": "fldzqXQqnTzut0LkX", "ml_h": "fld52e3ytHOyHBGbZ", "spread": "fld6mIO5UaDY4lAVH",
    "total": "fldNnnwjwal6YYw8z", "lines_upd": "fldBushguMyQNOkbx",
    "inj": "fldQCcDSmVDJy5XV6", "inj_upd": "fldg3txc9DZKspeyK",
}
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ODDS = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba/events/{id}/competitions/{id}/odds"
INJURIES = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
UA = {"User-Agent": "Mozilla/5.0 (nba-live game feed)", "Accept": "application/json"}
TEAM_MAP = {"WAS": "WSH", "GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "PHO": "PHX"}
ET = ZoneInfo("America/New_York")
CT = ZoneInfo("America/Chicago")
DK = "100"  # ESPN provider id for DraftKings


def log(*a):
    print(datetime.now(CT).strftime("%H:%M:%S"), *a, flush=True)


def tri(code):
    return TEAM_MAP.get(code, code)


def get(url, params=None):
    for i in range(3):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


def slate(day):
    sb = get(SCOREBOARD, {"dates": day.strftime("%Y%m%d")}) or {}
    out = []
    for ev in sb.get("events", []):
        comp = ev["competitions"][0]
        t = {c["homeAway"]: c for c in comp["competitors"]}
        out.append({
            "id": ev["id"], "state": ev.get("status", {}).get("type", {}).get("state"),
            "away": tri(t["away"]["team"]["abbreviation"]), "home": tri(t["home"]["team"]["abbreviation"]),
            "name": f"{day.isoformat()} {tri(t['away']['team']['abbreviation'])} @ {tri(t['home']['team']['abbreviation'])}",
        })
    return out


def american(v):
    if v is None:
        return None
    try:
        return int(round(float(str(v).replace("+", ""))))
    except ValueError:
        return None


def dk_lines(event_id):
    data = get(ODDS.format(id=event_id)) or {}
    for it in data.get("items", []):
        if str((it.get("provider") or {}).get("id")) != DK:
            continue
        a, h = it.get("awayTeamOdds") or {}, it.get("homeTeamOdds") or {}
        spread = it.get("spread")  # ESPN gives the home team's number (+ = home dog)
        return {
            F["ml_a"]: american(a.get("moneyLine")), F["ml_h"]: american(h.get("moneyLine")),
            F["spread"]: float(spread) if spread is not None else None,
            F["total"]: float(it["overUnder"]) if it.get("overUnder") is not None else None,
        }
    return None


STATUS = {"out": "Out", "doubtful": "Doubtful", "questionable": "Questionable", "day-to-day": "Questionable",
          "probable": "Probable", "suspension": "Out"}
ORDER = ["Out", "Doubtful", "Questionable", "Probable"]


def injury_report():
    """team abbr -> {status: [ 'Name (why)', ... ]}"""
    data = get(INJURIES) or {}
    out = {}
    for team in data.get("injuries", []):
        for inj in team.get("injuries", []):
            ath = inj.get("athlete") or {}
            ab = tri(((ath.get("team") or {}).get("abbreviation")) or "")
            if not ab:
                continue
            st = STATUS.get(str(inj.get("status", "")).lower())
            if not st:
                continue
            det = inj.get("details") or {}
            why = " ".join(x for x in [det.get("side") if det.get("side") not in (None, "Not Specified") else None,
                                       det.get("type"), det.get("detail")] if x).lower() or "injury"
            out.setdefault(ab, {}).setdefault(st, []).append(f"{ath.get('displayName')} ({why})")
    return out


def injury_text(rep, away, home):
    stamp = datetime.now(CT).strftime("%-m/%-d %-I:%M %p CT")
    parts = []
    for ab in (away, home):
        groups = rep.get(ab) or {}
        if not groups:
            parts.append(f"{ab} Available: no injuries listed.")
            continue
        segs = [f"{st}: {', '.join(sorted(groups[st]))}" for st in ORDER if groups.get(st)]
        parts.append(f"{ab} " + "; ".join(segs) + ".")
    return "\n".join(parts) + f"\nSource: ESPN injury report, {stamp}"


class Airtable:
    def __init__(self, token):
        self.url = f"https://api.airtable.com/v0/{BASE_ID}/{GAMES}"
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

    def rows(self, names):
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
                out[rec["fields"].get(F["game"])] = (rec["id"], rec["fields"])
            offset = data.get("offset")
            if not offset:
                return out

    def update(self, updates):
        for i in range(0, len(updates), 10):
            self._req("PATCH", json={"records": updates[i:i + 10]})
            time.sleep(0.25)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lines", action="store_true")
    ap.add_argument("--injuries", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not (args.lines or args.injuries):
        args.lines = args.injuries = True
    if not TOKEN and not args.dry_run:
        sys.exit("AIRTABLE_TOKEN is not set")

    today = datetime.now(ET).date()
    games = slate(today) + slate(today + timedelta(days=1))
    games = [g for g in games if g["state"] == "pre"]  # lines freeze at tip; finished games keep their report
    if not games:
        log("no upcoming games")
        return
    at = Airtable(TOKEN) if TOKEN else None
    rows = at.rows([g["name"] for g in games]) if at else {}
    rep = injury_report() if args.injuries else {}
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    updates, n_lines, n_inj = [], 0, 0
    for g in games:
        rid, cur = rows.get(g["name"], (None, {}))
        if at and not rid:
            log("no Airtable row for", g["name"])
            continue
        fields = {}
        if args.lines:
            ln = dk_lines(g["id"])
            if ln and any(v is not None for v in ln.values()):
                ln = {k: v for k, v in ln.items() if v is not None}
                if any(cur.get(k) != v for k, v in ln.items()):
                    fields.update(ln)
                    fields[F["lines_upd"]] = stamp
                    n_lines += 1
                if args.dry_run:
                    log(g["name"], "lines", ln)
        if args.injuries and rep:
            txt = injury_text(rep, g["away"], g["home"])
            body = txt.rsplit("\nSource:", 1)[0]
            if (cur.get(F["inj"]) or "").rsplit("\nSource:", 1)[0] != body:
                fields[F["inj"]] = txt
                fields[F["inj_upd"]] = stamp
                n_inj += 1
            if args.dry_run and g is games[0]:
                log(g["name"], "injuries:\n" + txt)
        if fields and rid:
            updates.append({"id": rid, "fields": fields})
    if updates and not args.dry_run:
        at.update(updates)
    log(f"{len(games)} upcoming games · lines changed on {n_lines} · injury reports changed on {n_inj}")


if __name__ == "__main__":
    main()
