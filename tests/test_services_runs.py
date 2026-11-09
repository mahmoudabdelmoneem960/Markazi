"""خدمات ما بعد التوزيع: المقارنة، ماذا لو، التصدير لكل مركز، استعلامات الطلاب، الفحص المسبق، قراءة الشيتات، شيت الجروبات."""
import gzip
import io
import json
import uuid

import numpy as np
import pandas as pd
import pytest

from distributor import config, db
from distributor.core import engine
from distributor.services import compare, exports, groupsheet, preflight, runs, sheets, whatif


# ---------------------------------------------------------------- helpers
def centers_df(rows):
    return pd.DataFrame([{"Group Code": g, "Center Code": c, "Area": a, "City": city, "Day": "Friday", "Time": "10:00",
                          "Center Name": f"name {c}", "سعة القاعة": cap, "Latitude": la, "Longitude": lo}
                         for g, c, a, city, cap, la, lo in rows])


def students_df(rows, gov_col="gov"):
    return pd.DataFrame([{"Student NID": n, gov_col: g, "Area": a, "Adress": "عنوان", "Level(25-26)": lv, "Latitude": la, "Longitude": lo}
                         for n, g, a, lv, la, lo in rows])


def make_run(S, C, max_km=50, name="t"):
    """بيعمل تشغيل كامل على الديسك بنفس الملفات اللي runs.execute بيكتبها (من غير geocoding ولا Excel)."""
    run_id = uuid.uuid4().hex[:10]
    d = runs.run_dir(run_id)
    d.mkdir(parents=True)
    r = engine.distribute(S, C, max_km=max_km, road_mode="factor", detour=1.3, use_cache=False)
    st = r["students"].copy()
    st["lat"], st["lon"] = pd.to_numeric(st["Latitude"]), pd.to_numeric(st["Longitude"])
    st["geo_level"], st["geo_source"] = "sheet", "sheet"          # بيتحطوا من geoprep في التشغيل الحقيقي
    with gzip.open(d / runs.FILES["students"], "wt", encoding="utf-8", newline="") as f:
        st.to_csv(f, index=False)
    r["load"].to_csv(d / runs.FILES["groups"], index=False, encoding="utf-8-sig")
    summ = dict(r["summary"], params={"max_km": max_km, "detour": 1.3, "road_mode": "factor"})
    res = {"run_id": run_id, "summary": summ, "by_gov": runs.records(r["by_gov"]), "deficit": runs.records(r["deficit"]),
           "by_gov_level": runs.records(r["by_gov_level"]), "sus_centers": []}
    (d / runs.FILES["result"]).write_text(json.dumps(res, ensure_ascii=False, default=str), encoding="utf-8")
    con = db.connect()
    try:
        db.run_insert(con, {"id": run_id, "name": name, "params": summ["params"], "students_file": "s.xlsx", "centers_file": "c.csv"})
        db.run_update(con, run_id, status="done")
    finally:
        con.close()
    return run_id, r


CEN = [("L1_0001", "CAI_C1", "وسط", "cairo", 2, 30.05, 31.24), ("L1_0002", "CAI_C2", "شرق", "cairo", 5, 30.10, 31.30),
       ("L2_0003", "CAI_C1", "وسط", "cairo", 1, 30.05, 31.24), ("L1_0004", "ALX_C1", "وسط", "alexandria", 3, 31.20, 29.92)]


def base_students(prefix="31"):
    return [(f"{prefix}{i:012d}", g, a, lv, la, lo) for i, (g, a, lv, la, lo) in enumerate([
        ("القاهرة", "وسط", "Level (1)", 30.05, 31.24), ("القاهرة", "وسط", "Level (1)", 30.051, 31.241),
        ("القاهرة", "وسط", "Level (1)", 30.052, 31.242), ("القاهرة", "شرق", "Level (1)", 30.10, 31.30),
        ("القاهرة", "وسط", "Level (2)", 30.05, 31.24), ("القاهرة", "وسط", "Level (2)", 30.05, 31.24),
        ("الإسكندرية", "وسط", "Level (1)", 31.20, 29.92), ("الإسكندرية", "بعيد", "Level (1)", 31.90, 29.92),
        ("الجيزة", "الدقي", "Level (1)", 30.0, 31.2),
    ])]


