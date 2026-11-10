"""المقارنة بين تشغيلة من السيستم وتشغيلة خارجية (شيت توزيع اتعمل برا السيستم)."""
import io

import pandas as pd
import pytest

from distributor.services import compare, external

from test_services_runs import CEN, base_students, centers_df, make_run, students_df

NID = [f"31{i:012d}" for i in range(9)]
# نتيجة السيستم للطلاب دول (test_services_runs): 0,1 → CAI_C1 L1_0001 | 2,3 → CAI_C2 L1_0002 | 4 → CAI_C1 L2_0003 | 5 VP
#                                              6 → ALX_C1 L1_0004 | 7 VP (بعيد) | 8 VP (الجيزة)


def xlsx(df):
    b = io.BytesIO()
    df.to_excel(b, index=False)
    return b.getvalue()


@pytest.fixture(scope="module")
def sys_run(app):
    run_id, r = make_run(students_df(base_students()), centers_df(CEN), name="system")
    return run_id, r["students"]


def test_identical_external_sheet_matches_100_percent(sys_run):
    run_id, st = sys_run
    ext = pd.DataFrame({"Student NID": st["Student NID"], "Center Code": st["center_code"], "Group Code": st["group_code"],
                        "Status": st["status"]})
    meta = external.import_sheet(run_id, "same.xlsx", xlsx(ext))
    assert meta["rows"] == 9 and meta["matched"] == 9 and meta["assigned"] == 6 and meta["issues_total"] == 0
    assert meta["columns"] == {"nid": "Student NID", "center": "Center Code", "group": "Group Code", "status": "Status"}
    r = compare.compare(run_id, meta["id"])
    assert r["pair"]["key"] == "الرقم القومي" and r["pair"]["same_outcome_pct"] == 100.0 and r["changed_total"] == 0
    assert r["b"]["external"] is True and r["external"]["name"] == "same.xlsx" and r["external"]["issues"] == []
    # المسافة المقدّرة للملف الخارجي = نفس مسافة السيستم (نفس الطريقة: خط مستقيم × المعامل)
    assert r["pair"]["mean_abs_dist_diff"] == 0


def test_student_db_style_sheet_with_planted_problems(sys_run):
    """أسماء أعمدة تانية (national_id / Center Name / group) + كل نوع مشكلة مرة."""
    run_id, _ = sys_run
    rows = [
        (NID[0], "name CAI_C1", "L1_0001", "تم التوزيع"),    # مطابق للسيستم (المركز من الاسم)
        (NID[1], "مركز مش موجود", "", "موزع"),               # مركز مش في السيستم
        (NID[2], "", "L2_0003", ""),                          # المركز من كود الجروب، بس الجروب L2 والطالب L1
        (NID[3], "name ALX_C1", "", ""),                      # مركز في الإسكندرية لطالب من القاهرة (وبعيد)
        (NID[4], "", "L2_0003", ""),                          # زي السيستم، بس L2_0003 (سعته 1) بقى فيه طالبين
        (NID[5], "", "", "VP"),                               # VP زي السيستم
        (NID[6], "", "", "VP"),                               # موزع في السيستم → VP هنا
        (NID[6], "name ALX_C1", "", ""),                      # رقم قومي متكرر
        ("39999999999999", "name CAI_C1", "", ""),            # مش في تشغيلة السيستم
        (f"{NID[7]}.0", "", "", "لم يتم التوزيع"),             # الرقم القومي بـ .0 + VP بالعربي
        ("٣١٠٠٠٠٠٠٠٠٠٠٠٨", "name CAI_C1", "L1_0001", ""),     # أرقام عربي؛ VP في السيستم → موزع هنا
    ]
    ext = pd.DataFrame(rows, columns=["national_id", "Center Name", "group", "الحالة"])
    ext.insert(1, "parent_national_id", "29000000000000")                # ما يتلخبطش مع الرقم القومي للطالب
    meta = external.import_sheet(run_id, "db.xlsx", xlsx(ext))
    assert meta["columns"]["nid"] == "national_id" and meta["columns"]["center_name"] == "Center Name"
    assert meta["matched"] == 9 and meta["students"] == 10
    c = meta["counts"]
    assert (c["level_mismatch"], c["other_gov"], c["unknown_center"], c["duplicate"], c["not_in_run"]) == (1, 2, 1, 1, 1)   # other_gov: القاهرة→الإسكندرية + الجيزة→القاهرة
    assert c["over_max_km"] == 1                                            # القاهرة → الإسكندرية أبعد من 50 كم
    assert c["over_capacity"] == 1 and meta["over_capacity"][0] == {"group": "L2_0003", "center": "CAI_C1", "students": 2, "capacity": 1}
    assert meta["unresolved_centers"] == {"مركز مش موجود": 1}

    r = compare.compare(run_id, meta["id"])
    p = r["pair"]
    assert p["common"] == 9 and p["only_b"] == 1
    assert p["a_vp_b_assigned"] == 1 and p["a_assigned_b_vp"] == 1
    ch = {x["key"]: x for x in r["changed"]}
    assert ch[NID[1]]["center_b"] == "مركز مش موجود" and ch[NID[3]]["center_b"] == "ALX_C1" and ch[NID[8]]["status_a"] == "VP"
    assert ch[NID[2]]["center_b"] == "CAI_C1" and ch[NID[6]]["status_b"] == "VP"
    assert NID[0] not in ch and NID[4] not in ch and NID[5] not in ch and NID[7] not in ch
    probs = {(x["key"], x["problem"]) for x in r["external"]["issues"]}
    assert (NID[2], "level_mismatch") in probs and (NID[3], "other_gov") in probs and ("39999999999999", "not_in_run") in probs


