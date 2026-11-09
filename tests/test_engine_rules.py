"""محرك التوزيع: كل قاعدة لوحدها + اختبارات خصائص (invariants) على بيانات عشوائية + وضع OSRM بسيرفر وهمي.

القواعد اللي بنتأكد منها (README «قواعد التوزيع»):
  R1 المحافظة إلزامية       R2 نفس المنطقة أولًا ثم الأقرب      R3 نفس الليفيل والتراك بالظبط
  R4 أقصى مسافة / VP بسبب   R5 طالب واحد في جروب واحد، والسعة ما تتعداش
"""
import io
import json
import math

import numpy as np
import pandas as pd
import pytest

from distributor.core import engine

GOV_CENTER = {"cairo": (30.05, 31.24, "القاهرة"), "alexandria": (31.20, 29.92, "الإسكندرية"), "giza": (30.00, 31.20, "الجيزة")}


# ---------------------------------------------------------------- helpers
def centers_df(rows):
    """rows: (group_code, center_code, area, city, cap, lat, lon)"""
    return pd.DataFrame([{"Group Code": g, "Center Code": c, "Area": a, "City": city, "Day": "Friday", "Time": "10:00",
                          "Center Name": f"name {c}", "سعة القاعة": cap, "Latitude": la, "Longitude": lo}
                         for g, c, a, city, cap, la, lo in rows])


def students_df(rows, start=0):
    """rows: (gov_ar, area, level_text, lat, lon)"""
    return pd.DataFrame([{"Student NID": f"3{start + i:013d}", "gov": g, "Area": a, "Adress": "", "Level(25-26)": lv,
                          "Latitude": la, "Longitude": lo} for i, (g, a, lv, la, lo) in enumerate(rows)])


def run(S, C, **kw):
    kw.setdefault("road_mode", "factor")
    kw.setdefault("detour", 1.0)
    kw.setdefault("use_cache", False)
    return engine.distribute(S, C, **kw)


def by_nid(r):
    return r["students"].set_index("Student NID")


def km(a, b, c, d):
    return float(engine.haversine(a, b, c, d))


# ---------------------------------------------------------------- name/level/code parsing
@pytest.mark.parametrize("name,code", [
    ("القاهرة", "cairo"), ("القاهره", "cairo"), ("  القاهرة ", "cairo"), ("محافظة القاهرة", "cairo"),
    ("Cairo", "cairo"), ("CAIRO", "cairo"), ("Cairo Governorate", "cairo"),
    ("الإسكندرية", "alexandria"), ("الاسكندريه", "alexandria"), ("اسكندرية", "alexandria"), ("Alexandria", "alexandria"),
    ("بورسعيد", "port_said"), ("بور سعيد", "port_said"), ("Port Said", "port_said"), ("port_said", "port_said"),
    ("كفر الشيخ", "kafr_el_sheikh"), ("كفرالشيخ", "kafr_el_sheikh"), ("Kafr El Sheikh", "kafr_el_sheikh"),
    ("بني سويف", "beni_suef"), ("بنى سويف", "beni_suef"), ("مرسى مطروح", "matrouh"), ("مطروح", "matrouh"),
    ("جنوب سيناء", "south_sinai"), ("جنوب سينا", "south_sinai"), ("الأقصر", "luxor"), ("أسوان", "aswan"),
    ("منيا", "minya"), ("جيزة", "giza"), ("الوادى الجديد", "new_valley"),
])
def test_gov_code_variants(name, code):
    assert engine.gov_code(name) == code


@pytest.mark.parametrize("name", ["", None, float("nan"), "كوكب", "6 اكتوبر", "x", "غير معروفة", "123"])
def test_gov_code_unknown(name):
    assert engine.gov_code(name) is None
    assert engine.get_gov_band(name) == "غير محدد"


def test_bands_cover_all_governorates_exactly_once():
    codes = set(engine.GOV_MAP.values())
    assert codes == set(engine.CITY_BANDS), "كل محافظة في GOV_MAP لازم ليها بند والعكس"
    per_band = {}
    for c, b in engine.CITY_BANDS.items():
        per_band.setdefault(b, set()).add(c)
    assert {b: len(v) for b, v in per_band.items()} == {engine.BAND_1: 9, engine.BAND_2: 9, engine.BAND_3: 9}
    # القوايم العربية المعروضة متطابقة مع التصنيف
    for band, names in engine.BAND_GOVS_AR.items():
        assert {engine.gov_code(n) for n in names} == per_band[band]


def test_band_lookup_agrees_with_engine_resolution():
    """البند والمحافظة لازم يتحسبوا بنفس الطريقة (قبل كده «Cairo» كان ليه بند بس المحرك بيعتبره غير معروف)."""
    for name in ["Cairo", "محافظة الجيزة", "بور سعيد", "اسكندرية", "الجيزه"]:
        assert engine.gov_code(name) is not None
        assert engine.get_gov_band(name) == engine.CITY_BANDS[engine.gov_code(name)]