@pytest.fixture(scope="module")
def run_a(app):
    return make_run(students_df(base_students()), centers_df(CEN), name="A")


# ---------------------------------------------------------------- compare
def test_compare_identical_runs(app, run_a):
    b, _ = make_run(students_df(base_students()), centers_df(CEN), name="B")
    r = compare.compare(run_a[0], b)
    p = r["pair"]
    assert p["key"] == "الرقم القومي" and p["common"] == 9 and p["only_a"] == p["only_b"] == 0
    assert p["same_outcome"] == 9 and p["same_outcome_pct"] == 100.0 and r["changed_total"] == 0
    assert p["same_center"] + p["both_vp"] == 9 and p["mean_abs_dist_diff"] == 0


def test_compare_detects_moves_and_status_changes(app, run_a):
    cen = centers_df(CEN)
    cen.loc[cen["Group Code"] == "L2_0003", "سعة القاعة"] = 2          # VP في A → متوزع في B
    cen.loc[cen["Group Code"] == "L1_0001", "سعة القاعة"] = 0          # طلاب وسط القاهرة L1 يتنقلوا لـ C2
    S = students_df(base_students())
    S = pd.concat([S, students_df([("39999999999999", "القاهرة", "شرق", "Level (1)", 30.10, 31.30)])], ignore_index=True)
    b, _ = make_run(S, cen, name="B2")
    r = compare.compare(run_a[0], b)
    p = r["pair"]
    assert p["only_b"] == 1 and p["only_a"] == 0
    assert p["a_vp_b_assigned"] == 1 and p["diff_center"] == 2
    assert r["changed_total"] == len(r["changed"]) == p["common"] - p["same_outcome"]
    ch = {x["key"]: x for x in r["changed"]}
    assert ch["31000000000005"]["status_a"] == "VP" and ch["31000000000005"]["status_b"] == "تم التوزيع"
    by = {x["gov_code"]: x for x in r["by_gov"]}
    assert by["cairo"]["students"] == 6 and by["alexandria"]["same_pct"] == 100.0
    xl = pd.ExcelFile(io.BytesIO(compare.compare_xlsx(run_a[0], b)))
    assert set(xl.sheet_names) == {"ملخص", "المقارنة", "حسب المحافظة", "طلاب نتيجتهم اتغيرت"}
    assert len(pd.read_excel(xl, "طلاب نتيجتهم اتغيرت")) == r["changed_total"]


def test_compare_falls_back_to_row_number_when_nids_are_broken(app, run_a):
    S = students_df(base_students())
    S["Student NID"] = "3.1E+13"
    b, _ = make_run(S, centers_df(CEN), name="B3")
    assert compare.compare(run_a[0], b)["pair"]["key"] == "رقم الصف"


def test_compare_missing_run(app, run_a):
    with pytest.raises(ValueError):
        compare.compare(run_a[0], "doesnotexist1")


# ---------------------------------------------------------------- what-if
def test_whatif_counts_rescued_vp_within_distance(app):
    rows = [(f"32{i:012d}", "القاهرة", "وسط", "Level (1)", 30.05 + 0.001 * i, 31.24) for i in range(7)]
    rows.append(("32999999999999", "القاهرة", "بعيد", "Level (1)", 30.60, 31.24))          # أبعد من 50 كم
    run_id, r = make_run(students_df(rows), centers_df([CEN[0]]))                             # مقعدين بس
    assert r["summary"]["vp"] == 6
    w = whatif.simulate(run_id, "CAI_C1", "L1", capacity=3, n_groups=1)
    assert (w["seats"], w["candidates"], w["rescued"], w["remaining_vp"], w["vp_total_level_gov"]) == (3, 5, 3, 3, 6)
    assert [x["km"] for x in w["list"]] == sorted(x["km"] for x in w["list"]) and len(w["list"]) == 3
    w = whatif.simulate(run_id, "CAI_C1", "L1", capacity=25, n_groups=2)
    assert w["rescued"] == 5 and w["remaining_vp"] == 1
    assert whatif.simulate(run_id, "CAI_C1", "L9", capacity=25)["rescued"] == 0
    for bad in [dict(capacity=0), dict(capacity=-5), dict(capacity=10, n_groups=0)]:
        with pytest.raises(ValueError):
            whatif.simulate(run_id, "CAI_C1", "L1", **bad)
    with pytest.raises(ValueError):
        whatif.simulate(run_id, "NOPE", "L1", capacity=5)


