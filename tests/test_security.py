"""الأمان ومتانة المدخلات: كل الـAPI محتاج دخول، حماية Host/Origin، open redirect، مسارات ملفات، ومدخلات غلط ← 400 مش 500."""
import re

import pytest


def _concrete(rule):
    """/api/runs/<run_id>/file/<key> -> /api/runs/abc123/file/xlsx"""
    return re.sub(r"<(?:\w+:)?(\w+)>", lambda m: {"key": "xlsx"}.get(m.group(1), "abc123"), rule)


def test_every_api_endpoint_requires_login(app):
    anon = app.test_client()
    checked = 0
    for rule in app.url_map.iter_rules():
        if not str(rule).startswith("/api/"):
            continue
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            r = anon.open(_concrete(str(rule)), method=method, json={})
            assert r.status_code == 401, f"{method} {rule} -> {r.status_code}"
            checked += 1
    assert checked > 60


def test_pages_redirect_to_login_when_anonymous(app, client):
    anon = app.test_client()
    for path in ["/", "/runs/new", "/centers", "/review", "/compare", "/planning", "/settings"]:
        r = anon.get(path)
        assert r.status_code == 302 and "/login" in r.headers["Location"], path
    assert anon.get("/health").status_code == 200


def test_setup_is_only_possible_once(app, client):
    anon = app.test_client()
    r = anon.post("/setup", data={"username": "evil", "password": "Evil12345", "password2": "Evil12345"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    r = anon.post("/login", data={"username": "evil", "password": "Evil12345"})
    assert r.status_code == 401


def test_login_wrong_password_and_open_redirect(app, client):
    anon = app.test_client()
    assert anon.post("/login", data={"username": "admin", "password": "wrong"}).status_code == 401
    for nxt, ok in [("https://evil.example/x", False), ("//evil.example", False), ("/\\evil.example", False),
                    ("javascript:alert(1)", False), ("/centers", True)]:
        anon = app.test_client()
        r = anon.post(f"/login?next={nxt}", data={"username": "admin", "password": "Test12345"})
        assert r.status_code == 302
        loc = r.headers["Location"]
        assert (loc.endswith("/centers")) if ok else ("evil" not in loc and "javascript" not in loc), (nxt, loc)


def test_host_and_origin_guards(client):
    assert client.get("/api/status", headers={"Host": "evil.example:5000"}).status_code == 403
    assert client.post("/api/settings", json={}, headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/settings", json={}, headers={"Origin": "http://localhost"}).status_code == 200


def test_settings_only_known_keys_are_saved(client):
    j = client.post("/api/settings", json={"max_km": "40", "evil": "x"}).get_json()
    assert j["settings"]["max_km"] == "40" and "evil" not in j["settings"]
    client.post("/api/settings", json={"max_km": "50"})


def test_password_policy(client):
    assert client.post("/api/password", json={"old": "Test12345", "new": "short"}).status_code == 400
    assert client.post("/api/password", json={"old": "wrong-old", "new": "LongEnough1"}).status_code == 400


@pytest.mark.parametrize("path", [
    "/api/runs/..%2F..%2Fsecret/result", "/api/runs/abc/file/../../app.db", "/api/runs/abc123/file/secret",
    "/api/review?run=../../x",
])
def test_no_path_traversal(client, path):
    r = client.get(path)
    assert r.status_code in (200, 400, 404, 405)
    assert b"SQLite" not in r.data and b"secret.key" not in r.data
    if path.startswith("/api/review"):
        assert r.get_json()["items"] == []


@pytest.mark.parametrize("path", [
    "/api/runs/abc123/students/invalid_nids?page=abc&per_page=-5",
    "/api/runs/abc123/students/band2?page=x",
    "/api/runs/abc123/students/far?per_page=zz",
    "/api/runs/abc123/students/far_points?limit=xx",
    "/api/runs/abc123/students/suspects?page=-1",
    "/api/review?min_n=abc&limit=zz",
])
def test_bad_numeric_query_params_are_not_server_errors(client, path):
    assert client.get(path).status_code < 500


@pytest.mark.parametrize("params,msg", [
    ({"max_km": "abc"}, "max_km"), ({"max_km": -5}, "max_km"), ({"max_km": 0}, "max_km"), ({"detour": 10}, "detour"),
    ({"road_mode": "teleport"}, "osrm"), ({"geo_mode": "magic"}, "auto"),
])
def test_run_params_are_validated(client, params, msg):
    r = client.post("/api/runs", json={"token": "abc123", "params": params})
    assert r.status_code == 400 and msg in r.get_json()["error"]


def test_bad_draft_token(client):
    for tok in ["../x", "nope123"]:
        r = client.post("/api/runs", json={"token": tok, "params": {"road_mode": "factor"}})
        assert r.status_code == 400
    assert client.post("/api/runs", json={}).status_code == 400
    assert client.post("/api/runs", data="notjson", content_type="text/plain").status_code == 400


def test_whatif_rejects_bad_input(client):
    for body in [{"center": "X", "level": "L1", "capacity": -5}, {"center": "X", "level": "L1", "capacity": "abc"},
                 {"center": "X", "level": "L1", "capacity": 10, "groups": 0}]:
        assert client.post("/api/runs/abc123/whatif", json=body).status_code == 400


def test_upload_validation(client):
    import io
    assert client.post("/api/runs/upload", data={}, content_type="multipart/form-data").status_code == 400
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(b"x"), "s.csv")}, content_type="multipart/form-data")
    assert r.status_code == 400                                          # source=sheet من غير شيت مراكز
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(b"\x00garbage"), "s.xlsx"),
                                              "centers": (io.BytesIO(b"\x00garbage"), "c.xlsx")},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "error" in r.get_json()


def test_student_override_requires_valid_nid(client):
    assert client.post("/api/review/student", json={"nid": "123", "input": "30.0,31.0"}).status_code == 400
    assert client.post("/api/review/student", json={"nid": "30000000000099", "input": "not coords"}).status_code == 400
    r = client.post("/api/review/student", json={"nid": "30000000000099", "input": "30.0444, 31.2357"})
    assert r.status_code == 200 and r.get_json()["lat"] == 30.0444


def test_audit_log_masks_national_id(client):
    from distributor import db
    client.post("/api/review/student", json={"nid": "30000000000098", "input": "30.0444, 31.2357"})
    con = db.connect()
    try:
        rows = [r["target"] for r in con.execute("SELECT target FROM audit WHERE action='student_override'")]
    finally:
        con.close()
    assert rows and all("30000000000098" not in t for t in rows)