@pytest.mark.parametrize("text,key", [
    ("Level (1)", "L1"), ("Level 2", "L2"), ("level (2)", "L2"), ("Level (3): Web", "L3WB"), ("Level (3): Cyber", "L3CY"),
    ("Level (3) - Cyber Security", "L3CY"), ("Level (4): Data", "L4DT"), ("Level (4): Data Analysis", "L4DT"),
    ("Level (5): Digital Arts", "L5DA"), ("Level (3): Embedded", "L3EM"), ("Level (4): AI", "L4DT"),
    ("L3WB", "L3WB"), ("l3cy", "L3CY"), ("L 4 DT", "L4DT"), ("L1", "L1"), ("Level (10)", "L10"),
])
def test_level_key(text, key):
    assert engine.level_key(text) == key


@pytest.mark.parametrize("text", ["", None, float("nan"), "x", "Level", "المستوى"])
def test_level_key_unknown(text):
    assert engine.level_key(text) is None


def test_level_key_does_not_misread_words_containing_ai():
    assert engine.level_key("Level (2): Main") == "L2"            # «main» فيها ai بس مش كلمة AI


@pytest.mark.parametrize("code,key", [
    ("L1_0001", "L1"), ("L3WB_0003", "L3WB"), ("l3cy_12", "L3CY"), (" L2_9 ", "L2"),
    ("PH-B-CAI-V03-L2-GEN-C008", "L2"), ("ON-A-S1-L4-DT-C010", "L4DT"), ("PH-A-ALX-V01-L3-CY-C001", "L3CY"),
    ("bad", None), ("", None), ("L1-0001", None), ("PH-A-CAI-V01-L2-GEN", None), ("1_0001", None),
])
def test_group_key(code, key):
    assert engine.group_key(code) == key


def test_level_and_group_key_agree_for_every_track():
    """كود الجروب والليفيل لازم يطلعوا نفس المفتاح وإلا الطالب عمره ما هيتوزع على الجروب ده."""
    for n in range(1, 6):
        for word, tr in [("", ""), ("Cyber", "CY"), ("Data", "DT"), ("Web", "WB"), ("Digital", "DA"), ("Embedded", "EM")]:
            lv = f"Level ({n})" + (f": {word}" if word else "")
            assert engine.level_key(lv) == engine.group_key(f"L{n}{tr}_0001") \
                == engine.group_key(f"PH-A-CAI-V01-L{n}-{tr or 'GEN'}-C001")


def test_area_key_equivalences():
    k = engine.area_key
    assert k("حي شرق") == k("الشرق حي") == k("شرق")
    assert k("المندرة") == k("المندره")
    assert k("مصر الجديدة") == k("مصر الجديده") != k("مصر القديمة")
    assert k("") == frozenset() and k(None) == frozenset()


def test_haversine_known_distance():
    d = km(30.0444, 31.2357, 31.2001, 29.9187)         # القاهرة ↔ الإسكندرية ≈ 180 كم خط مستقيم
    assert 175 < d < 185
    assert km(30, 31, 30, 31) == 0


# ---------------------------------------------------------------- single rules
def test_R1_governorate_is_mandatory_even_if_other_gov_center_is_closer():
    C = centers_df([("L1_0001", "GIZ_C1", "الدقي", "giza", 10, 30.00, 31.20),
                    ("L1_0002", "CAI_C1", "وسط", "cairo", 10, 30.20, 31.40)])
    S = students_df([("القاهرة", "وسط", "Level (1)", 30.001, 31.201)])       # جنب مركز الجيزة بالظبط
    st = by_nid(run(S, C))
    assert st.iloc[0]["center_code"] == "CAI_C1"


def test_R1_no_center_in_gov_gives_vp_with_reason():
    C = centers_df([("L1_0001", "GIZ_C1", "الدقي", "giza", 10, 30.00, 31.20)])
    st = by_nid(run(students_df([("القاهرة", "x", "Level (1)", 30.0, 31.2)]), C))
    assert st.iloc[0]["status"] == "VP" and "لا يوجد في محافظته" in st.iloc[0]["note"]


def test_R2_same_area_beats_nearer_center():
    C = centers_df([("L1_0001", "C_NEAR", "وسط البلد", "cairo", 5, 30.05, 31.24),
                    ("L1_0002", "C_SAME", "مصر الجديدة", "cairo", 5, 30.25, 31.40)])     # ~25 كم
    st = by_nid(run(students_df([("القاهرة", "مصر الجديده", "Level (1)", 30.05, 31.24)]), C))
    r = st.iloc[0]
    assert r["center_code"] == "C_SAME" and r["same_area"] == "نعم"