# ---------------------------------------------------------------- per-center export
def test_per_center_export_sheets(app, run_a):
    p = exports.per_center_xlsx(run_a[0])
    xl = pd.ExcelFile(p)
    assert xl.sheet_names[0] == "ملخص المراكز"
    assert {"CAI_C1", "CAI_C2", "ALX_C1"} <= set(xl.sheet_names)
    assert any(n.startswith("VP ") for n in xl.sheet_names)
    summ = pd.read_excel(xl, "ملخص المراكز")
    assert summ["students"].sum() == run_a[1]["summary"]["assigned"]
    assert exports.per_center_xlsx(run_a[0]) == p                                        # بيتعمل مرة واحدة


def test_regression_per_center_export_with_arabic_gov_header_and_case_clash(app):
    """BUG اتصلّح: لو عمود المحافظة اسمه «المحافظة» التصدير كان بيقع (KeyError 'gov')،
    وكودين مركز بيختلفوا في capital/small بس كانوا بيوقعوا Excel (اسم شيت مكرر)."""
    cen = centers_df([("L1_0001", "abc_C1", "a", "cairo", 1, 30.05, 31.24), ("L1_0002", "ABC_C1", "b", "cairo", 1, 30.06, 31.25)])
    S = students_df([(f"33{i:012d}", "القاهرة", "a", "Level (1)", 30.05, 31.24) for i in range(3)], gov_col="المحافظة")
    run_id, _ = make_run(S, cen)
    xl = pd.ExcelFile(exports.per_center_xlsx(run_id))
    assert len([n for n in xl.sheet_names if n.lower().startswith("abc_c1")]) == 2
    assert any(n.startswith("VP ") for n in xl.sheet_names)


def test_sheet_name_rules():
    used = set()
    names = [exports._sheet_name(x, used) for x in ["a/b", "A/B", "x" * 40, "'q'", ""]]
    assert names[0] == "a b" and names[1].lower() != names[0].lower() and len(names[2]) <= 31
    assert names[3] == "q" and names[4] == "sheet"


# ---------------------------------------------------------------- student queries on a run
def test_run_student_queries(app, run_a):
    run_id = run_a[0]
    pts = runs.students_points(run_id)
    assert pts["total"] == 9 and len(pts["points"]) == 9
    assert runs.students_points(run_id, status="vp")["total"] == run_a[1]["summary"]["vp"]
    assert runs.students_points(run_id, gov="alexandria", status="assigned")["total"] == 1
    assert runs.students_points(run_id, limit=2)["shown"] == 2
    c = runs.students_points(run_id, center="CAI_C1")
    assert c["total"] >= 3                                      # المتوزعين + الـVP اللي أقرب مركز ليهم هو ده
    det = runs.get_center_run_details(run_id, "CAI_C1")
    assert det["center"]["total_seats"] == 3 and det["center"]["total_assigned"] == 3 and det["center"]["occupancy_%"] == 100.0
    with pytest.raises(ValueError):
        runs.get_center_run_details(run_id, "NOPE")
    far = runs.get_far_students(run_id)
    assert far["total"] == 1 and far["rows"][0]["over_km"] > 0 and far["max_km"] == 50
    assert runs.get_far_students_points(run_id)["total"] == 1
    assert runs.get_band2_students(run_id)["total"] == 1
    assert runs.get_invalid_nids(run_id)["total"] == 0
    rec = runs.get_student_row(run_id, "2")
    assert rec["_meta"]["nid_valid"] and runs.get_student_row(run_id, rec["Student NID"])["row_no"] == "2"
    with pytest.raises(KeyError):
        runs.get_student_row(run_id, "99999")


def test_update_student_saves_override(app):
    run_id, _ = make_run(students_df(base_students("34")), centers_df(CEN))
    out = runs.update_student(run_id, "2", {"lat": "30.0601", "lon": "31.2501", "not_a_column": "x"}, user="t")
    assert out["student"]["lat"] == "30.0601" and "not_a_column" not in out["student"]
    from distributor.core import locdb
    con = db.connect()
    try:
        assert locdb.overrides_get(con, ["34000000000000"]) == {"34000000000000": (30.0601, 31.2501)}
    finally:
        con.close()
    with pytest.raises(KeyError):
        runs.update_student(run_id, "777", {"lat": 1})


