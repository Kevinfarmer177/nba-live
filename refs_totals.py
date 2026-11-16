#!/usr/bin/env python3
"""
Referee crews vs the DraftKings game total -> Airtable Referees table.

For every ref: average of (final combined score - DK total) over his last 60 games
(last season, cached in refs_last_season.json, plus this season from ESPN), the game
count, and a JSON log. The board turns the tonight's crew average into a totals lean.

Env: AIRTABLE_TOKEN    Usage: python refs_totals.py [--dry-run]
"""
import argparse, concurrent.futures as cf, json, os, sys, time
from collections import defaultdict
from datetime import date, timedelta, datetime
from zoneinfo import ZoneInfo
import requests

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appR3Vx7vTrll630D")
TOKEN = os.environ.get("AIRTABLE_TOKEN")
REFS = "tblWPqE47YvJZ7cbK"
F = {"name": "fldmsgMRaHgSDPBWG", "vs": "fldZdFc3BWkheWPJp", "n": "fldMvtYTYccliq0Y9", "log": "fldxpeYha2kofsAIV"}
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/summary"
SEASON_START = date(2026, 10, 20)
KEEP = 60
UA = {"User-Agent": "Mozilla/5.0 (cvpm-pull refs totals)"}
ET = ZoneInfo("America/New_York")


def get(url, params):
    for i in range(4):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


def finished_events():
    out, d, today = [], SEASON_START, datetime.now(ET).date()
    days = []
    while d <= today:
        days.append(d); d += timedelta(1)
    def sb(day):
        j = get(SCOREBOARD, {"dates": day.strftime("%Y%m%d")}) or {}
        return [(day.isoformat(), e["id"]) for e in j.get("events", []) if e["status"]["type"]["state"] == "post"]
    with cf.ThreadPoolExecutor(8) as ex:
        for l in ex.map(sb, days):
            out += l
    return out


def game(e):
    day, eid = e
    s = get(SUMMARY, {"event": eid}) or {}
    refs = [o.get("displayName") for o in (s.get("gameInfo") or {}).get("officials", []) if o.get("displayName")]
    comp = ((s.get("header") or {}).get("competitions") or [{}])[0]
    tot = sum(int(c.get("score") or 0) for c in comp.get("competitors", []))
    ou = next((pc.get("overUnder") for pc in (s.get("pickcenter") or []) if pc.get("overUnder")), None)
    return [day, eid, refs, tot, ou]


def norm(n):
    return " ".join(str(n or "").lower().replace(".", "").split())


class Airtable:
    def __init__(self, token):
        self.url = f"https://api.airtable.com/v0/{BASE_ID}/{REFS}"
        self.s = requests.Session(); self.s.headers.update({"Authorization": f"Bearer {token}"})

    def _req(self, method, **kw):
        for i in range(5):
            r = self.s.request(method, self.url, timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(2 * (i + 1)); continue
            r.raise_for_status(); return r.json()
        raise RuntimeError("rate limited")

    def all(self):
        out, off = {}, None
        while True:
            p = {"pageSize": 100, "returnFieldsByFieldId": "true", "fields[]": [F["name"]]}
            if off: p["offset"] = off
            d = self._req("GET", params=p)
            for r in d["records"]:
                out[norm(r["fields"].get(F["name"]))] = r["id"]
            off = d.get("offset")
            if not off: return out

    def write(self, ups, creates):
        for i in range(0, len(ups), 10):
            self._req("PATCH", json={"records": ups[i:i + 10], "typecast": True})
        for i in range(0, len(creates), 10):
            self._req("POST", json={"records": [{"fields": f} for f in creates[i:i + 10]], "typecast": True})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    here = os.path.dirname(os.path.abspath(__file__))
    games = json.load(open(os.path.join(here, "refs_last_season.json")))
    with cf.ThreadPoolExecutor(10) as ex:
        games += [g for g in ex.map(game, finished_events()) if g[2] and g[4]]
    games.sort(key=lambda g: g[0])
    logs, names = defaultdict(list), {}
    for day, eid, refs, tot, ou in games:
        if tot == ou:
            diff = 0.0
        else:
            diff = round(tot - float(ou), 1)
        for r in refs:
            logs[norm(r)].append([eid, diff]); names[norm(r)] = r
    print(f"{len(games)} games, {len(logs)} refs", flush=True)
    rows = {}
    for k, l in logs.items():
        l = l[-KEEP:]
        rows[k] = {F["vs"]: round(sum(x[1] for x in l) / len(l), 1), F["n"]: len(l), F["log"]: json.dumps(l, separators=(",", ":"))}
    if a.dry_run:
        for k, v in sorted(rows.items(), key=lambda kv: kv[1][F["vs"]])[:8]:
            print(f"  {names[k]}: {v[F['vs']]:+.1f} over {v[F['n']]}")
        return
    if not TOKEN: sys.exit("AIRTABLE_TOKEN is not set")
    at = Airtable(TOKEN); have = at.all()
    ups = [{"id": have[k], "fields": v} for k, v in rows.items() if k in have]
    creates = [dict(v, **{F["name"]: names[k]}) for k, v in rows.items() if k not in have and v[F["n"]] >= 10]
    at.write(ups, creates)
    print(f"refs totals: {len(ups)} updated, {len(creates)} new")


if __name__ == "__main__":
    main()
