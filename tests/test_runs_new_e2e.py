"""صفحة «تشغيل جديد» (/runs/new) من أولها لآخرها بنفس الـAPI اللي الصفحة بتستخدمه، على 10 طلاب نتيجتهم معروفة مسبقًا.

كل طالب بيختبر قاعدة (المرجع محسوب يدويًا ومتأكد منه بتطبيق مستقل للقواعد — شوف تقرير الاختبار):
  1 نفس المنطقة + الأقرب            2 نفس المنطقة قبل الأقرب (المعادي بدل مدينة نصر)
  3 «محافظة القاهرة» + «حي مدينة نصر»  4 «Cairo»: مركزه اتملى → التالي (التجمع، موقعه من لينك Google)
  5 تراك WB                           6 L3CY مش موجود في محافظته → VP
  7 عادي في الإسكندرية                8 أبعد من 50 كم → VP بأقرب مركز ومسافته
  9 الليفيل مكتوب ككود «L2»            10 الجيزة (البند التاني) → VP + تنبيه البنود
"""
import gzip
import io
import time

import pandas as pd
import pytest

from distributor import config
from distributor.core import locdb

from conftest import HAS_GAZ

CENTERS = [
    ("L1_0001", "E2E_CAI_C01", "مدينة نصر", "cairo", "Friday", 2, 30.0561, 31.3301, ""),
    ("L3WB_0002", "E2E_CAI_C01", "مدينة نصر", "cairo", "Saturday", 1, None, None, ""),
    ("L1_0003", "E2E_CAI_C02", "المعادي", "cairo", "Friday", 1, 29.9602, 31.2569, ""),
    ("L2_0004", "E2E_CAI_C02", "المعادي", "cairo", "Saturday", 2, None, None, ""),
    ("L1_0005", "E2E_CAI_C03", "التجمع الخامس", "cairo", "Friday", 1, None, None,
     "https://www.google.com/maps/place/x/@30.0074,31.4913,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2!3d30.0074!4d31.4913"),
    ("L1_0006", "E2E_ALX_C01", "سموحة", "alexandria", "Friday", 1, 31.2156, 29.9553, ""),
    ("L3CY_0007", "E2E_ALX_C01", "سموحة", "alexandria", "Saturday", 1, None, None, ""),
    ("L2_0008", "E2E_DKH_C01", "المنصورة", "dakahlia", "Friday", 1, 31.0409, 31.3785, ""),
    ("XX_BAD", "E2E_DKH_C01", "المنصورة", "dakahlia", "Friday", 5, None, None, ""),
]
STUDENTS = [
    ("30101010100011", "القاهرة", "مدينة نصر", "شارع عباس العقاد - مدينة نصر", "Level (1)", 30.0565, 31.3305),
    ("30101010100022", "القاهرة", "المعادي", "شارع مكرم عبيد - مدينة نصر", "Level (1)", 30.0600, 31.3400),
    ("30101010100033", "محافظة القاهرة", "حي مدينة نصر", "شارع الطيران - مدينة نصر", "Level (1)", 30.0580, 31.3250),
    ("30101010100044", "Cairo", "مدينه نصر", "شارع مصطفى النحاس - مدينة نصر", "Level (1)", 30.0620, 31.3450),
    ("30101010100055", "القاهرة", "مدينة نصر", "شارع عباس العقاد - مدينة نصر", "Level (3): Web", 30.0565, 31.3305),
    ("30101010100066", "القاهرة", "مدينة نصر", "شارع عباس العقاد - مدينة نصر", "Level (3): Cyber", 30.0565, 31.3305),
    ("30101010100077", "الإسكندرية", "سموحة", "شارع فوزي معاذ - سموحة", "Level (1)", 31.2100, 29.9500),
    ("30101010100088", "الإسكندرية", "برج العرب", "برج العرب الجديدة", "Level (1)", 30.8500, 29.6000),
    ("30101010100099", "الدقهلية", "المنصورة", "شارع الجمهورية - المنصورة", "L2", 31.0400, 31.3800),
    ("30101010100100", "الجيزة", "الدقي", "شارع التحرير - الدقي", "Level (1)", 30.0380, 31.2110),
]
# (status, center, group, distance_km, same_area) أو ("VP", note-fragment, nearest_center, distance)
EXPECTED = [
    ("تم التوزيع", "E2E_CAI_C01", "L1_0001", 0.1, "نعم"),
    ("تم التوزيع", "E2E_CAI_C02", "L1_0003", 17.8, "نعم"),
    ("تم التوزيع", "E2E_CAI_C01", "L1_0001", 0.7, "نعم"),
    ("تم التوزيع", "E2E_CAI_C03", "L1_0005", 19.9, "لا"),
    ("تم التوزيع", "E2E_CAI_C01", "L3WB_0002", 0.1, "نعم"),
    ("VP", "لا يوجد في محافظته أي مركز به L3CY", "", None),
    ("تم التوزيع", "E2E_ALX_C01", "L1_0006", 1.0, "نعم"),
    ("VP", "أبعد من 50 كم", "E2E_ALX_C01", 68.8),
    ("تم التوزيع", "E2E_DKH_C01", "L2_0008", 0.2, "نعم"),
    ("VP", "لا يوجد في محافظته أي مركز به L1", "", None),
]


