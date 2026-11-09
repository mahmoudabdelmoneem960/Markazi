"""التخطيط (تحليل شيت الطلاب) + أكواد الجروبات: الأرقام والترتيب والحسابات."""
import io
import re

import numpy as np
import pandas as pd
import pytest

from distributor.core import engine
from distributor.services import groupcodes as gc
from distributor.services import planning


def sheet(rows):
    """rows: (nid, gov, area, level)"""
    return pd.DataFrame([{"Student NID": n, "gov": g, "Area": a, "Adress": "", "Level(25-26)": lv} for n, g, a, lv in rows])


def nids(k, start=0):
    return [f"3{start + i:013d}" for i in range(k)]


@pytest.fixture(scope="module")
def rep():
    rows = []
    rows += [(n, "القاهرة", "مصر الجديدة", "Level (1)") for n in nids(40, 0)]
    rows += [(n, "القاهره", "مصر الجديده", "Level (2)") for n in nids(20, 100)]              # كتابة تانية لنفس المنطقة
    rows += [(n, "القاهرة", "", "Level (3): Web") for n in nids(5, 200)]
    rows += [(n, "الإسكندرية", "حي المنتزه", "Level (1)") for n in nids(25, 300)]
    rows += [(n, "الإسكندرية", "المنتزه", "Level (3): Cyber") for n in nids(5, 400)]
    rows += [(n, "الجيزة", "الدقي", "Level (1)") for n in nids(10, 500)]
    rows += [(n, "كوكب", "x", "Level (1)") for n in nids(3, 600)]
    rows += [(n, "القاهرة", "x", "مش معروف") for n in nids(2, 700)]
    rows += [("123", "القاهرة", "x", "Level (10)"), ("30000000000000", "القاهرة", "x", "Level (2)")]   # رقم بايظ + مكرر
    return planning.analyze(sheet(rows))


def test_totals_and_unknowns(rep):
    assert rep["total"] == 112
    assert rep["unknown_gov"] == 3 and rep["unknown_level"] == 2
    assert rep["unknown_gov_names"] == {"كوكب": 3}
    assert rep["nid"] == {"invalid": 1, "dup": 1}


def test_levels_natural_order_and_ranking(rep):
    assert rep["levels_order"] == ["L1", "L2", "L3CY", "L3WB", "L10", "غير محدد"]
    lv = {x["level"]: x for x in rep["levels"]}
    assert lv["L1"]["students"] == 78 and lv["L1"]["rank"] == 1 and lv["L1"]["govs"] == 4
    assert [x["rank"] for x in rep["levels"]] == list(range(1, len(rep["levels"]) + 1))
    assert sum(x["students"] for x in rep["levels"]) == rep["total"]
    assert sum(x["pct"] for x in rep["levels"]) == pytest.approx(100, abs=0.5)


def test_governorates_ranked_with_levels_and_bands(rep):
    g = {x["gov_code"]: x for x in rep["govs"]}
    assert [x["gov_code"] for x in rep["govs"]][:3] == ["cairo", "alexandria", "giza"]
    assert g["cairo"]["students"] == 69 and g["cairo"]["gov"] == "القاهرة" and g["cairo"]["band"] == engine.BAND_1
    assert g["cairo"]["levels"]["L1"] == 40 and g["cairo"]["levels"]["L2"] == 21
    assert g["cairo"]["levels_ranked"][0] == {"level": "L1", "students": 40, "pct": 58.0}
    assert g["giza"]["band"] == engine.BAND_2 and g["غير معروفة"]["band"] == "غير محدد"
    assert sum(sum(x["levels"].values()) for x in rep["govs"]) == rep["total"]
    # الترتيب جوه البند
    assert g["cairo"]["band_rank"] == 1 and g["alexandria"]["band_rank"] == 1 and g["cairo"]["pct_band"] == 100.0


def test_bands_summary(rep):
    b = {x["band"]: x for x in rep["bands"]}
    assert b[engine.BAND_1]["students"] == 69 and b[engine.BAND_3]["students"] == 30 and b[engine.BAND_2]["students"] == 10
    assert b["غير محدد"]["students"] == 3
    assert sum(x["students"] for x in rep["bands"]) == rep["total"]
    assert b[engine.BAND_1]["rank"] == 1 and b[engine.BAND_2]["govs_list"] == ["الجيزة"]


