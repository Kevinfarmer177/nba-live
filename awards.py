#!/usr/bin/env python3
"""
NBA award races -> Airtable "Awards Races".

DraftKings award futures (MVP, ROY, DPOY, MIP, 6MOY, COY, Clutch) via ESPN's odds feed.
Top 10 per award: current odds, odds the first time we saw them, odds a week ago,
plus a short daily history. Players who drop out of the top 10 keep their row with Rank blank.

Env: AIRTABLE_TOKEN    Usage: python awards.py [--dry-run]
"""
import argparse, json, os, re, sys, time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import requests

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appR3Vx7vTrll630D")
TOKEN = os.environ.get("AIRTABLE_TOKEN")
TABLE = "tblOpZGJIr8PB71OL"
F = {"entry": "fldGHQpO3R3rBr42J", "award": "fldMgowQneWlsRai7", "name": "fldSCqwsZqsOUr1At", "team": "fld5vTHZpH1vlylO8",
     "odds": "fldFsRJnGXkNnLDoR", "first": "fldT9XsIjo3feFjQ4", "week": "fldEy8Yfd0AYgOdXn", "rank": "fld5y8OyAPqiuHmh5",
     "upd": "fldsMurP0z0tmdCgx", "hist": "fldRBbHRijwCNPqFL"}
SEASON = 2027
AWARDS = {2581: "MVP", 2582: "Rookie of the Year", 2583: "Defensive Player", 2584: "Most Improved",
          2585: "6th Man", 2586: "Coach of the Year", 2587: "Clutch Player"}
TOP = 10
CORE = "https://sports.core.api.espn.com/v2/sports/basketball/leagues/nba"
UA = {"User-Agent": "Mozilla/5.0 (cvpm-pull awards)"}
CT = ZoneInfo("America/Chicago")
TEAM_MAP = {"WAS": "WSH", "GS": "GSW", "NY": "NYK", "NO": "NOP", "SA": "SAS", "UTAH": "UTA", "PHO": "PHX"}


def get(url):
    url = url.replace("http://", "https://")
    for i in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            if r.status_code == 200:
                return r.json()
            if r.status_code == 404:
                return None
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return None


_people, _teams = {}, {}


def person(ref):
    if ref not in _people:
        a = get(ref) or {}
        tref = (a.get("team") or {}).get("$ref")
        if tref and tref not in _teams:
            t = get(tref) or {}
            ab = t.get("abbreviation") or ""
            _teams[tref] = TEAM_MAP.get(ab, ab)
        _people[ref] = (a.get("displayName") or a.get("fullName") or "?", _teams.get(tref, "") if tref else "")
    return _people[ref]


def odds(v):
    try:
        return int(str(v).replace("+", ""))
    except ValueError:
        return None


class Airtable:
    def __init__(self, token):
        self.url = f"https://api.airtable.com/v0/{BASE_ID}/{TABLE}"
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
            p = {"pageSize": 100, "returnFieldsByFieldId": "true"}
            if off: p["offset"] = off
            d = self._req("GET", params=p)
            for r in d["records"]:
                out[r["fields"].get(F["entry"])] = (r["id"], r["fields"])
            off = d.get("offset")
            if not off: return out

    def write(self, creates, updates):
        for i in range(0, len(creates), 10):
            self._req("POST", json={"records": [{"fields": f} for f in creates[i:i + 10]], "typecast": True})
        for i in range(0, len(updates), 10):
            self._req("PATCH", json={"records": updates[i:i + 10], "typecast": True})


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    now = datetime.now(CT); today = now.date().isoformat(); wk = (now.date() - timedelta(days=7)).isoformat()
    rows = {}
    for fid, label in AWARDS.items():
        d = get(f"{CORE}/seasons/{SEASON}/futures/{fid}") or {}
        books = ((d.get("futures") or [{}])[0].get("books") or [])
        items = []
        for b in books:
            ref = (b.get("athlete") or {}).get("$ref") or (b.get("team") or {}).get("$ref")
            o = odds(b.get("value"))
            if ref and o is not None:
                items.append((o if o > 0 else -10000 / o, o, ref))  # sort by payout, favorites first
        items.sort()
        for rank, (_, o, ref) in enumerate(items[:TOP], 1):
            name, team = person(ref)
            rows[f"{label} | {name}"] = dict(award=label, name=name, team=team, odds=o, rank=rank)
        print(f"{label}: {len(items)} priced, top {min(TOP, len(items))}", flush=True)
    if a.dry_run:
        for k, v in list(rows.items())[:12]:
            print(" ", k, v["team"], v["odds"])
        return
    if not TOKEN: sys.exit("AIRTABLE_TOKEN is not set")
    at = Airtable(TOKEN); have = at.all(); creates, updates = [], []
    for k, v in rows.items():
        if k in have:
            rid, cur = have[k]
            try: hist = json.loads(cur.get(F["hist"]) or "[]")
            except ValueError: hist = []
            hist = [h for h in hist if h[0] != today] + [[today, v["odds"]]]
            hist = hist[-45:]
            wkv = next((h[1] for h in reversed(hist) if h[0] <= wk), cur.get(F["week"]))
            updates.append({"id": rid, "fields": {F["odds"]: v["odds"], F["rank"]: v["rank"], F["team"]: v["team"], F["upd"]: now.isoformat(),
                                                   F["hist"]: json.dumps(hist), F["week"]: wkv}})
        else:
            creates.append({F["entry"]: k, F["award"]: v["award"], F["name"]: v["name"], F["team"]: v["team"], F["odds"]: v["odds"],
                            F["first"]: v["odds"], F["rank"]: v["rank"], F["upd"]: now.isoformat(), F["hist"]: json.dumps([[today, v["odds"]]])})
    for k, (rid, cur) in have.items():
        if k not in rows and cur.get(F["rank"]) is not None:
            updates.append({"id": rid, "fields": {F["rank"]: None}})
    at.write(creates, updates)
    print(f"awards: {len(creates)} new, {len(updates)} updated")


if __name__ == "__main__":
    main()