def test_search_student_across_runs(app, run_a):
    with pytest.raises(ValueError):
        runs.search_student("12")
    r = runs.search_student("31000000000003")
    assert r["total"] >= 1 and all(x["exact"] for x in r["rows"][:1])
    assert any(x["run_id"] == run_a[0] for x in r["rows"])
    r = runs.search_student("٣١٠٠٠٠٠٠٠٠٠٠٠٣", run_id=run_a[0])                          # أرقام عربي
    assert r["total"] == 1
    assert runs.search_student("l1_0002", run_id=run_a[0])["total"] >= 1                 # كود الجروب
    assert runs.search_student("zzzzzz", run_id=run_a[0])["total"] == 0


def test_load_result_backfills_by_gov_level_for_old_runs(app):
    run_id, r = make_run(students_df(base_students("35")), centers_df(CEN))
    p = runs.run_dir(run_id) / runs.FILES["result"]
    res = json.loads(p.read_text(encoding="utf-8"))
    res.pop("by_gov_level")
    res["summary"]["vp_reasons"] = [{"reason": "أبعد من X كم", "count": 1}]
    p.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    out = runs.load_result(run_id)
    bgl = {(x["gov"], x["level_code"]): x for x in out["by_gov_level"]}
    assert bgl[("cairo", "L1")]["students"] == 4 and bgl[("cairo", "L1")]["seats"] == 7
    assert "50" in out["summary"]["vp_reasons"][0]["reason"]


def test_run_dir_rejects_traversal():
    for bad in ["../x", "a/b", "..", "a b", ""]:
        with pytest.raises(ValueError):
            runs.run_dir(bad)


# ---------------------------------------------------------------- preflight
def _pf_students(n=10, gov="القاهرة"):
    return pd.DataFrame({"Student NID": [f"36{i:012d}" for i in range(n)], "gov": [gov] * n, "Area": ["a"] * n,
                         "Adress": ["b"] * n, "Level(25-26)": ["Level (1)"] * n})


def _pf_centers(cap=5):
    return pd.DataFrame({"Group Code": ["L1_0001"], "Center Code": ["PF_C1"], "City": ["cairo"], "Area": ["a"],
                         "سعة القاعة": [cap], "Latitude": ["30.05"], "Longitude": ["31.24"]})


def test_preflight_cross_deficit_and_ok(app):
    rep = preflight.preflight(_pf_students(10), _pf_centers(5))
    assert rep["ok"] and rep["cross"] == [{"gov": "cairo", "level_code": "L1", "students": 10, "seats": 5, "deficit": 5}]
    assert any("العجز الإجمالي" in i["text"] for i in rep["issues"])
    assert rep["students"]["govs"] == {"cairo": 10} and rep["centers"]["seats"] == 5


def test_preflight_band2_and_name_variants(app):
    S = pd.concat([_pf_students(3, "الجيزة"), _pf_students(2, "Cairo"), _pf_students(1, "بور سعيد")], ignore_index=True)
    rep = preflight.preflight(S, _pf_centers())
    st = rep["students"]
    assert st["band2_count"] == 3 and st["unknown_gov_count"] == 0
    assert any("البند الثاني" in i["text"] and i["level"] == "warn" for i in rep["issues"])


def test_preflight_missing_location_columns_is_error(app):
    S = _pf_students().drop(columns=["Adress"])
    rep = preflight.preflight(S, _pf_centers())
    assert not rep["ok"] and "Latitude/Longitude" in rep["students"]["missing"][0]
    rep = preflight.preflight(_pf_students().assign(Latitude="30.0", Longitude="31.0").drop(columns=["Adress", "Area"]), _pf_centers())
    assert rep["ok"]


def test_preflight_center_location_sources(app):
    C = pd.DataFrame({"Group Code": ["L1_0001", "L1_0002", "L1_0003"], "Center Code": ["PF_L1", "PF_L2", "PF_L3"],
                      "City": ["cairo"] * 3, "Area": ["a"] * 3, "سعة القاعة": [5] * 3, "Latitude": ["", "", ""],
                      "Longitude": ["", "", ""], "لينك": ["https://maps.google.com/?q=30,31", "", ""],
                      "العنوان": ["", "شارع 1", ""]})
    ct = preflight.preflight(_pf_students(), C)["centers"]
    assert ct["no_coords_with_link"] == 1 and ct["no_coords_with_addr_only"] == 1 and ct["no_location_at_all"] == ["PF_L3"]