def test_R2_same_area_ignored_when_beyond_max_km():
    C = centers_df([("L1_0001", "C_NEAR", "وسط البلد", "cairo", 5, 30.05, 31.24),
                    ("L1_0002", "C_SAME", "مصر الجديدة", "cairo", 5, 30.60, 31.24)])     # ~61 كم
    st = by_nid(run(students_df([("القاهرة", "مصر الجديدة", "Level (1)", 30.05, 31.24)]), C, max_km=50))
    assert st.iloc[0]["center_code"] == "C_NEAR" and st.iloc[0]["same_area"] == "لا"


def test_R2_nearest_wins_when_no_same_area():
    C = centers_df([("L1_0001", "C_FAR", "a", "cairo", 5, 30.20, 31.24),
                    ("L1_0002", "C_NEAR", "b", "cairo", 5, 30.06, 31.24)])
    st = by_nid(run(students_df([("القاهرة", "z", "Level (1)", 30.05, 31.24)]), C))
    assert st.iloc[0]["center_code"] == "C_NEAR"
    assert st.iloc[0]["distance_km"] == pytest.approx(round(km(30.05, 31.24, 30.06, 31.24), 1))


def test_R3_track_must_match_exactly():
    C = centers_df([("L3_0001", "C1", "a", "cairo", 5, 30.05, 31.24), ("L3CY_0002", "C1", "a", "cairo", 5, 30.05, 31.24)])
    S = students_df([("القاهرة", "a", "Level (3): Web", 30.05, 31.24), ("القاهرة", "a", "Level (3): Cyber", 30.05, 31.24),
                     ("القاهرة", "a", "Level (3)", 30.05, 31.24)])
    st = by_nid(run(S, C))
    assert list(st["status"]) == ["VP", "تم التوزيع", "تم التوزيع"]
    assert "L3WB" in st.iloc[0]["note"]
    assert st.iloc[1]["group_code"] == "L3CY_0002" and st.iloc[2]["group_code"] == "L3_0001"


@pytest.mark.parametrize("detour", [1.0, 1.3])
def test_R4_max_km_boundary(detour):
    """المسافة = خط مستقيم × المعامل. أقل من أو يساوي الحد = يتوزع، أكبر = VP بأقرب مركز ومسافته."""
    C = centers_df([("L1_0001", "C1", "a", "cairo", 5, 30.00, 31.00)])
    d_in = 0.40 / detour        # درجات عرض تقريبًا
    S = students_df([("القاهرة", "z", "Level (1)", 30.00 + d_in, 31.00), ("القاهرة", "z", "Level (1)", 30.00 + 0.50 / detour, 31.00)])
    st = by_nid(run(S, C, max_km=50, detour=detour))
    near, far = st.iloc[0], st.iloc[1]
    assert near["status"] == "تم التوزيع" and near["distance_km"] <= 50
    assert far["status"] == "VP" and "أبعد من 50 كم" in far["note"] and far["nearest_center"] == "C1"
    assert far["distance_km"] == pytest.approx(round(km(30.0 + 0.5 / detour, 31, 30, 31) * detour, 1))


def test_R4_exact_boundary_is_inclusive():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 5, 30.00, 31.00)])
    S = students_df([("القاهرة", "z", "Level (1)", 30.10, 31.00)])
    d = km(30.10, 31.0, 30.0, 31.0)
    st = by_nid(run(S, C, max_km=d))           # الحد = المسافة بالظبط
    assert st.iloc[0]["status"] == "تم التوزيع"


def test_R5_capacity_and_even_split_between_groups_of_a_center():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 4, 30.05, 31.24), ("L1_0002", "C1", "a", "cairo", 4, 30.05, 31.24)])
    S = students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)] * 10)
    r = run(S, C)
    st = r["students"]
    assert (st["status"] == "تم التوزيع").sum() == 8
    assert st.loc[st.status != "VP", "group_code"].value_counts().to_dict() == {"L1_0001": 4, "L1_0002": 4}
    assert set(st.loc[st.status == "VP", "note"]) == {"كل مراكز المحافظة داخل 50 كم ممتلئة - مقترح VP"}
    # جروبين بسعة مختلفة: الطلاب بيروحوا للي فاضي فيه أكتر (توزيع متساوي)
    C2 = centers_df([("L1_0001", "C1", "a", "cairo", 6, 30.05, 31.24), ("L1_0002", "C1", "a", "cairo", 2, 30.05, 31.24)])
    st2 = run(students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)] * 4), C2)["students"]
    assert st2["group_code"].value_counts().to_dict() == {"L1_0001": 4}


