import numpy as np
import pandas as pd

from distributor.services import preflight, sheets


def test_adapt_student_db_sheet():
    S = pd.DataFrame({"id": ["1", "2"], "full_name": ["a", "b"], "phone": ["1", "2"], "national_id": ["31002281400186", "31104181200446"],
                      "city": ["cairo", "dakahlia"], "area": ["المعادي", "None"], "address": ["شارع 9", None],
                      "level": ["forth_cyber_security", "second"], "group": ["ON-BH-L4CY-G1689", "ON-CA-L2-G0762"]})
    out, note = sheets.adapt_students(S)
    assert list(out.columns)[:5] == ["Student NID", "gov", "Area", "Adress", "Level(25-26)"]
    assert out["gov"].tolist() == ["القاهره", "الدقهليه"]
    assert out["Area"].tolist() == ["المعادي", ""] and out["Adress"].tolist() == ["شارع 9", ""]
    assert out["Level(25-26)"].tolist() == ["Level (4): cyber", "Level (2)"]
    assert out["old_group"].tolist() == ["ON-BH-L4CY-G1689", "ON-CA-L2-G0762"]
    assert "phone" not in out.columns and "full_name" not in out.columns
    assert "1 طالب من غير منطقة" in note


def test_adapt_leaves_standard_sheet_untouched():
    S = pd.DataFrame({"Student NID": ["1"], "gov": ["القاهرة"], "Area": ["x"], "Adress": ["y"], "Level(25-26)": ["Level (1)"]})
    out, note = sheets.adapt_students(S)
    assert out is S and note is None


def test_level_from_level_name_when_no_group():
    S = pd.DataFrame({"national_id": ["3"], "city": ["cairo"], "level": ["third_web"]})
    out, _ = sheets.adapt_students(S)
    assert out["Level(25-26)"].iloc[0] == "Level (3): web"


def test_preflight_detects_problems():
    S = pd.DataFrame({"Student NID": ["31002281400186", "31002281400186", "31002281400187"], "gov": ["القاهرة", "القاهرة", "كوكب"],
                      "Area": ["المعادي", "", ""], "Adress": ["a", "", ""], "Level(25-26)": ["Level (1)", "Level (9)", "x"]})
    C = pd.DataFrame({"Group Code": ["L1_0001", "bad"], "Center Code": ["C1", "C1"], "City": ["cairo", "cairo"], "Area": ["المعادي"] * 2,
                      "سعة القاعة": ["20", "0"], "Latitude": ["30.0", ""], "Longitude": ["31.0", ""]})
    rep = preflight.preflight(S, C)
    st, ct = rep["students"], rep["centers"]
    assert st["nid_invalid"] == 0 and st["nid_dup"] == 1 and st["unknown_gov_count"] == 1 and st["no_level"] == 1
    assert ct["bad_group_codes"] == 1 and ct["no_capacity"] == 1 and ct["centers"] == 1 and ct["seats"] == 20
    assert rep["ok"] is True
    assert any(x["level_code"] == "L9" and x["seats"] == 0 for x in rep["cross"])
    # أرقام قومية بايظة (أكتر من 1%) = خطأ بيمنع التشغيل
    S.loc[:, "Student NID"] = ["3.1E+13", "3.1E+13", "3.1E+13"]
    rep = preflight.preflight(S, C)
    assert rep["ok"] is False and rep["students"]["nid_invalid"] == 3


def test_preflight_missing_columns_is_error():
    S = pd.DataFrame({"Student NID": ["1"], "Level(25-26)": ["Level (1)"]})
    C = pd.DataFrame({"Center Code": ["C1"]})
    rep = preflight.preflight(S, C)
    assert rep["ok"] is False and any(i["level"] == "error" for i in rep["issues"])
