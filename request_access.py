#!/usr/bin/env python3
"""Submit IUDX access requests for the EnvFlood resource groups.

OUTWARD ACTION: each request notifies the data provider (Pune / Chennai /
Kalyan-Dombivli smart-city SPVs) and asks them to grant this account a
consumer policy via the ACL-APD. Run deliberately, once.

    python3 request_access.py --list          # show groups + existing requests
    python3 request_access.py --submit        # actually submit requests

Auth chain (learned the hard way):
  identity token = AAA /token with itemId=rs.cos.iudx.org.in,
  itemType=resource_server, role=consumer; sent RAW (no "Bearer ") in the
  Authorization header — the APD's header pattern forbids spaces.
"""
import argparse
import json
import urllib.error
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CATALOGUE = "https://cos.iudx.org.in/iudx/cat/v1"
AAA = "https://authorization.iudx.org.in/auth/v1"
APD = "https://acl-apd.iudx.org.in/dx/apd/acl/v1"

ADDITIONAL_INFO = {
    "purpose": "research",
    "description": (
        "Building a longitudinal, non-commercial archive of urban flood-sensor "
        "readings for flood-risk research and civic analytics."
    ),
}


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
    """The EnvFlood resource groups: items whose own id is a resourceGroup for
    leaf sensors (leaf items carry `resourceGroup`; group items don't)."""
    leaves, groups, offset = {}, {}, 0
    while True:
        d = http_json(f"{CATALOGUE}/search?property=[type]&value=[[iudx:EnvFlood]]&limit=500&offset={offset}")
        rows = d.get("results", [])
        for r in rows:
            g = r.get("resourceGroup")
            if g:
                leaves.setdefault(g, []).append(r["id"])
            else:
                groups[r["id"]] = {"label": r.get("label"), "instance": r.get("instance"),
                                   "provider": r.get("provider")}
        if len(rows) < 500:
            break
        offset += 500
    for g, info in groups.items():
        info["n_leaves"] = len(leaves.get(g, []))
    # groups referenced by leaves but not returned by the search
    for g, ids in leaves.items():
        if g not in groups:
            groups[g] = {"label": "(group item not in EnvFlood search)",
                         "instance": None, "provider": None, "n_leaves": len(ids)}
    return groups


def representative_leaves():
    """One leaf resource id per group: {group_id: (leaf_id, label)}."""
    d = http_json(f"{CATALOGUE}/search?property=[type]&value=[[iudx:EnvFlood]]&limit=500&offset=0")
    rep = {}
    for r in d.get("results", []):
        g = r.get("resourceGroup")
        if g and g not in rep:
            rep[g] = (r["id"], r.get("label"))
    return rep


def identity_token(cid, csec):
    d = http_json(f"{AAA}/token",
                  data=json.dumps({"itemId": "rs.cos.iudx.org.in",
                                   "itemType": "resource_server",
                                   "role": "consumer"}).encode(),
                  headers={"clientId": cid, "clientSecret": csec,
                           "Content-Type": "application/json"})
    return d["results"]["accessToken"]


def existing_requests(tok):
    try:
        d = http_json(f"{APD}/policies/requests", headers={"Authorization": tok})
        return d.get("results", [])
    except urllib.error.HTTPError as e:
        if e.code == 404:  # "Access request not found" == none yet
            return []
        raise


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--submit", action="store_true")
    args = ap.parse_args()

    groups = flood_groups()
    print(f"{len(groups)} EnvFlood resource groups:")
    for g, info in groups.items():
        print(f"  {g} | {info['instance']} | {info['n_leaves']} sensors | {info['label']}")

    cid, csec = load_env()
    tok = identity_token(cid, csec)
    existing = existing_requests(tok)
    already = {r.get("itemId") for r in existing}
    if existing:
        print(f"\nexisting access requests: {len(existing)}")
        for r in existing:
            print(f"  {r.get('itemId')} -> {r.get('status')}")

    if not args.submit:
        print("\n(dry run — use --submit to send access requests)")
        return

    # This APD build rejects RESOURCE_GROUP ids ("it is group level resource"),
    # so request one representative LEAF resource per group — the provider can
    # still grant a group-wide policy when approving.
    reps = representative_leaves()
    for g, (rid, lbl) in reps.items():
        if rid in already:
            print(f"  {g}: already requested ({lbl}), skipping")
            continue
        body = json.dumps({"itemId": rid, "itemType": "RESOURCE",
                           "additionalInfo": ADDITIONAL_INFO}).encode()
        try:
            d = http_json(f"{APD}/policies/requests", data=body,
                          headers={"Authorization": tok,
                                   "Content-Type": "application/json"})
            print(f"  {g} via {lbl!r}: {d.get('title')}")
        except urllib.error.HTTPError as e:
            print(f"  {g}: HTTP {e.code} {e.read().decode()[:200]}")


if __name__ == "__main__":
    main()