def test_zero_or_bad_capacity_never_receives_students():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 0, 30.05, 31.24), ("L1_0002", "C1", "a", "cairo", "abc", 30.05, 31.24),
                    ("L1_0003", "C1", "a", "cairo", -3, 30.05, 31.24)])
    st = run(students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)]), C)["students"]
    assert st.iloc[0]["status"] == "VP"


def test_regression_top_centers_cutoff_no_false_full_vp():
    """BUG اتصلّح: الطالب كان بيشوف أقرب 15 مركز بس؛ لو اتملوا بيطلع VP «كل المراكز ممتلئة» وفيه مقعد فاضي في حدود المسافة."""
    n = engine.TOP_CENTERS + 3
    C = centers_df([(f"L1_{k:04d}", f"C{k:02d}", f"z{k}", "cairo", 1, 30.0 + 0.01 * k, 31.2) for k in range(n)])
    S = students_df([("القاهرة", "x", "Level (1)", 30.0, 31.2)] * n)
    r = run(S, C)
    assert r["summary"]["assigned"] == n and r["summary"]["vp"] == 0
    assert r["load"]["assigned"].tolist() == [1] * n


def test_regression_city_with_spaces_counts_seats_in_deficit_report():
    """BUG اتصلّح: City = 'cairo ' (بمسافة) كان بيخلي المقاعد صفر في تقرير العجز."""
    C = centers_df([("L1_0001", "C1", "a", " Cairo ", 2, 30.05, 31.23)])
    r = run(students_df([("القاهرة", "a", "Level (1)", 30.05, 31.23)] * 3), C)
    d = r["deficit"].iloc[0]
    assert r["summary"]["assigned"] == 2
    assert (d["seats_in_gov"], d["deficit"], d["vp"], d["extra_groups_needed"]) == (2, 1, 1, 1)


def test_regression_english_and_prefixed_gov_names_are_distributed():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 10, 30.05, 31.23), ("L1_0002", "P1", "a", "port_said", 10, 31.26, 32.30)])
    S = students_df([("Cairo", "a", "Level (1)", 30.05, 31.23), ("محافظة القاهرة", "a", "Level (1)", 30.05, 31.23),
                     ("بور سعيد", "a", "Level (1)", 31.26, 32.30)])
    st = run(S, C)["students"]
    assert st["status"].tolist() == ["تم التوزيع"] * 3
    assert st["البند"].tolist() == [engine.BAND_1] * 3


# ---------------------------------------------------------------- VP reasons & data quality
def test_every_vp_reason():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 1, 30.05, 31.24), ("L2_0002", "A1", "a", "alexandria", 5, 31.2, 29.92)])
    S = students_df([
        ("القاهرة", "a", "Level (1)", None, None),               # 0 مفيش إحداثيات
        ("القاهرة", "a", "مش ليفيل", 30.05, 31.24),              # 1 ليفيل مش مفهوم
        ("كوكب", "a", "Level (1)", 30.05, 31.24),                 # 2 محافظة مش معروفة
        ("القاهرة", "a", "Level (7)", 30.05, 31.24),              # 3 مفيش جروب L7 خالص
        ("القاهرة", "a", "Level (2)", 30.05, 31.24),              # 4 L2 موجود بس في الإسكندرية
        ("القاهرة", "a", "Level (1)", 31.20, 29.92),              # 5 أبعد من 50 كم
        ("القاهرة", "a", "Level (1)", 30.05, 31.24),              # 6 ياخد المقعد الوحيد
        ("القاهرة", "a", "Level (1)", 30.051, 31.241),            # 7 ممتلئ
    ])
    st = run(S, C)["students"]
    notes = st["note"].tolist()
    assert "إحداثيات ناقصة" in notes[0] and "الليفيل غير معروف" in notes[1] and "المحافظة غير معروف" in notes[2]
    assert "لا يوجد في أي مركز جروب L7" in notes[3] and "لا يوجد في محافظته أي مركز به L2" in notes[4]
    assert "أبعد من 50 كم" in notes[5] and "(C1)" in notes[5]
    assert st.iloc[6]["status"] == "تم التوزيع" and notes[6] == ""
    assert "ممتلئة" in notes[7]
    assert (st["status"] == "VP").sum() == 7
    # كل VP ليه سبب، وكل متوزع مالوش سبب
    assert (st.loc[st.status == "VP", "note"] != "").all() and (st.loc[st.status != "VP", "note"] == "").all()


