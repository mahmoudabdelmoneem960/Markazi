"""أكواد الجروبات (Online / Physical) من نتيجة التخطيط."""
import io
import re

import pandas as pd

from distributor.services import groupcodes as gc

REP = {
    "levels_order": ["L1", "L2", "L3CY", "L4DT", "غير محدد"],
    "govs": [
        {"gov_code": "cairo", "gov": "القاهرة", "band": "البند الأول", "students": 0,
         "levels": {"L1": 103, "L2": 50, "L3CY": 26, "L4DT": 1, "غير محدد": 4}},
        {"gov_code": "alexandria", "gov": "الإسكندرية", "band": "البند الثالث", "students": 0,
         "levels": {"L1": 30, "L3CY": 1}},
        {"gov_code": "giza", "gov": "الجيزة", "band": "البند الثاني", "students": 0, "levels": {"L1": 40}},
        {"gov_code": "غير معروفة", "gov": "غير معروفة", "band": "غير محدد", "students": 0, "levels": {"L2": 7}},
    ],
}

ON = re.compile(r"^ON-[AB]-S1-L\d-(GEN|CY|DT|WB|DA|EM)-C\d{3}$")
PH = re.compile(r"^PH-[AB]-[A-Z]{3}-V01-L\d-(GEN|CY|DT|WB|DA|EM)-C\d{3}$")


def test_split_level():
    assert gc.split_level("L1") == ("L1", "GEN")
    assert gc.split_level("L3CY") == ("L3", "CY")
    assert gc.split_level("L5WB") == ("L5", "WB")


def test_online_codes():
    r = gc.generate(REP, "online", 25)
    rows = r["rows"]
    assert all(ON.match(x["code"]) for x in rows), rows[:3]
    assert len({x["code"] for x in rows}) == len(rows)
    # Online على مستوى الجمهورية: L1 = 103+30+40 = 173 → 7 جروبات، L2 = 50+7 = 57 → 3
    lv = {x["level_code"]: x for x in r["summary"]["levels"]}
    assert lv["L1"]["groups"] == 7 and lv["L2"]["groups"] == 3 and lv["L3CY"]["groups"] == 2 and lv["L4DT"]["groups"] == 1
    assert "غير محدد" not in lv
    assert r["summary"]["students"] == 173 + 57 + 27 + 1
    # كل ليفيل نص ونص (فرق جروب واحد بالكتير)، والإجمالي متوازن
    assert all(abs(x["A"] - x["B"]) <= 1 for x in lv.values())
    d = {x["day"]: x["groups"] for x in r["summary"]["days"]}
    assert abs(d["A"] - d["B"]) <= 1
    # الطلاب متوزعين بالتساوي والجروب مش أكبر من السعة
    l1 = [x["students"] for x in rows if x["level_code"] == "L1"]
    assert sum(l1) == 173 and max(l1) <= 25 and max(l1) - min(l1) <= 1
    assert [x["code"] for x in rows if x["level_code"] == "L1"][0] == "ON-A-S1-L1-GEN-C001"
    assert [x["code"] for x in rows if x["level_code"] == "L1"][-1] == "ON-B-S1-L1-GEN-C007"


def test_physical_codes():
    r = gc.generate(REP, "physical", 25)
    rows = r["rows"]
    assert all(PH.match(x["code"]) for x in rows), rows[:3]
    assert len({x["code"] for x in rows}) == len(rows)
    assert {x["gov3"] for x in rows} == {"CAI", "ALX"}            # البند التاني والمحافظة غير المعروفة برا
    ex = {x["gov"]: x for x in r["excluded"]}
    assert ex["الجيزة"]["students"] == 40 and ex["غير معروفة"]["students"] == 7
    cai_l1 = [x for x in rows if x["gov3"] == "CAI" and x["level_code"] == "L1"]
    assert len(cai_l1) == 5 and cai_l1[0]["code"] == "PH-A-CAI-V01-L1-GEN-C001"
    assert [x["day"] for x in cai_l1].count("A") in (2, 3)
    # جروب واحد في كذا محافظة لنفس الليفيل: الزيادة بتتبادل بين الجمعة والسبت
    l3 = sorted(x["day"] for x in rows if x["level_code"] == "L3CY")
    assert l3.count("A") - l3.count("B") in (-1, 0, 1)


def test_excel_and_api(client, sample_files):
    x = gc.to_excel(gc.generate(REP, "physical", 25))
    df = pd.read_excel(x, sheet_name="أكواد الجروبات")
    assert df["Group Code"].str.match(PH.pattern).all() and "Venue" in df.columns
    sb = sample_files[0]
    r = client.post("/api/planning/analyze", data={"students": (io.BytesIO(sb), "students.xlsx"), "cap": "25", "mode": "online"},
                    content_type="multipart/form-data")
    pid = r.get_json()["id"]
    j = client.get(f"/api/planning/{pid}/codes").get_json()               # النوع والسعة من التحليل المحفوظ
    assert j["mode"] == "online" and j["rows"] and all(ON.match(c["code"]) for c in j["rows"])
    h = next(x for x in client.get("/api/planning/history").get_json() if x["id"] == pid)
    assert h["groups"] == j["summary"]["groups"]
    j = client.get(f"/api/planning/{pid}/codes?mode=physical&cap=30").get_json()
    assert j["mode"] == "physical" and j["cap"] == 30 and all(PH.match(c["code"]) for c in j["rows"])
    r = client.get(f"/api/planning/{pid}/codes.xlsx?mode=physical")
    assert r.status_code == 200 and r.data[:2] == b"PK"
    assert client.get("/api/planning/nope/codes").status_code == 404
    # تقرير التخطيط الكامل فيه الأكواد كمان
    r = client.get(f"/api/planning/{pid}/export.xlsx?mode=physical&cap=30")
    xl = pd.ExcelFile(io.BytesIO(r.data))
    assert {"ملخص", "أكواد الجروبات", "أكواد - الليفيلات × الأيام", "أكواد - المحافظات"} <= set(xl.sheet_names)
    df = pd.read_excel(xl, sheet_name="أكواد الجروبات")
    assert len(df) == len(j["rows"]) and df["Group Code"].str.match(PH.pattern).all()
