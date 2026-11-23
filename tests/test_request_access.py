import io
import json
import sys
import urllib.error

import pytest


@pytest.fixture
def ra(load, tmp_path, monkeypatch):
    m = load("request_access")
    monkeypatch.setattr(m, "BASE", tmp_path)
    return m


def http_error(code, body=b""):
    return urllib.error.HTTPError("http://x", code, "msg", {}, io.BytesIO(body))


def test_load_env(ra, tmp_path):
    with pytest.raises(FileNotFoundError):
        ra.load_env()
    (tmp_path / ".env").write_text("#c\nIUDX_CLIENT_ID= i \nIUDX_CLIENT_SECRET=s\n\n")
    assert ra.load_env() == ("i", "s")


class Resp:
    def __init__(self, body):
        self.b = body

    def read(self):
        return json.dumps(self.b).encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_http_json_verb_follows_the_body(ra, monkeypatch):
    seen = []
    monkeypatch.setattr(ra.urllib.request, "urlopen",
                        lambda req, timeout=None: seen.append((req.get_method(), dict(req.header_items()), timeout)) or Resp({"a": 1}))
    assert ra.http_json("http://x") == {"a": 1}
    assert seen[-1][0] == "GET" and seen[-1][2] == 30
    ra.http_json("http://x", data=b"{}", headers={"Authorization": "tok"})
    assert seen[-1][0] == "POST" and seen[-1][1]["Authorization"] == "tok"
    ra.http_json("http://x", data=b"{}", method="PATCH")
    assert seen[-1][0] == "PATCH"


def catalogue(monkeypatch, ra, pages):
    urls = []

    def fake(url, **kw):
        urls.append(url)
        return pages[len(urls) - 1]

    monkeypatch.setattr(ra, "http_json", fake)
    return urls


def test_flood_groups_separates_group_items_from_leaves(ra, monkeypatch):
    rows = [{"id": "G1", "label": "Pune group", "instance": "pune", "provider": "pmc"},
            {"id": "L1", "resourceGroup": "G1"}, {"id": "L2", "resourceGroup": "G1"},
            {"id": "L3", "resourceGroup": "G2"},                                       # group item absent from the search
            {"id": "G3", "label": "empty group"}]
    catalogue(monkeypatch, ra, [{"results": rows}])
    g = ra.flood_groups()
    assert g["G1"] == {"label": "Pune group", "instance": "pune", "provider": "pmc", "n_leaves": 2}
    assert g["G3"]["n_leaves"] == 0
    assert g["G2"] == {"label": "(group item not in EnvFlood search)", "instance": None,
                       "provider": None, "n_leaves": 1}
    assert set(g) == {"G1", "G2", "G3"}


def test_flood_groups_pages_in_steps_of_500_until_a_short_page(ra, monkeypatch):
    full = {"results": [{"id": f"L{i}", "resourceGroup": "G"} for i in range(500)]}
    last = {"results": [{"id": "L500", "resourceGroup": "G"}]}
    urls = catalogue(monkeypatch, ra, [full, last])
    assert ra.flood_groups()["G"]["n_leaves"] == 501
    assert "offset=0" in urls[0] and "offset=500" in urls[1] and len(urls) == 2


def test_representative_leaves_takes_the_first_leaf_per_group(ra, monkeypatch):
    rows = [{"id": "G1"}, {"id": "a", "resourceGroup": "G1", "label": "first"},
            {"id": "b", "resourceGroup": "G1", "label": "second"}, {"id": "c", "resourceGroup": "G2", "label": "c!"}]
    catalogue(monkeypatch, ra, [{"results": rows}])
    assert ra.representative_leaves() == {"G1": ("a", "first"), "G2": ("c", "c!")}


def test_identity_token_request(ra, monkeypatch):
    seen = {}

    def fake(url, data=None, headers=None, **kw):
        seen.update(url=url, data=json.loads(data), headers=headers)
        return {"results": {"accessToken": "RAWTOKEN"}}

    monkeypatch.setattr(ra, "http_json", fake)
    assert ra.identity_token("cid", "sec") == "RAWTOKEN"
    assert seen["url"] == f"{ra.AAA}/token"
    assert seen["data"] == {"itemId": "rs.cos.iudx.org.in", "itemType": "resource_server", "role": "consumer"}
    assert seen["headers"]["clientId"] == "cid" and seen["headers"]["clientSecret"] == "sec"