def test_missing_columns_raise_clear_errors():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 1, 30.05, 31.24)])
    S = students_df([("القاهرة", "a", "Level (1)", 30.0, 31.0)])
    with pytest.raises(ValueError, match="شيت الطلاب ناقص"):
        run(S.drop(columns=["gov"]), C)
    with pytest.raises(ValueError, match="شيت الطلاب ناقص"):
        run(S.drop(columns=["Latitude"]), C)
    with pytest.raises(ValueError, match="سعة القاعة"):
        run(S, C.drop(columns=["سعة القاعة"]))
    with pytest.raises(ValueError, match="Group Code"):
        run(S, C.drop(columns=["Group Code"]))
    with pytest.raises(ValueError, match="مفيهوش جروبات صالحة"):
        run(S, C.assign(**{"Group Code": "bad"}))


def test_alternative_column_names_are_accepted():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 5, 30.05, 31.24)]).rename(columns={"Latitude": "lat", "Longitude": "lng",
                                                                                         "سعة القاعة": "Capacity"})
    S = students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)]).rename(
        columns={"gov": "المحافظة", "Area": "المنطقة", "Level(25-26)": "Level 2025", "Latitude": "خط العرض", "Longitude": "خط الطول"})
    st = run(S, C)["students"]
    assert st.iloc[0]["status"] == "تم التوزيع" and "البند" in st.columns


def test_bad_group_codes_and_centers_without_coords_are_skipped_with_warning():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 5, 30.05, 31.24), ("weird", "C1", "a", "cairo", 5, 30.05, 31.24),
                    ("L1_0002", "C2", "a", "cairo", 5, None, None)])
    r = run(students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)]), C)
    w = " ".join(r["summary"]["warnings"])
    assert "1 جروب كوده مش بصيغة" in w and "1 مركز مالهمش إحداثيات" in w and "C2" in w
    assert r["summary"]["groups"] == 1 and r["summary"]["centers"] == 1


def test_coordinates_on_first_room_only_apply_to_all_rooms():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 1, 30.05, 31.24), ("L1_0002", "C1", "a", "cairo", 1, None, None)])
    r = run(students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)] * 2), C)
    assert r["summary"]["assigned"] == 2 and r["summary"]["seats"] == 2


def test_nid_quality_alerts():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 500, 30.05, 31.24)])
    S = students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)] * 200)
    S.loc[0, "Student NID"] = "123"                                  # 1 من 200 (≤1%) = تحذير
    r = run(S, C)
    assert any("رقم قومي مش 14 رقم" in w and "row_no = 2" in w for w in r["summary"]["warnings"])
    assert not any("الرقم القومي مش سليم" in a for a in r["summary"]["alerts"])
    S["Student NID"] = "3.10033E+13"                                  # كله بايظ = إنذار
    r = run(S, C)
    assert any("الرقم القومي مش سليم" in a for a in r["summary"]["alerts"])
    # الرقم القومي بيتنضّف من .0
    S["Student NID"] = [f"3{i:013d}.0" for i in range(200)]
    assert run(S, C)["students"]["Student NID"].str.fullmatch(r"\d{14}").all()


def test_suspicious_center_coordinates_are_reported():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 1, 30.05, 31.24), ("L1_0002", "C2", "a", "cairo", 1, 30.05, 31.24),
                    ("L1_0003", "C3", "a", "cairo", 1, 30.10, 31.20), ("L1_0004", "C4", "a", "cairo", 1, 30.5, 30.5)])
    r = run(students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)]), C)
    sc = r["sus_centers"].set_index("center_code")["problem"].to_dict()
    assert "نفس الإحداثيات مع C2" in sc["C1"] and "نفس الإحداثيات مع C1" in sc["C2"]
    assert "خط العرض = خط الطول" in sc["C4"] and "C3" not in sc


def test_suspicious_student_far_from_gov_peers_is_flagged():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 500, 30.05, 31.24)])
    S = students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)] * 20 + [("القاهرة", "a", "Level (1)", 25.0, 33.0)])
    r = run(S, C)
    st = r["students"]
    assert st.iloc[-1]["coord_suspect"] == "نعم" and "غالبًا إحداثيات الطالب غلط" in st.iloc[-1]["note"]
    assert (st["coord_suspect"] == "نعم").sum() == 1 == r["summary"]["suspect"] == len(r["sus_students"])


def test_band2_students_are_flagged_and_band_column_follows_gov():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 10, 30.05, 31.24)])
    S = students_df([("الجيزة", "a", "Level (1)", 30.0, 31.2), ("القاهرة", "a", "Level (1)", 30.05, 31.24)])
    r = run(S, C)
    cols = list(r["students"].columns)
    assert cols[cols.index("gov") + 1] == "البند"
    assert r["summary"]["band2_count"] == 1 and r["summary"]["alerts"][0].startswith("⚠️ تنبيه البنود")


