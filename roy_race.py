#!/usr/bin/env python3
"""
CVPM Rookie of the Year race -> Airtable "Award Races" (Race = "Rookie of the Year").

Rookies = ESPN roster experience 0. Ranked by CVPM (Season) from the CVPM Leaderboard,
floors: 5+ games and 12+ MPG (3+ games until any rookie reaches 5). Top 10 rows, same
fields the morning run writes for MVP / Sixth Man.

Env: AIRTABLE_TOKEN    Usage: python roy_race.py [--dry-run]
"""
import argparse, os, sys, time, unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appR3Vx7vTrll630D")
TOKEN = os.environ.get("AIRTABLE_TOKEN")
LB = "tblE9WOeByHz4j6yQ"
L = {"name": "fldx4G5VA2K1LdmE6", "team": "fldJrTNbBE3UVM49A", "gp": "fldCqDANlJuHQcIXb", "min": "fldUJGxVnVEWwboTw", "cvpm": "fldpReawzCcvBn8ue"}
AR = "tblIaoW02ZxDvi8Bs"
A = {"race": "fldZs5PkL48PwUWZS", "rank": "fldQN0JYWoQ5nawQv", "player": "fld3weqCyMJlPQfxG", "team": "fldlonmRxkDitOHly",
     "games": "fldc2EAt6R7icVSsn", "mpg": "fldWimT6MOnF6eWpG", "cvpm": "fldWuUUONRU4LG1C1", "asof": "fldMl3W2rzQ6RIgAb", "notes": "fldFUfIwBEasSqoYx"}
RACE = "Rookie of the Year"
TEAMS = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/teams"
UA = {"User-Agent": "Mozilla/5.0 (cvpm-pull roy)"}
CT = ZoneInfo("America/Chicago")


def norm(s):
    s = unicodedata.normalize("NFD", str(s or "")); s = "".join(c for c in s if not unicodedata.combining(c))
    s = "".join(c for c in s.lower() if c.isalpha() or c == " ")
    for suf in (" jr", " sr", " ii", " iii", " iv"):
        if s.endswith(suf): s = s[: -len(suf)]
    return " ".join(s.split())


def get(url):
    for i in range(4):
        try:
            r = requests.get(url, headers=UA, timeout=20)
            if r.ok: return r.json()
        except requests.RequestException:
            pass
        time.sleep(2 * (i + 1))
    return {}


def rookies():
    out = set()
    teams = ((get(TEAMS).get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams") or []
    for t in teams:
        tid = t["team"]["id"]
        for a in get(f"{TEAMS}/{tid}/roster").get("athletes", []):
            if (a.get("experience") or {}).get("years") == 0:
                out.add(norm(a.get("displayName")))
    return out


class Airtable:
    def __init__(self, token):
        self.s = requests.Session(); self.s.headers.update({"Authorization": f"Bearer {token}"})

    def _req(self, method, table, **kw):
        for i in range(5):
            r = self.s.request(method, f"https://api.airtable.com/v0/{BASE_ID}/{table}", timeout=30, **kw)
            if r.status_code == 429:
                time.sleep(2 * (i + 1)); continue
            r.raise_for_status(); return r.json()
        raise RuntimeError("rate limited")

    def all(self, table, fields, formula=None):
        out, off = [], None
        while True:
            p = {"pageSize": 100, "returnFieldsByFieldId": "true", "fields[]": fields}
            if formula: p["filterByFormula"] = formula
            if off: p["offset"] = off
            d = self._req("GET", table, params=p); out += d["records"]; off = d.get("offset")
            if not off: return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--dry-run", action="store_true"); a = ap.parse_args()
    if not TOKEN: sys.exit("AIRTABLE_TOKEN is not set")
    rk = rookies(); print(f"{len(rk)} rookies on ESPN rosters", flush=True)
    at = Airtable(TOKEN)
    lb = at.all(LB, list(L.values()))
    rows = []
    for r in lb:
        f = r["fields"]; gp = f.get(L["gp"]) or 0; mins = f.get(L["min"]) or 0; cv = f.get(L["cvpm"])
        if norm(f.get(L["name"])) not in rk or not gp or cv is None or isinstance(cv, dict): continue
        rows.append(dict(name=f.get(L["name"]), team=f.get(L["team"]), gp=gp, mpg=mins / gp, cv=cv))
    floor = 5 if any(r["gp"] >= 5 for r in rows) else 3
    q = sorted([r for r in rows if r["gp"] >= floor and r["mpg"] >= 12], key=lambda r: -r["cv"])[:10]
    for i, r in enumerate(q, 1): print(f"  {i}. {r['name']} {r['team']} {r['gp']}g {r['mpg']:.1f}mpg {round(r['cv']*1000)}")
    if a.dry_run: return
    today = datetime.now(CT).date().isoformat()
    have = {int(x["fields"].get(A["rank"]) or 0): x["id"] for x in at.all(AR, [A["race"], A["rank"]], f"{{Race}}='{RACE}'")}
    creates, updates = [], []
    for i in range(1, 11):
        r = q[i - 1] if i <= len(q) else None
        f = {A["race"]: RACE, A["rank"]: i, A["asof"]: today,
             A["player"]: r["name"] if r else None, A["team"]: r["team"] if r else None, A["games"]: r["gp"] if r else None,
             A["mpg"]: round(r["mpg"], 1) if r else None, A["cvpm"]: round(r["cv"] * 1000) if r else None,
             A["notes"]: (f"Rookies (ESPN experience 0), {floor}+ games and 12+ MPG, ranked by CVPM. Small samples." if i == 1 else (None if r else "Fewer than 10 rookies qualify."))}
        if i in have: updates.append({"id": have[i], "fields": f})
        else: creates.append({"fields": f})
    for i in range(0, len(creates), 10): at._req("POST", AR, json={"records": creates[i:i + 10], "typecast": True})
    for i in range(0, len(updates), 10): at._req("PATCH", AR, json={"records": updates[i:i + 10], "typecast": True})
    print(f"ROY race: {len(q)} ranked")


if __name__ == "__main__":
    main()
