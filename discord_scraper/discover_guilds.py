#!/usr/bin/env python3
"""List guilds the configured account is already a member of, with channel inventories.

Read-only, slow (1 request / 2.5s + jitter). Writes data/discovery/guilds.json.
Requires DISCORD_TOKEN. Never prints the token.
"""
import json
import os
import random
import sys
import time
from pathlib import Path

import requests

OUT = Path(__file__).parent / "data" / "discovery"
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}


def get(path: str, token: str):
    time.sleep(2.5 + random.random() * 1.5)
    for attempt in range(4):
        r = requests.get(f"https://discord.com/api/v9{path}", headers={**HEADERS, "Authorization": token}, timeout=30)
        if r.status_code == 429:
            time.sleep(float(r.headers.get("Retry-After", 5)) * (attempt + 1) + 1)
            continue
        return r
    return r


def main() -> int:
    token = os.environ.get("DISCORD_TOKEN")
    if not token:
        print("DISCORD_TOKEN not set", file=sys.stderr)
        return 1
    OUT.mkdir(parents=True, exist_ok=True)
    r = get("/users/@me/guilds?with_counts=true", token)
    print("guilds status", r.status_code)
    if r.status_code != 200:
        print(r.text[:200])
        return 2
    guilds = r.json()
    result = []
    for g in guilds:
        entry = {"id": g["id"], "name": g["name"], "members": g.get("approximate_member_count"), "channels": []}
        cr = get(f"/guilds/{g['id']}/channels", token)
        if cr.status_code == 200:
            entry["channels"] = [
                {"id": c["id"], "name": c.get("name"), "type": c.get("type"), "parent_id": c.get("parent_id")}
                for c in cr.json()
            ]
        else:
            entry["channels_error"] = cr.status_code
        result.append(entry)
        print(f"{g['name']!r:50} members={entry['members']} channels={len(entry['channels'])}", flush=True)
    (OUT / "guilds.json").write_text(json.dumps(result, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