def test_deficit_and_extra_groups_use_most_common_capacity_per_level():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 20, 30.05, 31.24), ("L1_0002", "C1", "a", "cairo", 20, 30.05, 31.24),
                    ("L1_0003", "C2", "b", "cairo", 15, 30.06, 31.25), ("L2_0004", "C1", "a", "cairo", 30, 30.05, 31.24)])
    S = students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)] * 100 + [("القاهرة", "a", "Level (2)", 30.05, 31.24)] * 31)
    d = run(S, C)["deficit"].set_index("level_code")
    assert d.loc["L1", "deficit"] == 45 and d.loc["L1", "group_capacity"] == 20 and d.loc["L1", "extra_groups_needed"] == 3
    assert d.loc["L2", "deficit"] == 1 and d.loc["L2", "group_capacity"] == 30 and d.loc["L2", "extra_groups_needed"] == 1


def test_reports_are_consistent_with_student_rows():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 3, 30.05, 31.24), ("L2_0002", "A1", "a", "alexandria", 3, 31.2, 29.92)])
    S = students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)] * 5 + [("الإسكندرية", "a", "Level (2)", 31.2, 29.92)] * 2)
    r = run(S, C)
    s, st = r["summary"], r["students"]
    assert s["students"] == 7 and s["assigned"] + s["vp"] == 7 and s["assigned"] == 5 and s["seats"] == 6
    assert r["by_gov"]["students"].sum() == 7 and r["by_gov"]["assigned"].sum() == 5
    assert r["load"]["assigned"].sum() == 5 == r["center_load"]["assigned"].sum()
    assert sum(x["count"] for x in s["vp_reasons"]) == s["vp"]
    assert len(r["lines"]) == 5 and r["lines"]["wkt"].str.startswith("LINESTRING(").all()
    assert st["row_no"].tolist() == list(range(2, 9))
    assert s["avg_km"] == pytest.approx(round(st.loc[st.status != "VP", "distance_km"].mean(), 1))


def test_deterministic_output():
    rng = np.random.default_rng(1)
    S, C = random_scenario(rng, 400, 12)
    a, b = run(S, C)["students"], run(S, C)["students"]
    pd.testing.assert_frame_equal(a, b)


def test_progress_callback_and_cancel():
    C = centers_df([("L1_0001", "C1", "a", "cairo", 3, 30.05, 31.24)])
    S = students_df([("القاهرة", "a", "Level (1)", 30.05, 31.24)])
    seen = []
    run(S, C, progress=lambda p, m: seen.append(p))
    assert seen == sorted(seen) and seen[0] >= 0 and seen[-1] <= 100

    def cancel(p, m):
        if p > 10:
            raise engine.JobCancelled()
    with pytest.raises(engine.JobCancelled):
        run(S, C, progress=cancel)


# ---------------------------------------------------------------- property tests (random scenarios)
LEVELS = ["Level (1)", "Level (2)", "Level (3): Web", "Level (3): Cyber", "Level (4): Data"]
AREAS = ["وسط", "شرق", "غرب", "حي الشرق", "المنتزه", "المندرة", "المندره", "الدقي", ""]


def random_scenario(rng, n_students, n_centers):
    govs = list(GOV_CENTER)
    crow = []
    for k in range(n_centers):
        g = govs[k % len(govs)]
        la, lo, _ = GOV_CENTER[g]
        cla, clo = la + rng.normal(0, 0.15), lo + rng.normal(0, 0.15)
        area = AREAS[rng.integers(len(AREAS) - 1)]
        for r in range(rng.integers(1, 4)):
            lv = engine.level_key(LEVELS[rng.integers(len(LEVELS))])
            crow.append((f"{lv}_{k:03d}{r}", f"C{k:03d}", area, g, int(rng.integers(0, 6)), cla if r == 0 else None,
                         clo if r == 0 else None))
    srow = []
    for _ in range(n_students):
        g = govs[rng.integers(len(govs))]
        la, lo, ar = GOV_CENTER[g]
        u = rng.random()
        lv = LEVELS[rng.integers(len(LEVELS))] if u > 0.03 else "??"
        srow.append((ar, AREAS[rng.integers(len(AREAS))], lv, la + rng.normal(0, 0.3), lo + rng.normal(0, 0.3)))
    return students_df(srow), centers_df(crow)