def test_existing_requests(ra, monkeypatch):
    seen = {}

    def ok(url, headers=None, **kw):
        seen.update(url=url, headers=headers)
        return {"results": [{"itemId": "a"}]}

    monkeypatch.setattr(ra, "http_json", ok)
    assert ra.existing_requests("tok") == [{"itemId": "a"}]
    assert seen["url"] == f"{ra.APD}/policies/requests" and seen["headers"] == {"Authorization": "tok"}   # raw, no 'Bearer '
    monkeypatch.setattr(ra, "http_json", lambda *a, **k: {})
    assert ra.existing_requests("tok") == []

    def notfound(*a, **k):
        raise http_error(404)

    monkeypatch.setattr(ra, "http_json", notfound)
    assert ra.existing_requests("tok") == []                       # "not found" means none yet

    def broken(*a, **k):
        raise http_error(500)

    monkeypatch.setattr(ra, "http_json", broken)
    with pytest.raises(urllib.error.HTTPError):
        ra.existing_requests("tok")


def wire_main(ra, monkeypatch, tmp_path, existing=(), post_effect=None):
    (tmp_path / ".env").write_text("IUDX_CLIENT_ID=c\nIUDX_CLIENT_SECRET=s\n")
    monkeypatch.setattr(ra, "flood_groups", lambda: {"G1": {"label": "L", "instance": "pune", "n_leaves": 3},
                                                     "G2": {"label": "M", "instance": "chennai", "n_leaves": 1}})
    monkeypatch.setattr(ra, "identity_token", lambda c, s: "TOK")
    monkeypatch.setattr(ra, "existing_requests", lambda tok: list(existing))
    monkeypatch.setattr(ra, "representative_leaves", lambda: {"G1": ("leaf1", "Leaf One"), "G2": ("leaf2", "Leaf Two")})
    posts = []

    def http_json(url, data=None, headers=None, **kw):
        posts.append((url, json.loads(data), headers))
        if post_effect:
            return post_effect(posts[-1])
        return {"title": "Access request created"}

    monkeypatch.setattr(ra, "http_json", http_json)
    return posts


def run(ra, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["request_access.py", *argv])
    ra.main()


def test_default_run_is_a_dry_run(ra, monkeypatch, tmp_path, capsys):
    posts = wire_main(ra, monkeypatch, tmp_path, existing=[{"itemId": "leaf1", "status": "pending"}])
    run(ra, monkeypatch)
    out = capsys.readouterr().out
    assert "2 EnvFlood resource groups:" in out
    assert "G1 | pune | 3 sensors | L" in out
    assert "existing access requests: 1" in out and "leaf1 -> pending" in out
    assert "(dry run — use --submit to send access requests)" in out
    assert posts == []                                             # nothing is sent without --submit


def test_list_flag_is_also_non_destructive(ra, monkeypatch, tmp_path, capsys):
    posts = wire_main(ra, monkeypatch, tmp_path)
    run(ra, monkeypatch, "--list")
    assert posts == [] and "dry run" in capsys.readouterr().out


def test_submit_sends_one_request_per_group_and_skips_existing(ra, monkeypatch, tmp_path, capsys):
    posts = wire_main(ra, monkeypatch, tmp_path, existing=[{"itemId": "leaf1", "status": "pending"}])
    run(ra, monkeypatch, "--submit")
    out = capsys.readouterr().out
    assert "G1: already requested (Leaf One), skipping" in out
    assert len(posts) == 1
    url, body, headers = posts[0]
    assert url == f"{ra.APD}/policies/requests"
    assert body == {"itemId": "leaf2", "itemType": "RESOURCE", "additionalInfo": ra.ADDITIONAL_INFO}
    assert headers == {"Authorization": "TOK", "Content-Type": "application/json"}
    assert "G2 via 'Leaf Two': Access request created" in out
    assert "dry run" not in out


def test_submit_reports_http_failures_and_continues(ra, monkeypatch, tmp_path, capsys):
    def effect(call):
        if call[1]["itemId"] == "leaf1":
            raise http_error(409, b"x" * 300)
        return {"title": "created"}

    posts = wire_main(ra, monkeypatch, tmp_path, post_effect=effect)
    run(ra, monkeypatch, "--submit")
    out = capsys.readouterr().out
    assert len(posts) == 2
    assert f"G1: HTTP 409 {'x' * 200}" in out and "x" * 201 not in out        # body truncated to 200 chars
    assert "G2 via 'Leaf Two': created" in out
