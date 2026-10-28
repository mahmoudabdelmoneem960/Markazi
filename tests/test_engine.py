"""قواعد التوزيع على مثال صغير محسوب يدويًا (تقدير مسافة)."""
import pandas as pd

from distributor.core import engine


def _centers():
    rows = []
    for code, lat, lon, area, groups in (("CAI_C01", 30.05, 31.23, "وسط البلد", [("L1_0001", 2), ("L2_0002", 1)]),
                                         ("CAI_C02", 30.10, 31.30, "مصر الجديدة", [("L1_0003", 1)]),
                                         ("ALX_C01", 31.20, 29.92, "حي وسط", [("L1_0004", 5)])):
        for g, cap in groups:
            rows.append({"Group Code": g, "Center Code": code, "Area": area, "City": "cairo" if code.startswith("CAI") else "alexandria",
                         "Day": "Friday", "Time": "10:00", "Center Name": code, "سعة القاعة": cap, "Latitude": lat, "Longitude": lon})
    return pd.DataFrame(rows)


def _students():
    return pd.DataFrame([
        {"Student NID": "30000000000001", "gov": "القاهرة", "Area": "مصر الجديدة", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 30.06, "Longitude": 31.24},   # نفس منطقة C02 رغم إنه أقرب لـ C01
        {"Student NID": "30000000000002", "gov": "القاهرة", "Area": "وسط البلد", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 30.05, "Longitude": 31.23},
        {"Student NID": "30000000000003", "gov": "القاهرة", "Area": "وسط البلد", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 30.051, "Longitude": 31.231},
        {"Student NID": "30000000000004", "gov": "القاهرة", "Area": "وسط البلد", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 30.052, "Longitude": 31.232},  # C01 L1 ممتلئ -> C02 ممتلئ -> VP
        {"Student NID": "30000000000005", "gov": "القاهرة", "Area": "وسط البلد", "Adress": "", "Level(25-26)": "Level (3): Web", "Latitude": 30.05, "Longitude": 31.23},  # مفيش L3WB
        {"Student NID": "30000000000006", "gov": "القاهرة", "Area": "وسط البلد", "Adress": "", "Level(25-26)": "Level (2)", "Latitude": 31.20, "Longitude": 29.92},  # في الإسكندرية جغرافيًا لكن محافظته القاهرة -> بعيد
        {"Student NID": "30000000000007", "gov": "الإسكندرية", "Area": "حي شرق", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 31.21, "Longitude": 29.93},
        {"Student NID": "30000000000008", "gov": "مجهولة", "Area": "x", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 30.05, "Longitude": 31.23},
    ])


def test_rules():
    r = engine.distribute(_students(), _centers(), max_km=50, road_mode="factor", detour=1.3, use_cache=False)
    st = r["students"].set_index("Student NID")
    assert st.loc["30000000000001", "center_code"] == "CAI_C02" and st.loc["30000000000001", "same_area"] == "نعم"
    assert st.loc["30000000000002", "center_code"] == "CAI_C01" and st.loc["30000000000003", "center_code"] == "CAI_C01"
    assert st.loc["30000000000004", "status"] == "VP" and "ممتلئة" in st.loc["30000000000004", "note"]
    assert st.loc["30000000000005", "status"] == "VP" and "L3WB" in st.loc["30000000000005", "note"]
    assert st.loc["30000000000006", "status"] == "VP" and "أبعد من" in st.loc["30000000000006", "note"]
    assert st.loc["30000000000007", "center_code"] == "ALX_C01"
    assert st.loc["30000000000008", "status"] == "VP" and "المحافظة" in st.loc["30000000000008", "note"]
    s = r["summary"]
    assert s["assigned"] == 4 and s["vp"] == 4 and s["seats"] == 9
    assert any(d["level_code"] == "L1" and d["gov"] == "cairo" and d["deficit"] == 1 for d in r["deficit"].to_dict("records"))


def test_level_and_group_keys():
    assert engine.level_key("Level (3): Web") == "L3WB" and engine.level_key("Level 1") == "L1" and engine.level_key("x") is None
    assert engine.group_key("L3WB_0003") == "L3WB" and engine.group_key("L1_0001") == "L1" and engine.group_key("bad") is None
    assert engine.area_key("حي شرق") == engine.area_key("الشرق حي")


def test_governorate_bands_mapping():
    # البند الأول (9 محافظات)
    b1_govs = ["القاهرة", "السويس", "الإسماعيلية", "بورسعيد", "البحيرة", "بني سويف", "المنيا", "جنوب سيناء", "شمال سيناء"]
    for g in b1_govs:
        assert engine.get_gov_band(g) == engine.BAND_1, f"{g} should be Band 1"

    # البند الثاني (9 محافظات)
    b2_govs = ["الجيزة", "القليوبية", "المنوفية", "قنا", "أسوان", "أسيوط", "الأقصر", "البحر الأحمر", "مرسى مطروح", "مطروح"]
    for g in b2_govs:
        assert engine.get_gov_band(g) == engine.BAND_2, f"{g} should be Band 2"

    # البند الثالث (9 محافظات)
    b3_govs = ["الإسكندرية", "الشرقية", "الدقهلية", "الغربية", "دمياط", "كفر الشيخ", "الفيوم", "سوهاج", "الوادي الجديد"]
    for g in b3_govs:
        assert engine.get_gov_band(g) == engine.BAND_3, f"{g} should be Band 3"


def test_band2_detection_in_distribution():
    students_with_b2 = pd.DataFrame([
        {"Student NID": "30000000000010", "gov": "الجيزة", "Area": "الدقي", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 30.03, "Longitude": 31.21},
        {"Student NID": "30000000000011", "gov": "القاهرة", "Area": "وسط البلد", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 30.05, "Longitude": 31.23},
        {"Student NID": "30000000000012", "gov": "المنوفية", "Area": "شبين الكوم", "Adress": "", "Level(25-26)": "Level (1)", "Latitude": 30.55, "Longitude": 31.01},
    ])
    centers = _centers()
    r = engine.distribute(students_with_b2, centers, max_km=50, road_mode="factor", detour=1.3, use_cache=False)
    st = r["students"]
    assert "البند" in st.columns and "band" in st.columns
    # موقع عمود البند بجانب المحافظة
    cols = list(st.columns)
    assert cols.index("البند") == cols.index("gov") + 1
    # رصد طلاب البند الثاني في التنبيهات والملخص
    summ = r["summary"]
    assert summ["band2_count"] == 2
    assert "الجيزة" in summ["band2_govs"] and "المنوفية" in summ["band2_govs"]
    assert any("البند الثاني" in a for a in summ["alerts"])