def test_areas_merge_spelling_variants_and_no_area_last(rep):
    cairo = next(x for x in rep["govs"] if x["gov_code"] == "cairo")["areas"]
    top = cairo[0]
    assert top["area"] == "مصر الجديدة" and top["students"] == 60 and "مصر الجديده" in top["variants"]
    assert top["levels"] == {"L1": 40, "L2": 20}
    assert cairo[-1]["area"] == planning.NO_AREA and cairo[-1]["students"] == 5
    assert [a["rank"] for a in cairo] == list(range(1, len(cairo) + 1))
    alex = next(x for x in rep["govs"] if x["gov_code"] == "alexandria")["areas"]
    assert len(alex) == 1 and alex[0]["students"] == 30                                   # «حي المنتزه» = «المنتزه»


@pytest.mark.parametrize("count,cap,groups", [(0, 25, 0), (1, 25, 1), (25, 25, 1), (26, 25, 2), (100, 25, 4), (5, 0, 0), (5, None, 0)])
def test_groups_needed(count, cap, groups):
    assert planning.groups_needed(count, cap) == groups


def test_total_groups_physical_vs_online(rep):
    phys = planning.total_groups(rep, 25, "physical")
    onl = planning.total_groups(rep, 25, "online")
    # Physical: لكل محافظة × ليفيل، من غير المحافظة/الليفيل غير المعروفين
    exp = sum(planning.groups_needed(c, 25) for g in rep["govs"] if g["gov_code"] != "غير معروفة"
              for k, c in g["levels"].items() if k != "غير محدد")
    assert phys == exp
    assert onl <= phys + 1 and planning.total_groups(rep, None) is None and planning.total_groups(None, 25) is None


def test_missing_columns_error():
    with pytest.raises(ValueError, match="ناقص"):
        planning.analyze(pd.DataFrame({"Student NID": ["1"], "Area": ["x"]}))


def test_student_db_export_is_analyzed():
    S = pd.DataFrame({"national_id": nids(3), "city": ["cairo", "giza", "alexandria"], "area": ["a", "b", "c"],
                      "level": ["first", "second", "third_ai"], "group": ["", "", ""]})
    r = planning.analyze(S)
    assert r["total"] == 3 and r["unknown_gov"] == 0 and set(r["levels_order"]) == {"L1", "L2", "L3DT"}
    assert r["note"] and "Student DB" in r["note"]


def test_excel_report_sheets(rep):
    xl = pd.ExcelFile(planning.to_excel(dict(rep)))
    assert {"ملخص", "البنود", "المحافظات", "المناطق", "الليفيلات", "محافظة × ليفيل"} <= set(xl.sheet_names)
    m = pd.read_excel(xl, "محافظة × ليفيل")
    assert m.iloc[-1]["المحافظة"] == "الإجمالي" and m.iloc[-1]["الإجمالي"] == rep["total"]
    xl = pd.ExcelFile(planning.to_excel(dict(rep), cap=25, mode="online", codes=gc.generate(rep, "online", 25)))
    assert "جروبات محافظة × ليفيل" in xl.sheet_names and "أكواد الجروبات" in xl.sheet_names
    s = pd.read_excel(xl, "ملخص").set_index("البند")["القيمة"]
    assert s["نوع الجروبات"] == "Online" and int(s["إجمالي الجروبات المطلوبة"]) == planning.total_groups(rep, 25, "physical")


def test_flags_filtering_and_paging():
    rows = [{"row_no": i + 2, "nid": f"3{i:013d}", "gov_code": "cairo" if i % 2 else "giza", "kind": "no_area" if i < 5 else "street",
             "conf": "أكيد", "area": "" if i < 5 else f"شارع {i}", "address": "عنوان", "other_gov": ""} for i in range(12)]
    f = planning._filter_flags
    assert len(f(rows, kind="no_area")) == 5 and len(f(rows, gov="cairo")) == 6
    assert len(f(rows, area="—")) == 5 and len(f(rows, q="شارع 7")) == 1 and len(f(rows, q="9")) >= 1
    assert len(f(rows, q=str(rows[3]["nid"]))) == 1