@pytest.mark.parametrize("seed", range(12))
def test_invariants_on_random_scenarios(seed):
    rng = np.random.default_rng(seed)
    S, C = random_scenario(rng, 600, int(rng.integers(6, 40)))
    max_km = float(rng.choice([15, 30, 50]))
    detour = float(rng.choice([1.0, 1.3]))
    r = engine.distribute(S, C, max_km=max_km, road_mode="factor", detour=detour, use_cache=False)
    st, load = r["students"], r["load"].copy()
    ok = st["status"] == "تم التوزيع"
    cen = load.drop_duplicates("center_code").set_index("center_code")
    akey = {c: engine.area_key(a) for c, a in cen["area"].items()}

    # R5: السعة ما تتعداش، وعدد الطلاب في كل جروب = assigned في تقرير الإشغال
    counts = st.loc[ok, "group_code"].value_counts()
    assert (load["assigned"] <= load["capacity"]).all() and (load["assigned"] >= 0).all()
    assert counts.to_dict() == load.loc[load.assigned > 0].set_index("group_code")["assigned"].to_dict()
    assert r["summary"]["assigned"] + r["summary"]["vp"] == len(S)

    g_of = load.set_index("group_code")
    for _, s in st[ok].iterrows():
        c = cen.loc[s["center_code"]]
        assert c["gov"] == s["gov_code"]                                          # R1
        assert g_of.loc[s["group_code"], "level_code"] == s["level_code"]         # R3
        d = round(km(float(s["Latitude"]), float(s["Longitude"]), c["lat"], c["lon"]) * detour, 1)
        assert s["distance_km"] == pytest.approx(d, abs=0.051) and d <= max_km + 0.05   # R4
        assert (s["same_area"] == "نعم") == (engine.area_key(s["Area"]) == akey[s["center_code"]])

    # المقاعد الفاضية في الآخر لكل (مركز، ليفيل)
    left = (load.assign(left=load.capacity.clip(lower=0) - load.assigned)
            .groupby(["center_code", "level_code"])["left"].sum())
    free = {k: v for k, v in left.items() if v > 0}

    def options(s):
        """كل المراكز اللي فيها مقعد فاضي لليفيل الطالب جوه محافظته وجوه المسافة: [(tier, dist)]"""
        out = []
        for (cc, lv), _n in free.items():
            c = cen.loc[cc]
            if lv != s["level_code"] or c["gov"] != s["gov_code"]:
                continue
            d = km(float(s["Latitude"]), float(s["Longitude"]), c["lat"], c["lon"]) * detour
            if d <= max_km:
                out.append((int(engine.area_key(s["Area"]) != akey[cc]), d))
        return out

    for _, s in st.iterrows():
        if s["status"] == "VP" and "ممتلئة" in s["note"]:
            # سبب «ممتلئة» لازم يكون صحيح: مفيش ولا مقعد فاضي ينفعه
            assert options(s) == [], f"VP ممتلئة وفيه مقاعد: {s['Student NID']}"
        elif s["status"] == "تم التوزيع":
            # الأولوية: مفيش مركز أحسن (نفس المنطقة، أو نفس الدرجة وأقرب) فضل فيه مقعد
            mine = (int(s["same_area"] != "نعم"), float(s["distance_km"]))
            better = [o for o in options(s) if (o[0], round(o[1], 1)) < (mine[0], mine[1] - 0.05)]
            assert not better, f"{s['Student NID']} اتحط في {mine} وفيه {better}"

    # التوزيع المتساوي جوه المركز: الجروب ما ياخدش طالب إلا لو كان الأفضى
    for (cc, lv), grp in load.groupby(["center_code", "level_code"]):
        lefts = (grp.capacity - grp.assigned).tolist()
        for (_, g), lf in zip(grp.iterrows(), lefts):
            if g["assigned"] > 0:
                assert lf + 1 >= max(lefts), (cc, lv, lefts)


# ---------------------------------------------------------------- OSRM mode (fake server, no network)
class FakeOSRM:
    """بيرد على /table زي OSRM: المسافة = خط مستقيم × 1.2، ومسارات معينة بترجع null."""

    def __init__(self, null_dest=None, fail=False, factor=1.2):
        self.calls, self.null_dest, self.fail, self.factor = 0, null_dest, fail, factor

    def __call__(self, url, timeout=None):
        self.calls += 1
        if self.fail:
            raise OSError("connection refused")
        path, _, query = url.partition("?")
        coords = [tuple(map(float, p.split(","))) for p in path.split("/driving/")[1].split(";")]
        q = dict(x.split("=", 1) for x in query.split("&"))
        src = [int(i) for i in q["sources"].split(";")] if "sources" in q else list(range(len(coords)))
        dst = [int(i) for i in q["destinations"].split(";")] if "destinations" in q else list(range(len(coords)))
        rows = []
        for i in src:
            row = []
            for j in dst:
                (lo1, la1), (lo2, la2) = coords[i], coords[j]
                row.append(None if self.null_dest and (round(la2, 4), round(lo2, 4)) == self.null_dest
                           else km(la1, lo1, la2, lo2) * self.factor * 1000)
            rows.append(row)
        return io.BytesIO(json.dumps({"code": "Ok", "distances": rows}).encode())


@pytest.fixture
def fake_osrm(monkeypatch):
    def install(**kw):
        f = FakeOSRM(**kw)
        monkeypatch.setattr(engine.urllib.request, "urlopen", f)
        monkeypatch.setattr(engine.time, "sleep", lambda s: None)
        return f
    return install