# ---------------------------------------------------------------- reading sheets
def test_parse_file_formats_keep_nid_as_text():
    df = pd.DataFrame({"Student NID": ["30000000000001", "00000000000002"], "gov": ["القاهرة", "الجيزة"]})
    for name, raw in [("s.csv", df.to_csv(index=False).encode("utf-8-sig")),
                      ("s.csv", df.to_csv(index=False).encode("cp1256")),
                      ("s.xlsx", _xlsx(df))]:
        out = sheets.parse_file(name, raw)
        assert out["Student NID"].tolist() == ["30000000000001", "00000000000002"], name
        assert out["gov"].tolist() == ["القاهرة", "الجيزة"]
    with pytest.raises(ValueError, match="تعذر قراءة الملف"):
        sheets.parse_file("s.xlsx", b"not an excel file")


def _xlsx(df):
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


@pytest.mark.parametrize("v,out", [(None, ""), (float("nan"), ""), ("  x ", "x"), ("None", ""), ("NULL", ""), ("nan", ""), (5, "5")])
def test_clean(v, out):
    assert sheets.clean(v) == out


def test_student_db_level_from_group_beats_level_name():
    S = pd.DataFrame({"national_id": ["1", "2", "3"], "city": ["cairo", "Giza ", "mars"],
                      "level": ["first", "third_web", "fifth_embedded"], "group": ["ON-CA-L4CY-G1", "", ""]})
    out, _ = sheets.adapt_students(S)
    assert out["Level(25-26)"].tolist() == ["Level (4): cyber", "Level (3): web", "Level (5): embedded"]
    assert [engine.gov_code(g) for g in out["gov"]] == ["cairo", "giza", None]
    assert [engine.level_key(x) for x in out["Level(25-26)"]] == ["L4CY", "L3WB", "L5EM"]


# ---------------------------------------------------------------- groups sheet
def test_normalize_groups_days_caps_and_codes():
    G = pd.DataFrame({"كود الجروب": ["PH-A-CAI-V01-L1-GEN-C001", "PH-B-CAI-V01-L1-GEN-C002", "L2_0001", "", "L2_0002"],
                      "كود المركز": ["C1", "C1", "C2", "C3", "C2"], "اليوم": ["", "", "الجمعة", "", "sunday"],
                      "التوقيت": ["10:00", "", "", "", ""], "السعة": ["20", "", "x", "", "30"]})
    out, info = groupsheet.normalize_groups(G, default_cap=25)
    assert len(out) == 4                                                   # الصف من غير كود اتشال
    assert out["Day"].tolist() == ["Friday", "Saturday", "Friday", "Sunday"]
    assert out["سعة القاعة"].tolist() == [20, 25, 25, 30]
    assert info == {"cap_from_sheet": True, "has_day": True, "has_time": True}
    with pytest.raises(ValueError, match="ناقص"):
        groupsheet.normalize_groups(pd.DataFrame({"x": [1]}))
    with pytest.raises(ValueError, match="فاضي"):
        groupsheet.normalize_groups(pd.DataFrame({"Group Code": [""], "Center Code": ["C1"]}))


def test_build_drops_groups_of_unknown_centers():
    groups = pd.DataFrame({"Group Code": ["L1_1", "L1_2", "L1_3"], "Center Code": ["C1", "CX", ""], "Day": "", "Time": "",
                           "سعة القاعة": 25})
    info = pd.DataFrame({"Center Code": ["C1", "C1"], "Center Name": ["n", "dup"], "City": ["cairo", "cairo"], "Area": ["a", "a"],
                         "العنوان التفصيلي للمركز": "", "لوكيشن المكان": "", "Latitude": [30.0, 30.0], "Longitude": [31.0, 31.0]})
    out, notes = groupsheet.build(groups, info)
    assert out["Group Code"].tolist() == ["L1_1"] and list(out.columns) == groupsheet.OUT_COLS
    assert len(notes) == 2 and "CX" in notes[0][1]
    with pytest.raises(ValueError):
        groupsheet.build(groups.iloc[1:], info)
