#!/usr/bin/env python3
"""
Today's referee crews from official.nba.com -> Games "Referee Crew" field.

Fills a game's Referee Crew when it is blank or still a placeholder ("not posted yet" etc.).
Format: "Crew chief X (#n), referee Y (#n), umpire Z (#n)" - the same format the runs use.

Env:   AIRTABLE_TOKEN (required), AIRTABLE_BASE_ID (optional)
Usage: python refs_today.py [--dry-run]
"""
import argparse
import os
import re
import sys
import time
from datetime import datetime
from html import unescape
from zoneinfo import ZoneInfo

import requests

BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appR3Vx7vTrll630D")
TOKEN = os.environ.get("AIRTABLE_TOKEN")
GAMES = "tblOJ5dyqjOTj87XZ"
GAME_F, CREW_F = "fldTEAyw4LUdjx8oF", "fldmveWvEJvgm7peX"
URL = "https://official.nba.com/referee-assignments/"
CITY = {
    "Atlanta": "ATL", "Boston": "BOS", "Brooklyn": "BKN", "Charlotte": "CHA", "Chicago": "CHI", "Cleveland": "CLE",
    "Dallas": "DAL", "Denver": "DEN", "Detroit": "DET", "Golden State": "GSW", "Houston": "HOU", "Indiana": "IND",
    "LA Clippers": "LAC", "L.A. Clippers": "LAC", "L.A. Lakers": "LAL", "LA Lakers": "LAL", "Memphis": "MEM",
    "Miami": "MIA", "Milwaukee": "MIL", "Minnesota": "MIN", "New Orleans": "NOP", "New York": "NYK",
    "Oklahoma City": "OKC", "Orlando": "ORL", "Philadelphia": "PHI", "Phoenix": "PHX", "Portland": "POR",
    "Sacramento": "SAC", "San Antonio": "SAS", "Toronto": "TOR", "Utah": "UTA", "Washington": "WSH",
}
PLACEHOLDER = re.compile(r"not posted|not yet|pending|tbd|unavailable", re.I)


def crews():
    r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    s = r.text
    i = s.find("Crew Chief")
    if i < 0:
        return []
    j = s.find("</table>", i)  # first table on the page = NBA games
    parts = [unescape(p).strip() for p in re.sub(r"<[^>]+>", "|", s[i:j if j > 0 else i + 15000]).split("|")]
    parts = [p for p in parts if p]
    out, cur = [], None
    for p in parts[parts.index("Alternate") + 1:]:
        if " @ " in p:
            if cur:
                out.append(cur)
            cur = [p]
        elif cur is not None and re.search(r"\(#\d+\)", p):
            cur.append(p)
    if cur:
        out.append(cur)
    return [c for c in out if len(c) >= 4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    day = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    try:
        found = crews()
    except requests.RequestException as e:
        print("official.nba.com not reachable:", e)
        return
    want = {}
    for game, chief, ref, ump, *_ in found:
        away, home = [g.strip() for g in game.split(" @ ")]
        if away not in CITY or home not in CITY:
            print("unknown team name:", game)
            continue
        want[f"{day} {CITY[away]} @ {CITY[home]}"] = f"Crew chief {chief}, referee {ref}, umpire {ump}"
    print(f"{len(want)} crews posted for {day}")
    if not want or args.dry_run:
        for k, v in want.items():
            print(" ", k, "|", v)
        return
    if not TOKEN:
        sys.exit("AIRTABLE_TOKEN is not set")
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {TOKEN}"})
    api = f"https://api.airtable.com/v0/{BASE_ID}/{GAMES}"
    formula = "OR(" + ",".join(f"{{Game}}='{n}'" for n in want) + ")"
    recs = s.get(api, params={"filterByFormula": formula, "returnFieldsByFieldId": "true",
                              "fields[]": [GAME_F, CREW_F]}, timeout=30).json().get("records", [])
    updates = []
    for rec in recs:
        f = rec["fields"]
        cur = f.get(CREW_F) or ""
        if cur and not PLACEHOLDER.search(cur):
            continue
        updates.append({"id": rec["id"], "fields": {CREW_F: want[f[GAME_F]]}})
    for i in range(0, len(updates), 10):
        r = s.patch(api, json={"records": updates[i:i + 10]}, timeout=30)
        r.raise_for_status()
        time.sleep(0.25)
    print(f"filled {len(updates)} crews")


if __name__ == "__main__":
    main()