def test_osrm_check_messages(fake_osrm):
    fake_osrm()
    ok, msg, _ = engine.osrm_check("127.0.0.1:5001")
    assert ok and "OSRM شغال" in msg
    assert engine.osrm_check("")[0] is False
    fake_osrm(fail=True)
    ok, msg, _ = engine.osrm_check("http://127.0.0.1:5001")
    assert not ok


def test_osrm_matrix_chunks_are_assembled_correctly(fake_osrm):
    fake_osrm()
    rng = np.random.default_rng(3)
    src = np.column_stack([30 + rng.random(23) * 0.5, 31 + rng.random(23) * 0.5])
    dst = np.column_stack([30 + rng.random(9) * 0.5, 31 + rng.random(9) * 0.5])
    m, stat = engine.osrm_matrix("http://x", src, dst, limit=10, workers=3)
    exp = engine.haversine(src[:, None, 0], src[:, None, 1], dst[None, :, 0], dst[None, :, 1]) * 1.2
    assert m.shape == (23, 9) and np.allclose(m, exp, atol=1e-3) and stat["fail"] == 0


def test_osrm_matrix_failing_server_raises_fast(fake_osrm):
    fake_osrm(fail=True)
    src = np.array([[30.0, 31.0]] * 50)
    with pytest.raises(engine.OSRMError):
        engine.osrm_matrix("http://x", src, np.array([[30.1, 31.1]]), limit=4, workers=2)


def test_distribute_with_osrm_uses_road_distances_and_cache(fake_osrm, tmp_path):
    f = fake_osrm()
    C = centers_df([("L1_0001", "C1", "a", "cairo", 10, 30.05, 31.24), ("L1_0002", "C2", "b", "cairo", 10, 30.30, 31.24)])
    S = students_df([("القاهرة", "z", "Level (1)", 30.06, 31.24), ("القاهرة", "z", "Level (1)", 30.29, 31.24)])
    cache = str(tmp_path / "osrm.pkl")
    r1 = engine.distribute(S, C, road_mode="osrm", osrm_url="http://fake", cache_path=cache)
    st = r1["students"]
    assert st["center_code"].tolist() == ["C1", "C2"]
    assert st.iloc[0]["distance_km"] == pytest.approx(round(km(30.06, 31.24, 30.05, 31.24) * 1.2, 1))
    assert r1["summary"]["distance_label"].startswith("مسافة طريق (OSRM)") and r1["summary"]["road_computed"] == 2
    calls = f.calls
    r2 = engine.distribute(S, C, road_mode="osrm", osrm_url="http://fake", cache_path=cache)
    assert r2["summary"]["road_cache_hits"] == 2 and r2["summary"]["road_computed"] == 0
    assert f.calls - calls <= 2                        # فحص الاتصال + البصمة بس، من غير /table للطلاب
    pd.testing.assert_frame_equal(r1["students"], r2["students"])


def test_distribute_osrm_missing_route_falls_back_to_estimate(fake_osrm):
    fake_osrm(null_dest=(30.30, 31.24))
    C = centers_df([("L1_0001", "C2", "b", "cairo", 10, 30.30, 31.24), ("L1_0002", "C1", "a", "cairo", 10, 30.05, 31.24)])
    S = students_df([("القاهرة", "z", "Level (1)", 30.29, 31.24)])
    r = engine.distribute(S, C, road_mode="osrm", osrm_url="http://fake", use_cache=False, detour=1.3)
    st = r["students"].iloc[0]
    assert st["center_code"] == "C2" and st["distance_km"] == pytest.approx(round(km(30.29, 31.24, 30.3, 31.24) * 1.3, 1))
    assert any("اتقدّرت" in w for w in r["summary"]["warnings"])


def test_distribute_osrm_down_raises(fake_osrm):
    fake_osrm(fail=True)
    C = centers_df([("L1_0001", "C1", "a", "cairo", 10, 30.05, 31.24)])
    with pytest.raises(engine.OSRMError):
        engine.distribute(students_df([("القاهرة", "z", "Level (1)", 30.06, 31.24)]), C, road_mode="osrm", osrm_url="http://x")


# ---------------------------------------------------------------- performance smoke
def test_performance_large_sheet():
    """30 ألف طالب × 300 مركز بالتقدير لازم يخلص في وقت معقول (الشيتات الحقيقية ~43 ألف)."""
    import time
    rng = np.random.default_rng(7)
    S, C = random_scenario(rng, 30000, 300)
    t = time.time()
    r = engine.distribute(S, C, max_km=50, road_mode="factor", use_cache=False)
    el = time.time() - t
    assert r["summary"]["students"] == 30000
    assert el < 90, f"التوزيع خد {el:.1f} ث"
