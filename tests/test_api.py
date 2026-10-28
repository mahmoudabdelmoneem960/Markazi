"""اختبار الواجهة كاملة على الشيتات التجريبية (تقدير مسافة، من غير OSRM)."""
import io
import time

import pytest

from tests.conftest import HAS_GAZ


def _wait(client, jid, timeout=240):
    t = time.time()
    while time.time() - t < timeout:
        j = client.get("/api/jobs/" + jid).get_json()
        if j["state"] in ("done", "error", "cancelled"):
            return j
        time.sleep(1)
    raise AssertionError("job timeout")


def test_auth_required(app):
    c = app.test_client()
    assert c.get("/api/status").status_code == 401
    r = c.get("/")
    assert r.status_code == 302


def test_csrf_origin_guard(client):
    r = client.post("/api/settings", json={"max_km": "50"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_status_and_settings(client):
    r = client.get("/api/status")
    assert r.status_code == 200 and "locations" in r.get_json()
    r = client.post("/api/settings", json={"max_km": "40", "road_mode": "factor"})
    assert r.status_code == 200 and r.get_json()["settings"]["max_km"] == "40"


def test_pages_render(client):
    for p in ("/", "/runs/new", "/centers", "/review", "/compare", "/settings"):
        r = client.get(p)
        assert r.status_code == 200 and b"<html" in r.data, p


@pytest.mark.skipif(not HAS_GAZ, reason="قاعدة الأماكن مش موجودة")
def test_full_run_flow(client, sample_files):
    S, C = sample_files
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(S), "students_sample.xlsx"), "centers": (io.BytesIO(C), "centers_sample.csv")},
                    content_type="multipart/form-data")
    pf = r.get_json()
    assert r.status_code == 200 and pf["ok"] and pf["students"]["rows"] == 600 and pf["centers"]["centers"] == 12
    assert pf["centers"]["no_coords_with_link"] == 3          # 3 مراكز من غير إحداثيات بس ليهم لينك
    r = client.post("/api/runs", json={"token": pf["token"], "name": "t", "params": {"geo_mode": "addr", "road_mode": "factor", "max_km": "50", "detour": "1.3"}})
    assert r.status_code == 200, r.get_json()
    run_id, jid = r.get_json()["run_id"], r.get_json()["job_id"]
    j = _wait(client, jid)
    assert j["state"] == "done", j.get("error")
    res = client.get(f"/api/runs/{run_id}/result").get_json()
    s = res["summary"]
    assert s["students"] == 600 and s["assigned"] + s["vp"] == 600 and s["assigned"] > 200
    assert s["geo"]["centers"]["with_coords"] == 12            # الـ3 الناقصين اتحددوا من اللينك
    meta = client.get(f"/api/runs/{run_id}").get_json()["run"]
    assert meta["status"] == "done" and meta["files"]["xlsx"] and meta["files"]["students"]
    for key in ("xlsx", "qgis", "per_center"):
        r = client.get(f"/api/runs/{run_id}/file/{key}")
        assert r.status_code == 200 and len(r.data) > 2000, key
    cen = client.get(f"/api/runs/{run_id}/centers").get_json()
    assert len(cen) == 12 and all("occupancy" in c for c in cen)
    pts = client.get(f"/api/runs/{run_id}/students?gov=cairo").get_json()
    assert pts["total"] > 100 and pts["points"][0]["lat"] > 20
    w = client.post(f"/api/runs/{run_id}/whatif", json={"center": "SMP_CAI_C01", "level": "L1", "capacity": 25, "groups": 1}).get_json()
    assert w["seats"] == 25 and 0 <= w["rescued"] <= 25
    rv = client.get(f"/api/review?run={run_id}&min_n=1&limit=10").get_json()
    assert rv["run"] == run_id and rv["groups_total"] >= 0
    # نفس الشيت مرة تانية (الكاش) + مقارنة
    r = client.post("/api/runs/upload", data={"students": (io.BytesIO(S), "students_sample.xlsx"), "centers": (io.BytesIO(C), "centers_sample.csv")},
                    content_type="multipart/form-data")
    r = client.post("/api/runs", json={"token": r.get_json()["token"], "params": {"geo_mode": "addr", "road_mode": "factor", "max_km": "50", "detour": "1.3"}})
    run_b, jid_b = r.get_json()["run_id"], r.get_json()["job_id"]
    assert _wait(client, jid_b)["state"] == "done"
    cmp = client.get(f"/api/compare?a={run_id}&b={run_b}").get_json()
    assert cmp["pair"]["common"] == 600 and cmp["pair"]["same_outcome"] == 600     # نفس المدخلات = نفس النتيجة بالظبط
    assert client.get(f"/api/compare.xlsx?a={run_id}&b={run_b}").status_code == 200
    assert client.delete(f"/api/runs/{run_b}").status_code == 200
    assert client.get(f"/api/runs/{run_b}").status_code == 404


