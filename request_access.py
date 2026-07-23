#!/usr/bin/env python3
"""Submit IUDX access requests for the EnvFlood resource groups.

OUTWARD ACTION: each request notifies the data provider (Pune / Chennai /
Kalyan-Dombivli smart-city SPVs) and asks them to grant this account a
consumer policy via the ACL-APD. Run deliberately, once.

    python3 request_access.py --list          # show groups that would be requested
    python3 request_access.py --submit        # actually submit requests
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CATALOGUE = "https://cos.iudx.org.in/iudx/cat/v1"
AAA = "https://authorization.iudx.org.in/auth/v1"
APD = "https://acl-apd.iudx.org.in/dx/apd/acl/v1"


def load_env():
    env = {}
    for line in (BASE / ".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env["IUDX_CLIENT_ID"], env["IUDX_CLIENT_SECRET"]


def http_json(url, headers=None, data=None, method=None):
    req = urllib.request.Request(url, data=data, method=method or ("POST" if data else "GET"),
                                 headers=headers or {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def flood_groups():
    """Distinct (resourceGroup, provider, sample label) for EnvFlood."""
    out, offset = {}, 0
    while True:
        d = http_json(f"{CATALOGUE}/search?property=[type]&value=[[iudx:EnvFlood]]&limit=500&offset={offset}")
        rows = d.get("results", [])
        for r in rows:
            g = r.get("resourceGroup")
            if g not in out:
                out[g] = {"provider": r.get("provider"), "sample": r.get("label"),
                          "instance": r.get("instance"), "resource_ids": []}
            out[g]["resource_ids"].append(r["id"])
        if len(rows) < 500:
            break
        offset += 500
    return out


def identity_token(cid, csec):
    """APD calls authenticate with an AAA identity token for the APD itself."""
    body = json.dumps({"itemId": "acl-apd.iudx.org.in", "itemType": "resource_server",
                       "role": "consumer"}).encode()
    d = http_json(f"{AAA}/token", data=body,
                  headers={"clientId": cid, "clientSecret": csec,
                           "Content-Type": "application/json"})
    return d["results"]["accessToken"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    groups = flood_groups()
    print(f"{len(groups)} EnvFlood resource groups:")
    for g, info in groups.items():
        print(f"  group {g} | instance={info['instance']} | provider={info['provider']}")
        print(f"    e.g. {info['sample']} | {len(info['resource_ids'])} resources")

    if not args.submit:
        print("\n(dry run — use --submit to send access requests)")
        return

    cid, csec = load_env()
    tok = identity_token(cid, csec)
    for g, info in groups.items():
        # request at resource level: one representative id per group is enough
        # for the provider to grant a group policy; APD accepts itemId+itemType.
        body = json.dumps({"itemId": info["resource_ids"][0], "itemType": "RESOURCE"}).encode()
        try:
            d = http_json(f"{APD}/policies/requests", data=body,
                          headers={"Authorization": f"Bearer {tok}",
                                   "Content-Type": "application/json"})
            print(f"  {g}: submitted -> {d.get('title')}")
        except urllib.error.HTTPError as e:
            print(f"  {g}: HTTP {e.code} {e.read().decode()[:200]}")


if __name__ == "__main__":
    main()