def test_import_errors(sys_run):
    run_id, _ = sys_run
    with pytest.raises(ValueError, match="الرقم القومي"):
        external.import_sheet(run_id, "x.xlsx", xlsx(pd.DataFrame({"Center Code": ["CAI_C1"]})))
    with pytest.raises(ValueError, match="كود المركز"):
        external.import_sheet(run_id, "x.xlsx", xlsx(pd.DataFrame({"Student NID": [NID[0]], "notes": ["x"]})))
    with pytest.raises(ValueError, match="مالهاش نتيجة"):
        external.import_sheet("nosuchrun1", "x.xlsx", xlsx(pd.DataFrame({"Student NID": [NID[0]], "Center Code": ["C"]})))
    S = students_df(base_students("37"))
    S["Student NID"] = "3.1E+13"
    bad_run, _ = make_run(S, centers_df(CEN))
    with pytest.raises(ValueError, match="أرقامها القومية"):
        external.import_sheet(bad_run, "x.xlsx", xlsx(pd.DataFrame({"Student NID": [NID[0]], "Center Code": ["CAI_C1"]})))
    for bad in ["../x", "ext/../a", "run123", ""]:
        assert not external.is_external(bad)


def test_external_compare_api_flow(client, sys_run):
    run_id, st = sys_run
    csv = pd.DataFrame({"الرقم القومي": st["Student NID"], "كود المركز": st["center_code"], "كود الجروب": st["group_code"]})
    csv.loc[0, "كود المركز"] = "CAI_C2"
    raw = csv.to_csv(index=False).encode("utf-8-sig")
    assert client.post("/api/compare/external", data={"run": run_id}, content_type="multipart/form-data").status_code == 400
    assert client.post("/api/compare/external", data={"file": (io.BytesIO(raw), "e.csv")},
                       content_type="multipart/form-data").status_code == 400
    r = client.post("/api/compare/external", data={"file": (io.BytesIO(raw), "e.csv"), "run": run_id}, content_type="multipart/form-data")
    assert r.status_code == 200, r.get_json()
    eid = r.get_json()["id"]
    assert any(x["id"] == eid for x in client.get(f"/api/compare/externals?run={run_id}").get_json())
    j = client.get(f"/api/compare?a={run_id}&b={eid}").get_json()
    assert j["changed_total"] == 1 and j["changed"][0]["center_b"] == "CAI_C2" and j["b"]["external"]
    x = client.get(f"/api/compare.xlsx?a={run_id}&b={eid}")
    assert x.status_code == 200
    assert {"فحص الملف الخارجي", "مشاكل الملف الخارجي"} <= set(pd.ExcelFile(io.BytesIO(x.data)).sheet_names)
    assert client.get("/compare").status_code == 200
    assert client.delete(f"/api/compare/external/{eid}").get_json() == {"ok": True}
    assert client.get(f"/api/compare?a={run_id}&b={eid}").status_code == 400
    assert client.delete("/api/compare/external/..%2Fx").status_code in (404, 405)