def test_centers_edit_and_review_overrides(client, sample_files):
    cs = client.get("/api/centers").get_json()
    if not cs:
        _, C = sample_files
        client.post("/api/centers/import", data={"file": (io.BytesIO(C), "centers_sample.csv")},
                    content_type="multipart/form-data")
        cs = client.get("/api/centers").get_json()
    assert len(cs) >= 12
    code = cs[0]["code"]
    r = client.post(f"/api/centers/{code}", json={"input": "30.0444, 31.2357"})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert client.post(f"/api/centers/{code}", json={"input": "abc"}).status_code == 400
    assert client.post("/api/centers/NOPE_X", json={"input": "30.0444, 31.2357"}).status_code == 400
    r = client.post("/api/review/student", json={"nid": "31002281400186", "input": "https://www.google.com/maps?q=30.05,31.24"})
    assert r.status_code == 200 and r.get_json()["lat"] == 30.05
    assert client.post("/api/review/student", json={"nid": "12", "input": "30,31"}).status_code == 400
    r = client.post("/api/review/group", json={"gkey": "cairo|x|y", "gov": "cairo", "area": "x", "text": "y", "input": "30.1, 31.2"})
    assert r.status_code == 200
    assert client.post("/api/review/group/delete", json={"gkey": "cairo|x|y"}).status_code == 200
    r = client.get("/api/centers.csv")
    assert r.status_code == 200 and b"code,name" in r.data


def test_google_without_key(client):
    assert client.get("/api/google/status").get_json()["key"] is False
    r = client.post("/api/google/verify", json={"scope": "all", "run": True})
    assert r.status_code == 400 and "google_key.txt" in r.get_json()["error"]
    r = client.post("/api/google/verify", data="x", content_type="text/plain")
    assert r.status_code == 415


def test_password_change(client):
    assert client.post("/api/password", json={"old": "wrong", "new": "Another123"}).status_code == 400
    assert client.post("/api/password", json={"old": "Test12345", "new": "Another123"}).status_code == 200
    assert client.post("/api/password", json={"old": "Another123", "new": "Test12345"}).status_code == 200


def test_center_suggestion_and_resolution(client):
    from distributor import db
    con = db.connect()
    try:
        con.execute("INSERT OR REPLACE INTO centers(code, name, gov, area, address, lat, lon) VALUES ('TST_01', 'Test Center', 'cairo', 'heliopolis', 'شارع التحرير', 30.05, 31.25)")
        con.commit()
    finally:
        con.close()
    
    code = "TST_01"
    # Suggest coords
    r = client.get(f"/api/centers/{code}/suggest_coords")
    assert r.status_code == 200 and "center" in r.get_json()
    # Verify current
    r = client.post(f"/api/centers/{code}/verify_current")
    assert r.status_code == 200 and r.get_json()["ok"]
    # Apply coords
    r = client.post(f"/api/centers/{code}/apply_coords", json={"lat": 30.12345, "lon": 31.54321, "source": "manual", "clear_flags": True})
    assert r.status_code == 200 and r.get_json()["ok"]
    assert r.get_json()["lat"] == 30.12345


def test_far_students_endpoints(client):
    from distributor.services import runs
    # Check if any run exists or create dummy run folder
    active_runs = runs.list_runs()
    if active_runs:
        rid = active_runs[0]["id"]
        r = client.get(f"/api/runs/{rid}/students/far?filter_type=gov")
        assert r.status_code == 200
        data = r.get_json()
        assert "rows" in data and "total" in data

        r_pts = client.get(f"/api/runs/{rid}/students/far_points?filter_type=gov")
        assert r_pts.status_code == 200
        pts_data = r_pts.get_json()
        assert "points" in pts_data and "centers" in pts_data