def _files():
    C = pd.DataFrame([{"Group Code": g, "Center Code": c, "Area": a, "City": ci, "Day": d, "Time": "10:00", "Center Name": c,
                       "سعة القاعة": cap, "Latitude": la, "Longitude": lo, "العنوان التفصيلي للمركز": f"{a} - شارع 1",
                       "لوكيشن المكان": lk} for g, c, a, ci, d, cap, la, lo, lk in CENTERS])
    S = pd.DataFrame([{"Student NID": n, "gov": g, "Area": a, "Adress": ad, "Level(25-26)": lv, "Latitude": la, "Longitude": lo}
                      for n, g, a, ad, lv, la, lo in STUDENTS])
    sb = io.BytesIO()
    S.to_excel(sb, index=False)
    G = C[C["Group Code"] != "XX_BAD"][["Group Code", "Center Code", "Day", "Time", "سعة القاعة"]]
    gb = io.BytesIO()
    G.to_excel(gb, index=False)
    return sb.getvalue(), C.to_csv(index=False).encode("utf-8-sig"), gb.getvalue()


def _upload(client, source="sheet", groups=False):
    s, c, g = _files()
    data = {"students": (io.BytesIO(s), "students_10.xlsx"), "source": source, "default_cap": "25"}
    if source == "sheet":
        data["centers"] = (io.BytesIO(c), "centers_10.csv")
    if groups:
        data["groups"] = (io.BytesIO(g), "groups_10.xlsx")
    r = client.post("/api/runs/upload", data=data, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def _run(client, token, **params):
    p = {"geo_mode": "sheet", "road_mode": "factor", "max_km": "50", "detour": "1.3"}
    p.update(params)
    r = client.post("/api/runs", json={"token": token, "name": "e2e10", "params": p})
    assert r.status_code == 200, r.get_json()
    run_id, jid = r.get_json()["run_id"], r.get_json()["job_id"]
    t0 = time.time()
    while time.time() - t0 < 120:
        j = client.get(f"/api/jobs/{jid}").get_json()
        if j["state"] != "running" and j.get("files") != "writing":
            break
        time.sleep(0.2)
    assert j["state"] == "done", j
    with gzip.open(config.RUNS_DIR / run_id / "students.csv.gz", "rt", encoding="utf-8") as f:
        return run_id, pd.read_csv(f, dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def run_a(client, monkeypatch_module):
    monkeypatch_module.setattr(locdb, "GMAPS_GAP", 0)
    pre = _upload(client)
    return pre, *_run(client, pre["token"])


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


def test_preflight_reports_every_planted_problem(run_a):
    pre = run_a[0]
    assert pre["ok"] is True
    st, ct = pre["students"], pre["centers"]
    assert (st["rows"], st["nid_invalid"], st["nid_dup"], st["unknown_gov_count"], st["band2_count"], st["no_level"]) == (10, 0, 0, 0, 1, 0)
    assert st["govs"] == {"cairo": 6, "alexandria": 2, "dakahlia": 1, "giza": 1}
    assert st["levels"] == {"L1": 7, "L3WB": 1, "L3CY": 1, "L2": 1}
    assert ct["bad_group_codes"] == 1 and ct["centers_no_coords"] == ["E2E_CAI_C03"] and ct["no_coords_with_link"] == 1
    assert ct["seats"] == 10, "مقاعد الجروب اللي كوده غلط (هيتجاهل) ما تتحسبش"
    cross = {(x["gov"], x["level_code"]): x["deficit"] for x in pre["cross"]}
    assert cross == {("alexandria", "L1"): 1, ("cairo", "L3CY"): 1, ("giza", "L1"): 1}


def test_every_student_gets_the_expected_result(run_a):
    st = run_a[2]
    assert len(st) == 10
    for i, (exp, (_, s)) in enumerate(zip(EXPECTED, st.iterrows()), 1):
        if exp[0] == "VP":
            assert s["status"] == "VP" and exp[1] in s["note"], (i, s["note"])
            assert s["nearest_center"] == exp[2], i
            if exp[3] is not None:
                assert float(s["distance_km"]) == pytest.approx(exp[3], abs=0.11), i
        else:
            got = (s["status"], s["center_code"], s["group_code"], s["same_area"])
            assert got == (exp[0], exp[1], exp[2], exp[4]), (i, got)
            assert float(s["distance_km"]) == pytest.approx(exp[3], abs=0.11), i
    assert st["البند"].tolist().count("البند الثاني") == 1


def test_summary_reports_and_downloads(client, run_a):
    run_id = run_a[1]
    res = client.get(f"/api/runs/{run_id}/result").get_json()
    s = res["summary"]
    assert (s["students"], s["assigned"], s["vp"], s["same_area"], s["centers"], s["groups"], s["seats"]) == (10, 7, 3, 6, 5, 8, 10)
    assert s["avg_km"] == pytest.approx(5.7, abs=0.05) and s["band2_count"] == 1
    assert any("لينك Google" in w for w in s["warnings"]) and any("مش بصيغة" in w for w in s["warnings"])
    load = {c["center_code"]: (c["assigned"], c["seats"]) for c in res["center_load"]}
    assert load == {"E2E_CAI_C01": (3, 3), "E2E_CAI_C02": (1, 3), "E2E_CAI_C03": (1, 1), "E2E_ALX_C01": (1, 2), "E2E_DKH_C01": (1, 1)}
    r = client.get(f"/api/runs/{run_id}/file/xlsx")
    assert r.status_code == 200 and "طلاب البند الثاني (للتعديل)" in pd.ExcelFile(io.BytesIO(r.data)).sheet_names
    assert client.get(f"/api/runs/{run_id}/file/qgis").status_code == 200
    r = client.get(f"/api/runs/{run_id}/file/per_center")
    assert r.status_code == 200 and {"E2E_CAI_C01", "VP الجيزة"} <= set(pd.ExcelFile(io.BytesIO(r.data)).sheet_names)
    far = client.get(f"/api/runs/{run_id}/students/far").get_json()["rows"]
    assert [x["nid"] for x in far] == ["30101010100088"] and far[0]["over_km"] == pytest.approx(18.8, abs=0.1)
    w = client.post(f"/api/runs/{run_id}/whatif", json={"center": "E2E_CAI_C01", "level": "L3CY", "capacity": 25}).get_json()
    assert (w["rescued"], w["remaining_vp"]) == (1, 0)


def test_centers_from_db_with_groups_sheet_gives_same_result(client, run_a):
    pre = _upload(client, source="db", groups=True)
    assert pre["ok"] and pre["source"]["centers"] == "db" and pre["source"]["groups"] == "من شيت الجروبات"
    _, st = _run(client, pre["token"])
    a = run_a[2]
    mine = st[st["Student NID"].isin(a["Student NID"])].reset_index(drop=True)
    assert (mine[["status", "center_code", "group_code"]].values == a[["status", "center_code", "group_code"]].values).all()


def test_smaller_max_km_turns_far_students_into_vp(client):
    pre = _upload(client)
    _, st = _run(client, pre["token"], max_km="10")
    assert st["status"].tolist() == ["تم التوزيع", "VP", "تم التوزيع", "VP", "تم التوزيع", "VP", "تم التوزيع", "VP", "تم التوزيع", "VP"]
    assert "داخل 10 كم ممتلئة" in st.iloc[1]["note"] and "أبعد من 10 كم" in st.iloc[7]["note"]


def test_osrm_down_is_refused_before_starting(client):
    pre = _upload(client)
    r = client.post("/api/runs", json={"token": pre["token"], "params": {"road_mode": "osrm", "osrm_url": "http://127.0.0.1:9",
                                                                          "geo_mode": "sheet"}})
    assert r.status_code == 400 and "OSRM" in r.get_json()["error"]


@pytest.mark.skipif(not HAS_GAZ, reason="محتاج data/gazetteer.sqlite")
@pytest.mark.parametrize("gov,area,addr,lat,lon,max_err", [
    # BUG اتصلّح: «- مدينة نصر» كانت بتتقري حي «النصر» (مكان تاني) والشارع يتدوّر عليه هناك ← غلط 5–7.5 كم
    ("cairo", "مدينة نصر", "شارع عباس العقاد - مدينة نصر", 30.0565, 31.3305, 1.5),
    ("cairo", "مدينه نصر", "شارع مصطفى النحاس - مدينة نصر", 30.0620, 31.3450, 1.5),
    # BUG اتصلّح: «حي مدينة نصر» كمنطقة كانت بتتقري «النصر»
    ("cairo", "حي مدينة نصر", "شارع الطيران - مدينة نصر", 30.0580, 31.3250, 1.0),
    ("cairo", "المعادي", "شارع مكرم عبيد - مدينة نصر", 30.0600, 31.3400, 1.0),
    ("dakahlia", "المنصورة", "شارع الجمهورية - المنصورة", 31.0400, 31.3800, 1.5),
    ("giza", "الدقي", "شارع التحرير - الدقي", 30.0380, 31.2110, 0.5),
])
def test_geocoder_accuracy_on_known_addresses(gov, area, addr, lat, lon, max_err):
    from distributor.core import geocoder
    r = geocoder.Geocoder(str(locdb.find_gazetteer())).geocode(gov, area, addr)
    assert geocoder._km(lat, lon, r["lat"], r["lon"]) <= max_err, r


@pytest.mark.skipif(not HAS_GAZ, reason="محتاج data/gazetteer.sqlite")
def test_geocoder_does_not_accept_one_generic_word_as_street():
    from distributor.core import geocoder
    r = geocoder.Geocoder(str(locdb.find_gazetteer())).geocode("dakahlia", "المنصورة", "المنصورة - 53 شارع مسجد السيدة هاجر")
    assert r["matched"] != "شارع المسجد"
