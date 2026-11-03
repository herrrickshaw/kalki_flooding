from __future__ import annotations

import request_access as ra

PAGE = {"results": [
    {"id": "G1", "label": "Group", "instance": "pune", "provider": "p"},
    {"id": "L1", "label": "Leaf 1", "resourceGroup": "G1"},
    {"id": "L2", "label": "Leaf 2", "resourceGroup": "G1"},
    {"id": "L3", "label": "Orphan leaf", "resourceGroup": "G2"},
]}


def test_flood_groups_counts_leaves_and_orphans(monkeypatch):
    monkeypatch.setattr(ra, "http_json", lambda url, **k: PAGE)
    groups = ra.flood_groups()
    assert groups["G1"]["n_leaves"] == 2
    assert groups["G2"]["n_leaves"] == 1 and groups["G2"]["instance"] is None


def test_representative_leaves_first_per_group(monkeypatch):
    monkeypatch.setattr(ra, "http_json", lambda url, **k: PAGE)
    assert ra.representative_leaves() == {"G1": ("L1", "Leaf 1"), "G2": ("L3", "Orphan leaf")}