# ---------------------------------------------------------------- group codes
ON = re.compile(r"^ON-([AB])-S1-(L\d+)-(GEN|CY|DT|WB|DA|EM)-C(\d{3})$")
PH = re.compile(r"^PH-([AB])-([A-Z]{3})-V01-(L\d+)-(GEN|CY|DT|WB|DA|EM)-C(\d{3})$")


def random_report(rng):
    levels = ["L1", "L2", "L3CY", "L3WB", "L4DT", "L5EM", "غير محدد"]
    codes = list(gc.GOV3_DEFAULT) + ["giza", "qena", "غير معروفة"]
    govs = []
    for g in codes:
        lv = {k: int(rng.integers(0, 140)) for k in levels if rng.random() > 0.3}
        govs.append({"gov_code": g, "gov": g, "band": engine.CITY_BANDS.get(g, "غير محدد"), "students": sum(lv.values()), "levels": lv})
    return {"levels_order": levels, "govs": govs}


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("mode", ["online", "physical"])
def test_group_codes_invariants(seed, mode, monkeypatch):
    monkeypatch.setattr(gc, "gov_codes", lambda: dict(gc.GOV3_DEFAULT))
    rng = np.random.default_rng(seed)
    rep = random_report(rng)
    cap = int(rng.choice([15, 20, 25, 30]))
    r = gc.generate(rep, mode, cap)
    rows = r["rows"]
    pat = ON if mode == "online" else PH
    assert all(pat.match(x["code"]) for x in rows)
    assert len({x["code"] for x in rows}) == len(rows)                       # كل كود مميز
    assert all(0 < x["students"] <= cap for x in rows)                         # الجروب مش فاضي ومش أكبر من السعة
    # الطلاب المتوقعين = كل الطلاب المعروف ليفيلهم (Physical: من غير المستبعدين)
    known = [k for k in rep["levels_order"] if k != "غير محدد"]
    if mode == "online":
        exp = sum(g["levels"].get(k, 0) for g in rep["govs"] for k in known)
        assert r["excluded"] == []
    else:
        ok = [g for g in rep["govs"] if g["gov_code"] in gc.GOV3_DEFAULT and g["band"] != engine.BAND_2]
        exp = sum(g["levels"].get(k, 0) for g in ok for k in known)
        assert sum(x["students"] for x in r["excluded"]) == sum(
            g["levels"].get(k, 0) for g in rep["govs"] if g not in ok for k in known if g["levels"].get(k))
        assert {x["gov_code"] for x in rows} <= set(gc.GOV3_DEFAULT)
    assert r["summary"]["students"] == exp == sum(x["students"] for x in rows)
    # كل (محافظة؟، ليفيل): أقل عدد جروبات، الأحجام متساوية ±1، الترقيم C001..Cn متصل، والأيام نص ونص ±1
    key = (lambda x: x["level_code"]) if mode == "online" else (lambda x: (x["gov3"], x["level_code"]))
    by = {}
    for x in rows:
        by.setdefault(key(x), []).append(x)
    for k, grp in by.items():
        n = sum(x["students"] for x in grp)
        assert len(grp) == -(-n // cap)
        sizes = [x["students"] for x in grp]
        assert max(sizes) - min(sizes) <= 1
        assert [int(x["class"][1:]) for x in grp] == list(range(1, len(grp) + 1))
        days = [x["day"] for x in grp]
        assert abs(days.count("A") - days.count("B")) <= 1
        assert days == sorted(days)                                         # الجمعة الأول وبعدين السبت
    lv = r["summary"]["levels"]
    assert all(abs(x["A"] - x["B"]) <= 1 for x in lv)
    d = {x["day"]: x["groups"] for x in r["summary"]["days"]}
    assert abs(d["A"] - d["B"]) <= 1 and d["A"] + d["B"] == len(rows)


def test_group_codes_round_trip_into_engine_level_keys():
    """الكود اللي بيتولد لازم المحرك يفهمه ويرجّع نفس الليفيل."""
    rep = {"levels_order": ["L1", "L3CY", "L4DT"], "govs": [{"gov_code": "cairo", "gov": "القاهرة", "band": engine.BAND_1,
                                                              "levels": {"L1": 30, "L3CY": 3, "L4DT": 60}}]}
    for mode in ("online", "physical"):
        for x in gc.generate(rep, mode, 25)["rows"]:
            assert engine.group_key(x["code"]) == x["level_code"]


def test_group_codes_empty_inputs():
    assert gc.generate(None, "online", 25)["rows"] == []
    assert gc.generate({"levels_order": ["L1"], "govs": []}, "online", 0)["rows"] == []
    assert gc.generate({"levels_order": ["L1"], "govs": []}, "weird", 25)["mode"] == "physical"


def test_split_level_and_sizes():
    assert gc.split_level("L10EM") == ("L10", "EM") and gc.split_level("غير محدد") == (None, None)
    assert gc._sizes(53, 3) == [18, 18, 17] and gc._sizes(5, 0) == []


def test_gov3_codes_from_centers_table(app, monkeypatch):
    from distributor import db
    con = db.connect()
    try:
        for code, gov in [("PLX_CAI_C01", "cairo"), ("PLX_CAI_C02", "cairo"), ("PLX_ALX_C01", "alexandria"),
                          ("PLX_QNA_C01", "qena"), ("PLX_ZZZ_C01", "suez"), ("PLX_ZZZ_C02", "ismailia")]:
            con.execute("INSERT OR REPLACE INTO centers(code, gov) VALUES (?,?)", (code, gov))
        con.commit()
        codes = gc.gov_codes()
        assert codes["cairo"] == "CAI" and codes["alexandria"] == "ALX" and codes["qena"] == "QNA"
        # نفس الكود لمحافظتين = غلط في أكواد المراكز → الكود الافتراضي
        assert codes["suez"] == "SWS" and codes["ismailia"] == "ISM"
    finally:
        con.execute("DELETE FROM centers WHERE code LIKE 'PLX_%'")
        con.commit()
        con.close()


def test_codes_excel_sheets():
    rep = {"levels_order": ["L1"], "govs": [{"gov_code": "giza", "gov": "الجيزة", "band": engine.BAND_2, "levels": {"L1": 10}},
                                            {"gov_code": "cairo", "gov": "القاهرة", "band": engine.BAND_1, "levels": {"L1": 30}}]}
    xl = pd.ExcelFile(gc.to_excel(gc.generate(rep, "physical", 25)))
    assert set(xl.sheet_names) == {"ملخص", "أكواد الجروبات", "الليفيلات × الأيام", "المحافظات", "مستبعد"}
    assert pd.read_excel(xl, "مستبعد")["الطلاب"].tolist() == [10]
    xl = pd.ExcelFile(gc.to_excel(gc.generate(rep, "online", 25)))
    assert "المحافظات" not in xl.sheet_names and len(pd.read_excel(xl, "أكواد الجروبات")) == 2


def test_planning_api_validation(client):
    r = client.post("/api/planning/analyze", data={}, content_type="multipart/form-data")
    assert r.status_code == 400
    r = client.post("/api/planning/analyze", data={"students": (io.BytesIO(b"\x00\x01garbage"), "s.xlsx")},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "error" in r.get_json()
    csv = "Student NID,gov,Area,Level(25-26)\n30000000000001,القاهرة,x,Level (1)\n".encode("utf-8-sig")
    r = client.post("/api/planning/analyze", data={"students": (io.BytesIO(csv), "s.csv"), "cap": "9999", "mode": "ONLINE"},
                    content_type="multipart/form-data")
    j = r.get_json()
    assert r.status_code == 200 and j["cap"] is None and j["mode"] == "online"         # سعة برا 1..500 بتتجاهل
    pid = j["id"]
    assert client.post(f"/api/planning/{pid}", json={"cap": 30, "mode": "physical", "name": "  "}).get_json() == \
        {"ok": True, "cap": 30, "mode": "physical", "name": "s.csv"}
    assert client.get(f"/api/planning/{pid}").get_json()["cap"] == 30
    assert client.delete(f"/api/planning/{pid}").status_code == 200
    assert client.get(f"/api/planning/{pid}").status_code == 404
