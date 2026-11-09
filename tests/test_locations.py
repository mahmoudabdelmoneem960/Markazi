"""طبقة المواقع: قراءة الإحداثيات، قاعدة المراكز، التطبيع العربي، قرارات جوجل، مراجعة المراكز، وتجهيز المواقع قبل التوزيع.

كل حاجة هنا من غير نت: اللينكات المختصرة وجوجل بيتعملهم monkeypatch.
اختبارات centers_upsert بتستخدم قاعدة SQLite مؤقتة (tmp_path)؛ اختبارات الخدمات بتستخدم القاعدة المشتركة بأكواد LT_.
"""
import csv
import io
import itertools
import math

import numpy as np
import pandas as pd
import pytest

from distributor import config, db
from distributor.core import geonorm as gn
from distributor.core import geoprep as gp
from distributor.core import googlegeo as gg
from distributor.core import locdb
from distributor.services import centers as cs

_nid_counter = itertools.count(1)


def _nid():
    """رقم قومي فريد 14 رقم (مايتكررش مع باقي الاختبارات)."""
    return "29" + "71" + f"{next(_nid_counter):010d}"


@pytest.fixture
def tcon(tmp_path):
    """قاعدة مواقع معزولة لكل اختبار."""
    con = locdb.connect(str(tmp_path / "loc.db"))
    yield con
    con.close()


@pytest.fixture
def shared():
    """اتصال بالقاعدة المشتركة (نفس اللي الخدمات بتستخدمها)."""
    con = db.connect()
    yield con
    con.close()


def _row(con, code):
    return dict(con.execute("SELECT * FROM centers WHERE code=?", (code,)).fetchone())


def _insert(con, code, **kw):
    cols = {"code": code, **kw}
    con.execute(f"INSERT OR REPLACE INTO centers({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values()))
    con.commit()


def _sheet(rows):
    cols = ["Center Code", "Center Name", "City", "Area", "Center Address", "Location", "Latitude", "Longitude"]
    return pd.DataFrame(rows, columns=cols)


# =====================================================================
# locdb.parse_coords / km / in_egypt
# =====================================================================
class TestParseCoords:
    def test_plain_lat_lon(self):
        assert locdb.parse_coords("30.0444, 31.2357") == (30.0444, 31.2357, "")
        assert locdb.parse_coords("  30.1;31.2 ")[:2] == (30.1, 31.2)
        assert locdb.parse_coords("30 31")[:2] == (30.0, 31.0)

    def test_swapped_lon_lat_is_fixed(self):
        lat, lon, note = locdb.parse_coords("32.27, 30.6")          # الإسماعيلية مكتوبة lon, lat
        assert (lat, lon) == (30.6, 32.27) and note

    def test_ambiguous_order_inside_egypt_kept_as_is(self):
        # الترتيبين جوه حدود مصر: مفيش طريقة نعرف إنها اتبدلت، فبتفضل زي ما هي
        assert locdb.parse_coords("31.2357, 30.0444") == (31.2357, 30.0444, "")

    def test_arabic_digits_and_comma(self):
        assert locdb.parse_coords("٣٠٫٠٤٤٤، ٣١٫٢٣٥٧") == (30.0444, 31.2357, "")

    @pytest.mark.parametrize("text", ["25°29'41.6\"N 28°58'57.9\"E", "28°58'57.9\"E 25°29'41.6\"N"])
    def test_dms(self, text):
        lat, lon, _ = locdb.parse_coords(text, strict=True)
        assert lat == pytest.approx(25 + 29 / 60 + 41.6 / 3600)
        assert lon == pytest.approx(28 + 58 / 60 + 57.9 / 3600)

    def test_google_pin_beats_map_center(self):
        url = ("https://www.google.com/maps/place/X/@30.1,31.1,17z/data=!3m1!4b1!4m6!3m5!1s0x0:0x0!8m2"
               "!3d30.0444!4d31.2357")
        assert locdb.parse_coords(url, strict=True)[:2] == (30.0444, 31.2357)

    def test_query_param_links(self):
        assert locdb.parse_coords("https://maps.google.com/?q=30.0444,31.2357", strict=True)[:2] == (30.0444, 31.2357)
        assert locdb.parse_coords("https://maps.google.com/?q=30.0444%2C31.2357", strict=True)[:2] == (30.0444, 31.2357)
        assert locdb.parse_coords("https://maps.google.com/?ll=30.0444, 31.2357", strict=True)[:2] == (30.0444, 31.2357)

    # regression: كان BUG ( بعد الفاصلة في ?q=) واتصلّح
    def test_query_param_with_plus_separator(self):
        assert locdb.parse_coords("https://maps.google.com/?q=30.0444,+31.2357")[:2] == (30.0444, 31.2357)

    def test_center_param_only_non_strict(self):
        url = "https://www.google.com/maps?center=30.05,31.24"
        assert locdb.parse_coords(url)[:2] == (30.05, 31.24)
        assert locdb.parse_coords(url, strict=True) is None

    def test_map_center_zoom_rules(self):
        near, far, nozoom = ("https://www.google.com/maps/@30.05,31.24,17z", "https://www.google.com/maps/@30.05,31.24,12z",
                             "https://www.google.com/maps/@30.05,31.24")
        assert locdb.parse_coords(near, strict=True)[:2] == (30.05, 31.24)
        assert locdb.parse_coords(far, strict=True) is None             # زووم بعيد = مش موقع مكان
        assert locdb.parse_coords(nozoom, strict=True) is None
        assert locdb.parse_coords(far)[:2] == (30.05, 31.24)            # غير strict: بنقبله
        assert locdb.parse_coords("https://www.google.com/maps/@30.05,31.24,15z", strict=True) is not None   # 15 = الحد

    def test_strict_rejects_plain_text(self):
        assert locdb.parse_coords("30.0444, 31.2357", strict=True) is None

    @pytest.mark.parametrize("text", ["48.85, 2.35", "https://maps.google.com/?q=48.85,2.35", "40.7, -74.0"])
    def test_outside_egypt_is_none(self, text):
        assert locdb.parse_coords(text) is None

    @pytest.mark.parametrize("text", [None, "", "   ", "hello", "abc, def", "01001234567", "https://maps.app.goo.gl/abc",
                                      "https://www.google.com/maps/search/مدرسة+النور"])
    def test_garbage_is_none(self, text):
        assert locdb.parse_coords(text) is None


def test_km_and_in_egypt():
    assert locdb.km(30, 31, 30, 31) == 0
    assert locdb.km(30, 31, 31, 31) == pytest.approx(110.57)
    assert locdb.km(30, 31, 30, 32) == pytest.approx(111.32 * math.cos(math.radians(30)), rel=1e-6)
    assert locdb.km(30, 31, 30.5, 31.5) == pytest.approx(locdb.km(30.5, 31.5, 30, 31))
    assert locdb.in_egypt(30.0444, 31.2357)
    assert locdb.in_egypt(21.5, 24.5) and locdb.in_egypt(31.9, 37.3)      # الحدود نفسها جوه
    assert not locdb.in_egypt(48.85, 2.35)
    assert not locdb.in_egypt(None, 31.2) and not locdb.in_egypt(30.0, None)
    assert not locdb.in_egypt(float("nan"), 31.0)


# =====================================================================
# locdb.center_records
# =====================================================================
def test_center_records_first_coords_across_rooms():
    C = pd.DataFrame({
        "Center Code": ["A", "A", "A", "B"],
        "Center Name": ["مركز أ", "مركز أ", "مركز أ", "مركز ب"],
        "City": [" Cairo ", "cairo", "cairo", np.nan],
        "Area": ["نصر", "نصر", "نصر", None],
        "Center Address": [np.nan, "شارع عباس", "تاني", np.nan],
        "Link": [np.nan, "not a link", "https://maps.app.goo.gl/x", np.nan],
        " Latitude ": [np.nan, "30.06", 30.5, np.nan],
        "Longitude": [31.3, 31.33, 31.5, np.nan],
    })
    recs = locdb.center_records(C)
    a, b = recs["A"], recs["B"]
    assert a["lat"] == 30.06 and a["lon"] == 31.33          # أول روم فيها الاتنين مع بعض
    assert a["gov"] == "cairo" and a["name"] == "مركز أ" and a["area"] == "نصر"
    assert a["address"] == "شارع عباس"                      # أول عنوان مش فاضي
    assert a["url"] == "https://maps.app.goo.gl/x"          # عمود اللينك اتعرف من محتواه
    assert b == {"code": "B", "gov": "", "area": "", "name": "مركز ب", "address": "", "url": "", "lat": None, "lon": None}


def test_center_records_codes_as_strings_and_arabic_headers():
    C = pd.DataFrame({"Center Code": [101, 101], "خط العرض": [30.1, 30.2], "خط الطول": [31.1, 31.2], "العنوان": ["x", "y"]})
    recs = locdb.center_records(C)
    assert list(recs) == ["101"]
    assert (recs["101"]["lat"], recs["101"]["lon"], recs["101"]["address"]) == (30.1, 31.1, "x")
    assert recs["101"]["gov"] == "" and recs["101"]["url"] == ""


def test_s_helper():
    assert locdb._s(None) == "" and locdb._s(float("nan")) == "" and locdb._s(" nan ") == ""
    assert locdb._s("NULL") == "" and locdb._s(" x ") == "x" and locdb._s(5) == "5"


# =====================================================================
# locdb.validate_center
# =====================================================================
def test_validate_center_flags(tcon):
    assert locdb.validate_center(tcon, "X", "g", None, 31.0) == ["مفيش إحداثيات"]
    assert "برا حدود مصر" in locdb.validate_center(tcon, "X", "g", 48.8, 2.3)
    assert "خط العرض = خط الطول" in locdb.validate_center(tcon, "X", "g", 30.0, 30.0)
    assert locdb.validate_center(tcon, "X", "g", 30.0, 31.0) == []

    _insert(tcon, "P0", gov="g", lat=30.0, lon=31.0)
    assert locdb.validate_center(tcon, "X", "g", 30.0, 31.0) == ["نفس إحداثيات المركز P0"]
    assert locdb.validate_center(tcon, "P0", "g", 30.0, 31.0) == []          # المركز نفسه مش بيتقارن بنفسه

    for i, (la, lo) in enumerate([(30.01, 31.01), (30.02, 31.02)]):
        _insert(tcon, f"P{i + 1}", gov="g", lat=la, lon=lo)
    far = locdb.validate_center(tcon, "X", "g", 31.0, 33.0)
    assert any(f.startswith("بعيد") and "كم عن باقي مراكز المحافظة" in f for f in far)
    assert locdb.validate_center(tcon, "X", "g", 30.3, 31.3) == []           # ~45 كم: جوه الحد (80)
    assert locdb.validate_center(tcon, "X", "other_gov", 31.0, 33.0) == []    # محافظة تانية: مفيش مقارنة


def test_validate_center_needs_three_peers(tcon):
    _insert(tcon, "Q1", gov="g2", lat=30.0, lon=31.0)
    _insert(tcon, "Q2", gov="g2", lat=30.01, lon=31.01)
    assert locdb.validate_center(tcon, "X", "g2", 31.5, 33.0) == []


# =====================================================================
# locdb.centers_upsert
# =====================================================================
class TestCentersUpsert:
    def test_insert_new_with_and_without_coords(self, tcon):
        rep = locdb.centers_upsert(_sheet([
            ("N1", "مركز 1", "cairo", "نصر", "شارع 1", "https://maps.app.goo.gl/n1", 30.05, 31.33),
            ("N2", "مركز 2", "cairo", "حلوان", "", "", None, None),
        ]), con=tcon)
        assert rep["new"] == 2 and rep["no_coords"] == 1 and rep["updated_coords"] == 0
        r1, r2 = _row(tcon, "N1"), _row(tcon, "N2")
        assert (r1["lat"], r1["lon"], r1["source"], r1["verified"]) == (30.05, 31.33, "sheet", 0)
        assert r1["gmaps_url"] == "https://maps.app.goo.gl/n1" and r1["address"] == "شارع 1"
        assert r2["lat"] is None and r2["source"] is None and r2["flags"] == ""

    def test_source_label_is_used(self, tcon):
        locdb.centers_upsert(_sheet([("S1", "", "cairo", "", "", "", 30.0, 31.0)]), source="custom", con=tcon)
        assert _row(tcon, "S1")["source"] == "custom"

    def test_sheet_updates_coords_and_reports_moves(self, tcon):
        locdb.centers_upsert(_sheet([("M1", "", "cairo", "", "", "", 30.0, 31.0),
                                     ("M2", "", "cairo", "", "", "", 30.1, 31.1),
                                     ("M3", "", "cairo", "", "", "", 30.2, 31.2)]), con=tcon)
        tcon.execute("UPDATE centers SET verified=1, google_lat=30.0, google_lon=31.0, google_verdict='same_current', "
                     "link_lat=30.0, link_lon=31.0 WHERE code='M1'")
        tcon.commit()
        rep = locdb.centers_upsert(_sheet([
            ("M1", "", "cairo", "", "", "", 30.001, 31.0),       # ~110 م: اتنقل
            ("M2", "", "cairo", "", "", "", 30.1002, 31.1),      # ~22 م: اتحدث بس مش "اتنقل"
            ("M3", "", "cairo", "", "", "", 30.2000001, 31.2),   # أقل من 2 م: مفيش تحديث
        ]), con=tcon)
        assert rep["updated_coords"] == 2 and rep["moved"] == ["M1"] and rep["new"] == 0
        m1 = _row(tcon, "M1")
        assert m1["lat"] == 30.001 and m1["verified"] == 0          # "متأكد" اتشال
        assert m1["google_lat"] is None and m1["google_verdict"] is None
        assert m1["link_diff_m"] == pytest.approx(110.57, rel=1e-3)  # الفرق عن اللينك اتحسب على الموقع الجديد
        assert _row(tcon, "M2")["lat"] == 30.1002
        assert _row(tcon, "M3")["lat"] == 30.2

    def test_missing_sheet_coords_keep_db_coords(self, tcon):
        locdb.centers_upsert(_sheet([("K1", "", "cairo", "", "", "", 30.0, 31.0)]), con=tcon)
        rep = locdb.centers_upsert(_sheet([("K1", "اسم جديد", "giza", "", "", "", None, None)]), con=tcon)
        r = _row(tcon, "K1")
        assert (r["lat"], r["lon"], r["name"], r["gov"]) == (30.0, 31.0, "اسم جديد", "giza")
        assert rep["no_coords"] == 0 and rep["updated_coords"] == 0

    @pytest.mark.parametrize("src", ["manual", "google"])
    def test_manual_and_google_never_overwritten(self, tcon, src):
        locdb.centers_upsert(_sheet([("G1", "", "cairo", "", "", "", 30.0, 31.0),
                                     ("G2", "", "cairo", "", "", "", 30.1, 31.1)]), con=tcon)
        locdb.center_set(tcon, "G1", 30.5, 31.5, source=src)
        locdb.center_set(tcon, "G2", 30.1, 31.1, source=src)
        rep = locdb.centers_upsert(_sheet([("G1", "", "cairo", "", "", "", 30.0, 31.0),
                                           ("G2", "", "cairo", "", "", "", 30.1001, 31.1)]), con=tcon)   # G2: ~11 م بس
        assert rep["kept_manual"] == 2 and rep["updated_coords"] == 0
        assert rep["differs_from_manual"] == ["G1"]
        g1 = _row(tcon, "G1")
        assert (g1["lat"], g1["lon"], g1["source"], g1["verified"]) == (30.5, 31.5, src, 1)
        assert _row(tcon, "G2")["lat"] == 30.1

    def test_new_link_clears_link_fields_same_link_keeps_them(self, tcon):
        locdb.centers_upsert(_sheet([("L1", "", "cairo", "", "", "https://a.example/1", 30.0, 31.0)]), con=tcon)
        tcon.execute("UPDATE centers SET link_lat=30.0, link_lon=31.0, link_diff_m=0, link_name='x', link_tried=1 WHERE code='L1'")
        tcon.commit()
        rep = locdb.centers_upsert(_sheet([("L1", "", "cairo", "", "", "https://a.example/1", 30.0, 31.0)]), con=tcon)
        assert "link_changed" not in rep and _row(tcon, "L1")["link_lat"] == 30.0
        rep = locdb.centers_upsert(_sheet([("L1", "", "cairo", "", "", "https://a.example/2", 30.0, 31.0)]), con=tcon)
        r = _row(tcon, "L1")
        assert rep["link_changed"] == ["L1"] and r["gmaps_url"] == "https://a.example/2"
        assert all(r[k] is None for k in ("link_lat", "link_lon", "link_diff_m", "link_name", "link_tried"))
        # شيت من غير لينك: اللينك المحفوظ ما يتمسحش
        locdb.centers_upsert(_sheet([("L1", "", "cairo", "", "", "", 30.0, 31.0)]), con=tcon)
        assert _row(tcon, "L1")["gmaps_url"] == "https://a.example/2"

    def test_address_change_clears_estimate_manual_address_preserved(self, tcon):
        locdb.centers_upsert(_sheet([("A1", "", "cairo", "نصر", "عنوان 1", "", 30.0, 31.0),
                                     ("A2", "", "cairo", "نصر", "عنوان 2", "", 30.1, 31.1)]), con=tcon)
        tcon.execute("UPDATE centers SET addr_lat=30, addr_lon=31, addr_level='street', addr_matched='m'")
        tcon.execute("UPDATE centers SET address='عنوان يدوي', addr_manual=1 WHERE code='A2'")
        tcon.commit()
        # نفس العنوان والمنطقة: التقدير يفضل
        locdb.centers_upsert(_sheet([("A1", "", "cairo", "نصر", "عنوان 1", "", 30.0, 31.0)]), con=tcon)
        assert _row(tcon, "A1")["addr_level"] == "street"
        locdb.centers_upsert(_sheet([("A1", "", "cairo", "نصر", "عنوان جديد", "", 30.0, 31.0),
                                     ("A2", "", "cairo", "نصر", "عنوان الشيت", "", 30.1, 31.1)]), con=tcon)
        a1, a2 = _row(tcon, "A1"), _row(tcon, "A2")
        assert a1["address"] == "عنوان جديد" and a1["addr_lat"] is None and a1["addr_level"] is None
        assert a2["address"] == "عنوان يدوي" and a2["addr_level"] == "street"   # العنوان اليدوي ما بيتغيرش
        # تغيير المنطقة بس كمان بيمسح التقدير
        locdb.centers_upsert(_sheet([("A2", "", "cairo", "حلوان", "عنوان الشيت", "", 30.1, 31.1)]), con=tcon)
        assert _row(tcon, "A2")["addr_level"] is None and _row(tcon, "A2")["address"] == "عنوان يدوي"

    def test_empty_sheet_address_keeps_db_address(self, tcon):
        locdb.centers_upsert(_sheet([("E1", "", "cairo", "نصر", "عنوان قديم", "", 30.0, 31.0)]), con=tcon)
        locdb.centers_upsert(_sheet([("E1", "", "cairo", "نصر", None, "", 30.0, 31.0)]), con=tcon)
        assert _row(tcon, "E1")["address"] == "عنوان قديم"

    def test_flags_recomputed(self, tcon):
        locdb.centers_upsert(_sheet([("F1", "", "cairo", "", "", "", 30.0, 31.0),
                                     ("F2", "", "cairo", "", "", "", 30.0, 31.0),
                                     ("F3", "", "cairo", "", "", "", 29.5, 29.5)]), con=tcon)
        assert _row(tcon, "F1")["flags"] == "نفس إحداثيات المركز F2"
        assert "خط العرض = خط الطول" in _row(tcon, "F3")["flags"]

    def test_own_connection_when_none(self, monkeypatch, tmp_path):
        p = str(tmp_path / "own.db")
        monkeypatch.setattr(locdb, "DB_PATH", p)
        rep = locdb.centers_upsert(_sheet([("O1", "", "cairo", "", "", "", 30.0, 31.0)]))
        assert rep["new"] == 1
        con = locdb.connect(p)
        try:
            assert _row(con, "O1")["lat"] == 30.0
        finally:
            con.close()


# =====================================================================
# locdb.center_set + overrides
# =====================================================================
def test_center_set(tcon):
    with pytest.raises(KeyError):
        locdb.center_set(tcon, "NOPE", 30, 31)
    _insert(tcon, "C1", gov="cairo", lat=30.0, lon=31.0, source="sheet", verified=0,
            link_lat=30.01, link_lon=31.0, google_lat=30.0, google_lon=31.01)
    _insert(tcon, "C2", gov="cairo", lat=30.2, lon=31.2)
    fl = locdb.center_set(tcon, "C1", 30.0, 31.0, note="n")
    r = _row(tcon, "C1")
    assert fl == [] and (r["source"], r["verified"], r["note"], r["flags"]) == ("manual", 1, "n", "")
    assert r["link_diff_m"] == pytest.approx(1105.7, rel=1e-3)
    assert r["google_gap_m"] == pytest.approx(111.32 * math.cos(math.radians(30)) * 10, rel=1e-3)
    fl = locdb.center_set(tcon, "C1", 30.2, 31.2, source="google")
    assert fl == ["نفس إحداثيات المركز C2"] and _row(tcon, "C1")["source"] == "google"
    assert _row(tcon, "C1")["flags"] == "نفس إحداثيات المركز C2"


def test_nid_and_address_overrides(tcon):
    n1, n2 = _nid(), _nid()
    locdb.override_set(tcon, 30.1, 31.1, nid=n1, note="x")
    locdb.override_set(tcon, 30.2, 31.2, nid=n1)                      # استبدال
    assert locdb.overrides_get(tcon, [n1, n2, "", None]) == {n1: (30.2, 31.2)}
    assert locdb.overrides_get(tcon, []) == {}
    k = locdb.geo_key("cairo", "مدينة نصر", "شارع عباس")
    assert k == "cairo|مدينه نصر|شارع عباس"
    locdb.override_set(tcon, 30.3, 31.3, key=k)
    assert locdb.addr_overrides_get(tcon, [k, "other"]) == {k: (30.3, 31.3)}
    assert locdb.overrides_get(tcon, [k]) == {}                       # الجدولين منفصلين
    with pytest.raises(ValueError):
        locdb.override_set(tcon, 30, 31)
    locdb.override_delete(tcon, nid=n1)
    assert locdb.overrides_get(tcon, [n1]) == {} and locdb.addr_overrides_get(tcon, [k])
    locdb.override_delete(tcon, key=k)
    assert locdb.addr_overrides_get(tcon, [k]) == {}
    locdb.override_delete(tcon)                                       # من غير حاجة: مايوقعش


def test_overrides_get_many_chunks(tcon):
    nids = [_nid() for _ in range(1700)]
    for n in nids[::400]:
        locdb.override_set(tcon, 30.0, 31.0, nid=n)
    assert set(locdb.overrides_get(tcon, nids)) == set(nids[::400])


def test_group_overrides(tcon):
    locdb.group_override_set(tcon, "cairo|نصر|ش:عباس", "cairo", "نصر", "ش:عباس", 30.06, 31.33)
    locdb.group_override_set(tcon, "cairo|نصر|ش:عباس", "cairo", "نصر", "ش:عباس", 30.07, 31.34, note="2")
    assert locdb.group_overrides_get(tcon, ["cairo|نصر|ش:عباس", "x"]) == {"cairo|نصر|ش:عباس": (30.07, 31.34)}
    locdb.group_override_delete(tcon, "cairo|نصر|ش:عباس")
    assert locdb.group_overrides_get(tcon, ["cairo|نصر|ش:عباس"]) == {}


def test_cache_prepare_keeps_google_and_manual(tcon):
    rows = [(f"k{s}", "cairo", "a", "b", 30.0, 31.0, "street", 0.9, "m", s) for s in ("osm", "google", "manual")]
    locdb.cache_put_many(tcon, rows)
    locdb.cache_prepare(tcon, "fp1")
    assert set(locdb.cache_get_many(tcon, ["kosm", "kgoogle", "kmanual"])) == {"kgoogle", "kmanual"}
    locdb.cache_put_many(tcon, rows[:1])
    locdb.cache_prepare(tcon, "fp1")                                   # نفس البصمة: مفيش مسح
    assert "kosm" in locdb.cache_get_many(tcon, ["kosm"])


def test_place_name_from_url():
    assert locdb.place_name_from_url("https://www.google.com/maps/place/%D9%85%D8%AF%D8%B1%D8%B3%D8%A9+%D8%A7%D9%84%D9%86%D9%88%D8%B1/@30,31,17z") == "مدرسة النور"
    assert locdb.place_name_from_url("https://www.google.com/maps/@30,31,17z") == ""
    assert locdb.place_name_from_url(None) == ""


def test_resolve_gmaps_uses_url_coords_without_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(locdb.urllib.request, "urlopen", boom)
    assert locdb.resolve_gmaps("https://www.google.com/maps/place/X/@30.1,31.1,17z/data=!3d30.0444!4d31.2357") == (30.0444, 31.2357)
    assert locdb.resolve_gmaps("not a url") is None and locdb.resolve_gmaps(None) is None


# =====================================================================
# geonorm
# =====================================================================
def test_norm():
    assert gn.norm(None) == ""
    assert gn.norm("  أحمد  إبراهيم، آمنة! ٣٤ مُحَمَّد مصطفى ") == "احمد ابراهيم امنه 34 محمد مصطفي"
    assert gn.norm("شارع_النصر-الجديد") == "شارع النصر الجديد"
    assert gn.norm("Street 9") == "street 9"
    assert gn.norm("مسئول مؤمن") == "مسيول مومن"


def test_tokens():
    assert gn.tokens("الحسين الي") == ["حسين", "الي"]              # «ال» بتتشال بس من الكلمات الأطول من 3 حروف
    assert gn.tokens("ابن خلدون") == ["بن", "خلدون"] and gn.tokens("بن خلدون") == ["بن", "خلدون"]
    assert gn.tokens("عبد الرحمن") == ["عبدرحمن"]
    assert gn.tokens("ابو بكر") == ["ابوبكر"]
    assert gn.tokens("مدرسة عبد") == ["مدرسه", "عبد"]                  # آخر كلمة: مفيش حاجة تتدمج معاها
    assert gn.tokens("حي الزهور", drop={"حي"}) == ["زهور"]
    assert gn.tokens("") == [] and gn.tokens(None) == []


@pytest.mark.xfail(strict=True, reason="BUG: tokens() strips 'ال' from 'الله'/'الحسن' before joining with عبد/ابو, so "
                                       "'عبد الله' -> 'عبدله' but 'عبدالله' -> 'عبدالله' (docstring promises they're equal)")
def test_tokens_join_matches_written_together():
    assert gn.tokens("عبد الله") == gn.tokens("عبدالله")
    assert gn.key("ابو الحسن") == gn.key("ابوالحسن")


def test_key_and_gov_code():
    assert gn.key("حي الزهور") == "زهور"
    assert gn.key("مدينة نصر") == "نصر"
    assert gn.key("شارع النصر", "street") == "نصر"
    assert gn.key("شارع النصر", "other") == "شارع نصر"
    assert gn.key_full("حي الزهور") == "حي زهور"
    assert gn.gov_code("القاهرة") == "cairo"
    assert gn.gov_code("محافظة الجيزة") == "giza"
    assert gn.gov_code(" الإسكندريّة ") == "alexandria"
    assert gn.gov_code("بنى سويف") == "beni_suef"
    assert gn.gov_code("كفر الشيخ") == "kafr_el_sheikh"
    assert gn.gov_code("Atlantis") is None and gn.gov_code(None) is None and gn.gov_code("") is None
    assert gn.has_arabic("abc ب") and not gn.has_arabic("abc") and not gn.has_arabic(None)


# =====================================================================
# googlegeo (pure)
# =====================================================================
def _g(lat, lon, precise=True, **kw):
    return {"lat": lat, "lon": lon, "precise": precise, "type": "ROOFTOP", "addr": "", **kw}


class TestDecide:
    CUR = (30.0, 31.0)
    NEAR = (30.001, 31.0)       # ~110 م
    FAR = (30.01, 31.0)         # ~1.1 كم
    VERY_FAR = (30.2, 31.0)     # ~22 كم

    def test_none(self):
        assert gg.decide(self.CUR, None, None) == {"verdict": "none", "apply": None, "gap_cur_m": None, "gap_link_m": None}

    def test_conflict(self):
        d = gg.decide(self.CUR, None, _g(*self.CUR, conflict=True))
        assert d["verdict"] == "conflict" and d["apply"] is None and d["gap_cur_m"] == 0

    def test_approx_and_approx_far(self):
        assert gg.decide(self.CUR, None, _g(*self.FAR, precise=False))["verdict"] == "approx"
        assert gg.decide(self.CUR, None, _g(*self.VERY_FAR, precise=False))["verdict"] == "approx_far"
        d = gg.decide(None, None, _g(*self.VERY_FAR, precise=False))
        assert d["verdict"] == "approx" and d["apply"] is None and d["gap_cur_m"] is None

    def test_fill_when_nothing_known(self):
        d = gg.decide(None, None, _g(*self.CUR))
        assert (d["verdict"], d["apply"]) == ("fill", "google")

    def test_no_current_link_agrees_or_differs(self):
        d = gg.decide(None, self.CUR, _g(*self.NEAR))
        assert (d["verdict"], d["apply"]) == ("same_link", "link") and d["gap_link_m"] == pytest.approx(110.57, rel=1e-3)
        d = gg.decide(None, self.CUR, _g(*self.FAR))
        assert (d["verdict"], d["apply"]) == ("differs", None)

    def test_same_current(self):
        d = gg.decide(self.CUR, self.FAR, _g(*self.NEAR))
        assert (d["verdict"], d["apply"]) == ("same_current", "verify")

    def test_same_current_boundary_300m(self):
        g = _g(30.0 + 0.299 / 110.57, 31.0)
        assert gg.decide(self.CUR, None, g)["verdict"] == "same_current"
        g = _g(30.0 + 0.301 / 110.57, 31.0)
        assert gg.decide(self.CUR, None, g)["verdict"] == "differs"

    def test_link_wins_when_current_far(self):
        d = gg.decide(self.FAR, self.CUR, _g(*self.NEAR))
        assert (d["verdict"], d["apply"]) == ("same_link", "link")

    def test_differs(self):
        assert gg.decide(self.CUR, None, _g(*self.FAR)) ["verdict"] == "differs"
        assert gg.decide(self.CUR, self.VERY_FAR, _g(*self.FAR))["apply"] is None

    @pytest.mark.parametrize("cur,link,g,verdict", [
        (CUR, None, _g(*NEAR), "same_current"),
        (FAR, CUR, _g(*NEAR), "differs"),        # كان same_link: مع اليدوي بيبقى اختلاف للمراجعة
        (None, None, _g(*CUR), "fill"),
        (None, CUR, _g(*NEAR), "differs"),
        (CUR, None, _g(*FAR), "differs"),
        (CUR, None, None, "none"),
    ])
    def test_manual_never_applies(self, cur, link, g, verdict):
        d = gg.decide(cur, link, g, manual=True)
        assert d["apply"] is None and d["verdict"] == verdict


def test_pick_best():
    assert gg.pick_best([]) is None and gg.pick_best(None) is None
    a, b = _g(30.0, 31.0, precise=False), _g(30.5, 31.5)
    assert gg.pick_best([a, b]) == b                                   # الدقيق قبل التقريبي
    assert gg.pick_best([a]) == a and gg.pick_best([a]) is not a       # نسخة
    near, far = _g(30.0, 31.0), _g(30.001, 31.0)
    assert "conflict" not in gg.pick_best([near, far])                 # ~110 م: نفس المكان
    res = [near, _g(30.01, 31.0)]
    best = gg.pick_best(res)
    assert best["conflict"] is True and (best["lat"], best["lon"]) == (30.0, 31.0)
    assert "conflict" not in res[0]                                    # المدخلات ما اتعدلتش


def test_txt_and_build_queries():
    assert gg._txt("ﺷﺎرع  ی\tک ") == "شارع ي ك"
    assert gg._txt(None) == "" and gg._txt("NaN") == "" and gg._txt(" none ") == ""
    row = {"gov": "cairo", "name": "مركز النور", "area": "مدينة نصر", "address": "شارع عباس"}
    assert gg.build_queries(row) == ["مركز النور مدينة نصر القاهرة", "شارع عباس القاهرة"]
    assert gg.build_queries({**row, "address": "شارع عباس، القاهرة"})[1] == "شارع عباس، القاهرة"   # المحافظة موجودة
    assert gg.build_queries({**row, "name": None, "area": "nan"}) == ["شارع عباس القاهرة"]
    assert gg.build_queries({**row, "address": ""}) == ["مركز النور مدينة نصر القاهرة"]
    assert gg.build_queries({"gov": "cairo", "name": "x", "area": "", "address": "x"}) == ["x القاهرة"]  # نفس الاستعلام مرة واحدة
    assert gg.build_queries({"gov": "", "name": "", "area": "", "address": ""}) == []
    assert gg.build_queries({"gov": "atlantis", "name": "x", "area": "", "address": ""}) == ["x atlantis"]


# regression: كان BUG (المنوفية بالكود الإنجليزي) واتصلّح
def test_build_queries_monufia_uses_arabic_name():
    assert gg.build_queries({"gov": "monufia", "name": "مركز", "area": "", "address": ""}) == ["مركز المنوفية"]


def test_all_canonical_govs_have_arabic_names():
    missing = set(gn.GOV_NAMES.values()) - set(gg.GOV_AR)
    assert missing == set()


class TestGoogleKey:
    VALID = "AIzaSyA1234567890abcdefGHIJ_-xyz"

    @pytest.fixture(autouse=True)
    def _iso(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        self.kf = tmp_path / "google_key.txt"
        monkeypatch.setattr(config, "GOOGLE_KEY_FILE", self.kf)

    def test_no_key(self):
        assert gg.get_key() is None and gg.key_problem() == ""

    def test_env_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", f' "{self.VALID}" ')
        assert gg.get_key() == self.VALID and gg.key_problem() == ""

    def test_env_bad_key(self, monkeypatch):
        monkeypatch.setenv("GOOGLE_API_KEY", "short")
        assert gg.get_key() is None and "GOOGLE_API_KEY" in gg.key_problem()

    def test_env_beats_file(self, monkeypatch):
        self.kf.write_text("B" * 30, encoding="utf-8")
        monkeypatch.setenv("GOOGLE_API_KEY", self.VALID)
        assert gg.get_key() == self.VALID

    def test_file_formats(self):
        self.kf.write_text(f"# comment\n\nGOOGLE_API_KEY = '{self.VALID}'  # trailing\nother\n", encoding="utf-8")
        assert gg.get_key() == self.VALID and gg.key_problem() == ""
        self.kf.write_bytes(("﻿" + self.VALID + "\r\n").encode("utf-8"))
        assert gg.get_key() == self.VALID
        self.kf.write_text(self.VALID, encoding="utf-16")
        assert gg.get_key() == self.VALID

    def test_bad_file(self):
        self.kf.write_text("# only comments\n", encoding="utf-8")
        assert gg.get_key() is None and "google_key.txt" in gg.key_problem()
        self.kf.write_text("not a key!", encoding="utf-8")
        assert gg.get_key() is None and gg.key_problem()

    def test_status_never_contains_key(self, monkeypatch, tcon):
        monkeypatch.setenv("GOOGLE_API_KEY", self.VALID)
        st = gg.status(tcon)
        assert st["key"] is True and self.VALID not in str(st) and st["used"] == 0
        gg._bump(tcon)
        assert gg.usage(tcon) == 1


def test_geocode_parsing_with_fake_urlopen(monkeypatch):
    """geocode() من غير نت: رد جوجل متزيف."""
    import json as _json
    payload = {"status": "OK", "results": [{"geometry": {"location": {"lat": 30.05, "lng": 31.24}, "location_type": "APPROXIMATE"},
                                            "types": ["establishment"], "formatted_address": "Cairo"}]}

    class R:
        def __init__(self, d):
            self.d = _json.dumps(d).encode()

        def read(self):
            return self.d

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    calls = []
    monkeypatch.setattr(gg.urllib.request, "urlopen", lambda url, timeout=0: calls.append(url) or R(payload))
    g = gg.geocode("q", "SECRETKEY_123456789012", on_request=lambda: calls.append("req"))
    assert g["precise"] is True and (g["lat"], g["lon"]) == (30.05, 31.24) and calls[0] == "req"
    payload["results"][0]["partial_match"] = True
    assert gg.geocode("q", "k")["precise"] is False
    payload.update(status="ZERO_RESULTS")
    assert gg.geocode("q", "k") is None
    payload.update(status="REQUEST_DENIED", error_message="bad key SECRETKEY_123456789012")
    with pytest.raises(gg.GoogleError) as e:
        gg.geocode("q", "SECRETKEY_123456789012")
    assert "SECRETKEY_123456789012" not in str(e.value) and "***" in str(e.value)
    payload.update(status="OVER_QUERY_LIMIT")
    with pytest.raises(gg.GoogleError):
        gg.geocode("q", "k")


# =====================================================================
# services/centers (pure parts)
# =====================================================================
class TestParseInput:
    def test_coords(self):
        assert cs.parse_input("30.0444, 31.2357") == (30.0444, 31.2357, "")

    def test_garbage(self, monkeypatch):
        monkeypatch.setattr(locdb, "resolve_gmaps", lambda *a, **k: pytest.fail("should not resolve non-links"))
        for t in ("abc", "", None, "48.85, 2.35"):
            with pytest.raises(ValueError, match="إحداثيات"):
                cs.parse_input(t)

    def test_short_link_resolved(self, monkeypatch):
        seen = []
        monkeypatch.setattr(locdb, "resolve_gmaps", lambda url, *a, **k: seen.append(url) or (30.1, 31.2))
        assert cs.parse_input(" https://maps.app.goo.gl/abc ") == (30.1, 31.2, "من لينك Google")
        assert seen == ["https://maps.app.goo.gl/abc"]

    def test_unresolvable_link(self, monkeypatch):
        monkeypatch.setattr(locdb, "resolve_gmaps", lambda *a, **k: None)
        with pytest.raises(ValueError):
            cs.parse_input("https://maps.app.goo.gl/dead")

    def test_long_link_needs_no_resolve(self, monkeypatch):
        monkeypatch.setattr(locdb, "resolve_gmaps", lambda *a, **k: pytest.fail("no network for long links"))
        assert cs.parse_input("https://www.google.com/maps?q=30.05,31.24")[:2] == (30.05, 31.24)


class TestReviewState:
    BASE = {"gmaps_url": "https://x", "link_lat": 30.0, "link_lon": 31.0, "addr_lat": 30.0, "addr_lon": 31.0,
            "addr_level": "poi", "link_tried": 1, "review": None}

    def _st(self, **kw):
        return cs.review_state({**self.BASE, **kw})

    def test_output_fields(self):
        s = self._st(addr_lat=30.005)
        assert s["tolerance_km"] == 1.0 and s["addr_gap_km"] == pytest.approx(0.6, abs=0.05)
        assert s["status"] == "ok" and s["status_ar"] == cs.REVIEW_LABELS["ok"]

    @pytest.mark.parametrize("level,dlat,status", [
        ("poi", 0.008, "ok"), ("poi", 0.011, "far"),           # ~0.9 / ~1.2 كم مقابل 1 كم
        ("locality", 0.025, "ok"), ("locality", 0.03, "far"),  # ~2.8 / ~3.3 مقابل 3
        ("markaz", 0.085, "ok"), ("markaz", 0.1, "far"),       # ~9.4 / ~11 مقابل 10
    ])
    def test_tolerance_by_level(self, level, dlat, status):
        assert self._st(addr_level=level, addr_lat=30.0 + dlat)["status"] == status

    def test_pending_when_address_not_estimated(self):
        s = self._st(addr_lat=None, addr_lon=None, addr_level=None)
        assert s["status"] == "pending" and s["addr_gap_km"] is None and s["tolerance_km"] is None

    def test_vague_when_level_unknown_even_with_coords(self):
        assert self._st(addr_level="gov")["status"] == "vague"
        assert self._st(addr_lat=None, addr_level="none")["status"] == "vague"

    def test_priority_order(self):
        assert self._st(review="confirmed", gmaps_url="")["status"] == "confirmed"
        assert self._st(review="recheck", gmaps_url="")["status"] == "recheck"
        assert self._st(gmaps_url=None, link_lat=None)["status"] == "no_link"
        assert self._st(link_lat=None, link_tried=0)["status"] == "pending"
        assert self._st(link_lat=None, link_tried=1, addr_level=None)["status"] == "link_unreadable"
        assert self._st(review="other")["status"] == "ok"

    def test_every_status_is_ordered_and_labelled(self):
        assert set(cs.REVIEW_ORDER) == set(cs.REVIEW_LABELS)


def test_same_name_and_dup_ok():
    assert cs._same_name({"name": "مدرسة أحمد"}, {"name": " مدرسه احمد "})
    assert not cs._same_name({"name": ""}, {"name": ""})
    assert not cs._same_name({"name": None}, {})
    assert not cs._same_name({"name": "أ"}, {"name": "ب"})
    assert cs._dup_ok({"dup_ok": "A,,B"}) == {"A", "B"}
    assert cs._dup_ok({"dup_ok": None}) == set() and cs._dup_ok({}) == set()
    assert cs.maps_link(30.04441234, 31.2) == "https://www.google.com/maps?q=30.044412,31.200000"


# =====================================================================
# services/centers (shared DB)
# =====================================================================
def test_center_issues_and_mark_same_place(shared):
    # منطقة نائية مخصوصة للاختبار ده (مفيش مراكز تانية جنبها)
    base = (24.7031, 34.1017)
    _insert(shared, "LT_ISS1", name="مركز تجربة 1", gov="red_sea", area="أ", lat=base[0], lon=base[1])
    _insert(shared, "LT_ISS2", name="مركز تجربة 2", gov="red_sea", area="ب", lat=base[0] + 0.0001, lon=base[1])   # ~11 م
    _insert(shared, "LT_ISS3", name="مركز تجربة 1", gov="red_sea", area="أ", lat=base[0], lon=base[1])          # نفس اسم 1
    _insert(shared, "LT_ISS4", name="مركز تجربة 4", gov="red_sea", area="أ", lat=base[0] + 0.0005, lon=base[1])  # ~55 م: بعيد كفاية
    _insert(shared, "LT_ISS5", name="مركز تجربة 5", gov="red_sea", area="أ", lat=base[0] + 0.0005, lon=base[1])  # نفس 4 بالظبط
    d = cs.center_issues()
    by = {c["code"]: c for c in d["centers"] if c["code"].startswith("LT_ISS")}
    assert {o["code"] for o in by["LT_ISS1"]["others"]} == {"LT_ISS2"}
    assert {o["code"] for o in by["LT_ISS2"]["others"]} == {"LT_ISS1", "LT_ISS3"}
    assert {o["code"] for o in by["LT_ISS4"]["others"]} == {"LT_ISS5"} and by["LT_ISS4"]["diff_area"] is False
    assert by["LT_ISS1"]["diff_area"] is True and "LT_ISS2" in by["LT_ISS1"]["text"]
    assert by["LT_ISS1"]["others"][0]["m"] == pytest.approx(11, abs=1)
    assert "نفس الإحداثيات بالظبط" in by["LT_ISS4"]["text"] and "على بعد 11 م" in by["LT_ISS2"]["text"]
    assert d["count"] == len(d["centers"]) and d["total"] >= 4

    with pytest.raises(KeyError):
        cs.mark_same_place("LT_ISS1", "LT_ISS1")
    with pytest.raises(KeyError):
        cs.mark_same_place("LT_ISS1", "LT_NOPE")
    assert cs.mark_same_place("LT_ISS1", "LT_ISS2")["ok"]
    assert cs.mark_same_place("LT_ISS2", "LT_ISS3")["ok"]
    assert _row(shared, "LT_ISS2")["dup_ok"] == "LT_ISS1,LT_ISS3" and _row(shared, "LT_ISS1")["dup_ok"] == "LT_ISS2"
    left = {c["code"] for c in cs.center_issues()["centers"]}
    assert not left & {"LT_ISS1", "LT_ISS2", "LT_ISS3"}
    assert _row(shared, "LT_ISS2")["lat"] == base[0] + 0.0001               # المواقع ما اتغيرتش


def test_edit_center(shared):
    _insert(shared, "LT_ED1", gov="cairo", area="نصر", address="قديم", lat=29.91, lon=31.91,
            addr_lat=29.9, addr_lon=31.9, addr_level="street", addr_matched="m")
    with pytest.raises(KeyError):
        cs.edit_center("LT_NOPE", address="x")
    assert cs.edit_center("LT_ED1")["ok"]                                   # مفيش تغيير
    assert cs.edit_center("LT_ED1", address="  قديم ")["ok"]                 # نفس العنوان بعد strip
    r = _row(shared, "LT_ED1")
    assert r["addr_manual"] is None and r["addr_level"] == "street"
    cs.edit_center("LT_ED1", address=" عنوان جديد ")
    r = _row(shared, "LT_ED1")
    assert r["address"] == "عنوان جديد" and r["addr_manual"] == 1
    assert all(r[k] is None for k in ("addr_lat", "addr_lon", "addr_level", "addr_matched"))
    # رفع الشيت تاني: العنوان المعدّل يفضل
    locdb.centers_upsert(_sheet([("LT_ED1", "", "cairo", "نصر", "عنوان الشيت", "", 29.91, 31.91)]), con=shared)
    assert _row(shared, "LT_ED1")["address"] == "عنوان جديد"


def test_master_xlsx_and_centers_csv(shared):
    _insert(shared, "LT_MX1", name="مركز مراجعة", gov="aswan", area="أ", address="عنوان 1", lat=24.0911, lon=32.8999,
            source="manual", review="confirmed", correct_url="https://maps.app.goo.gl/good", review_by="admin")
    _insert(shared, "LT_MX2", name="مركز تاني", gov="aswan", area="ب", address="", lat=24.0922, lon=32.9011,
            gmaps_url="https://x.example", review="recheck", link_diff_m=12.4)
    _insert(shared, "LT_MX3", name="من غير موقع", gov="aswan", lat=None, lon=None)
    x = pd.read_excel(cs.master_xlsx(), dtype=str)
    assert {"Center Code", "Center Name", "City", "Area", "Center Address", "Location", "Latitude", "Longitude",
            "حالة المراجعة"} <= set(x.columns)
    rows = x.set_index("Center Code")
    assert rows.loc["LT_MX1", "Location"] == "https://maps.app.goo.gl/good"
    assert rows.loc["LT_MX2", "Location"] == cs.maps_link(24.0922, 32.9011)       # من غير لينك صح: لينك من الإحداثيات
    assert pd.isna(rows.loc["LT_MX3", "Location"])
    assert rows.loc["LT_MX1", "حالة المراجعة"] == cs.REVIEW_LABELS["confirmed"]
    assert rows.loc["LT_MX2", "حالة المراجعة"] == cs.REVIEW_LABELS["recheck"]
    order = list(x["Center Code"])
    assert order.index("LT_MX2") < order.index("LT_MX3") < order.index("LT_MX1")     # recheck ... no_link ... confirmed

    text = cs.centers_csv()
    assert text.startswith("﻿")
    rd = list(csv.reader(io.StringIO(text[1:])))
    assert rd[0][:3] == ["code", "name", "gov"] and len(rd[0]) == 15
    r2 = next(r for r in rd if r[0] == "LT_MX2")
    assert r2[11] in ("12.0", "12") and r2[13] == "https://x.example"


# =====================================================================
# geoprep
# =====================================================================
def test_bbox_ok():
    lat = np.array([30.0, np.nan, 30.0, 48.8, np.inf, 21.5])
    lon = np.array([31.0, 31.0, np.nan, 2.3, 31.0, 37.3])
    assert gp._bbox_ok(lat, lon).tolist() == [True, False, False, False, False, True]


def _cluster(n, lat0, lon0, spread, seed=0, gov="cairo"):
    rng = np.random.default_rng(seed)
    lat = lat0 + rng.uniform(-spread, spread, n)
    lon = lon0 + rng.uniform(-spread, spread, n)
    g = np.array([gov] * n, dtype=object)
    areas = np.array([f"منطقة {i % 40}" for i in range(n)], dtype=object)
    addrs = np.array([f"شارع رقم {i} في المنطقة" for i in range(n)], dtype=object)
    return g, areas, addrs, lat, lon


def test_sheet_coords_suspect_tight_cluster():
    msgs = gp._sheet_coords_suspect(*_cluster(300, 30.05, 31.24, 0.003))
    assert len(msgs) == 1 and "cairo" in msgs[0] and "300" in msgs[0]


def test_sheet_coords_suspect_normal_spread_and_small_groups():
    assert gp._sheet_coords_suspect(*_cluster(400, 30.05, 31.24, 0.15)) == []
    assert gp._sheet_coords_suspect(*_cluster(299, 30.05, 31.24, 0.003)) == []     # أقل من 300: مش بنحكم
    g, a, t, la, lo = _cluster(300, 30.05, 31.24, 0.003)
    g[:] = None                                                                  # من غير محافظة معروفة: مستبعدين
    assert gp._sheet_coords_suspect(g, a, t, la, lo) == []


def test_sheet_coords_suspect_same_address_different_coords():
    g, areas, _, lat, lon = _cluster(330, 30.05, 31.24, 0.15, seed=3)
    addrs = np.array([f"شارع التحرير عمارة {i % 110}" for i in range(330)], dtype=object)   # كل عنوان 3 مرات بإحداثيات عشوائية
    areas[:] = "وسط البلد"
    msgs = gp._sheet_coords_suspect(g, areas, addrs, lat, lon)
    assert len(msgs) == 1 and "نفس العنوان" in msgs[0]


def test_loc_text_and_group_key():
    assert gp.loc_text_of("15 - حي الزهور - شارع النصر") == "زهور"
    assert gp.loc_text_of("شارع النصر بجوار المسجد") == "ش:نصر"
    assert gp.loc_text_of("منزل 12 عمارة السلام") == "نص:منزل عماره سلام"
    assert gp.loc_text_of("12 34") == "" and gp.loc_text_of("") == "" and gp.loc_text_of(None) == ""
    assert gp.group_key_of("cairo", "مدينة نصر", "شارع النصر بجوار المسجد") == "cairo|مدينه نصر|ش:نصر"
    assert gp.group_key_of("cairo", "x", "") == ""


@pytest.fixture
def offline(monkeypatch):
    """prepare من غير gazetteer ولا نت ولا انتظار."""
    monkeypatch.setattr(locdb, "find_gazetteer", lambda: None)
    monkeypatch.setattr(locdb, "GMAPS_GAP", 0)
    monkeypatch.setattr(locdb, "resolve_gmaps", lambda *a, **k: pytest.fail("network"))
    con = locdb.connect()
    yield con
    con.close()


def _centers_df():
    return pd.DataFrame({"Center Code": ["LT_GP1", "LT_GP1", "LT_GP2"], "Center Name": ["أ", "أ", "ب"],
                         "City": ["qena", "qena", "qena"], "Area": ["x", "x", "y"],
                         "Location": ["", "", "https://maps.app.goo.gl/zzz"],
                         "Latitude": [26.1601, 26.1601, None], "Longitude": [32.7201, 32.7201, None]})


def test_prepare_sheet_mode_precedence(offline):
    con = offline
    n_ov, n_sheet, n_out, n_nan, n_bad = _nid(), _nid(), _nid(), _nid(), "123"
    locdb.override_set(con, 26.17, 32.73, nid=n_ov)
    locdb.override_set(con, 26.18, 32.74, nid=_nid())                    # override لطالب مش في الشيت
    S = pd.DataFrame({"Student NID": [n_ov, n_sheet, n_out, n_nan, n_bad],
                      "gov": ["قنا"] * 5, "Area": ["x"] * 5, "Adress": ["شارع 1"] * 5,
                      "Latitude": [26.0, 26.15, 48.85, None, 26.2], "Longitude": [32.0, 32.71, 2.35, None, 32.8]})
    out = gp.prepare(S, _centers_df(), mode="sheet", con=con, use_google_links=False)
    R = out["S"]
    assert list(R["geo_source"]) == ["manual", "sheet", "missing", "missing", "sheet"]
    assert list(R["geo_level"]) == ["manual", "sheet", "", "", "sheet"]
    assert (R.loc[0, "Latitude"], R.loc[0, "Longitude"]) == (26.17, 32.73)     # التصحيح اليدوي غلب الشيت
    assert R.loc[0, "geo_conf"] == 1.0 and np.isnan(R.loc[1, "geo_conf"])
    assert (R.loc[1, "Latitude"], R.loc[1, "Longitude"]) == (26.15, 32.71)
    assert np.isnan(R.loc[2, "Latitude"]) and np.isnan(R.loc[3, "Longitude"])  # برا مصر = مالوش موقع
    assert "Latitude_sheet" not in R.columns
    assert any("2 طالب مالهمش موقع" in a for a in out["alerts"])
    assert out["info"]["students"]["by_source"] == {"sheet": 2, "missing": 2, "manual": 1}
    # المراكز: الأول من الشيت على كل الرومز، التاني من غير موقع (مفيش لينكات ولا gazetteer)
    C = out["C"]
    assert list(C["Latitude"].iloc[:2]) == [26.1601, 26.1601] and np.isnan(C["Latitude"].iloc[2])
    assert out["info"]["centers"]["with_coords"] == 1 and out["info"]["centers"]["total"] == 2
    assert any("1 مركز مالوش موقع" in a for a in out["alerts"])
    assert len(S) == 5 and "geo_source" not in S.columns                     # المدخلات ما اتعدلتش


def test_prepare_sheet_mode_without_coords_columns(offline):
    S = pd.DataFrame({"Student NID": [_nid()], "gov": ["قنا"]})
    out = gp.prepare(S, _centers_df(), mode="sheet", con=offline, use_google_links=False)
    assert list(out["S"]["geo_source"]) == ["missing"] and "Latitude" in out["S"].columns


def test_prepare_manual_center_beats_sheet(offline):
    con = offline
    gp.prepare(pd.DataFrame({"x": [1]}), _centers_df(), mode="sheet", con=con, use_google_links=False)
    locdb.center_set(con, "LT_GP1", 26.1655, 32.7255)
    out = gp.prepare(pd.DataFrame({"x": [1]}), _centers_df(), mode="sheet", con=con, use_google_links=False)
    assert out["C"]["Latitude"].iloc[0] == 26.1655
    assert any("LT_GP1" in w and "التصحيح المحفوظ" in w for w in out["warnings"])


def test_prepare_addr_mode_keeps_sheet_columns(offline):
    n_ov = _nid()
    locdb.override_set(offline, 26.17, 32.73, nid=n_ov)
    S = pd.DataFrame({"Student NID": [n_ov, _nid()], "gov": ["Atlantis", "Atlantis"], "Area": ["x", "x"],
                      "Adress": ["a", "b"], "Latitude": [26.0, 26.15], "Longitude": [32.0, 32.71]})
    R = gp.prepare(S, _centers_df(), mode="addr", con=offline, use_google_links=False)["S"]
    assert list(R["Latitude_sheet"]) == [26.0, 26.15] and list(R["Longitude_sheet"]) == [32.0, 32.71]
    assert list(R["geo_source"]) == ["manual", "missing"]                        # addr: الشيت ما بيتاخدش
    assert R.loc[0, "Latitude"] == 26.17 and np.isnan(R.loc[1, "Latitude"])


def test_prepare_addr_mode_errors(offline):
    S = pd.DataFrame({"Student NID": [_nid()], "Latitude": [None], "Longitude": [None]})
    with pytest.raises(gp.GeoPrepError, match="gov"):
        gp.prepare(S, _centers_df(), mode="addr", con=offline, use_google_links=False)
    S = pd.DataFrame({"Student NID": [_nid()], "gov": ["القاهرة"], "Area": ["x"], "Adress": ["شارع 1"]})
    with pytest.raises(gp.GeoPrepError, match="gazetteer"):                    # محتاج geocoder ومفيش قاعدة أماكن
        gp.prepare(S, _centers_df(), mode="addr", con=offline, use_google_links=False)
    with pytest.raises(gp.GeoPrepError, match="Center Code"):
        gp.prepare(S, pd.DataFrame({"x": [1]}), mode="sheet", con=offline)


def test_prepare_auto_mode_flags_tight_sheet_coords(offline):
    g, areas, addrs, lat, lon = _cluster(320, 26.16, 32.72, 0.003, seed=7)
    S = pd.DataFrame({"Student NID": [_nid() for _ in range(320)], "gov": ["قنا"] * 320, "Area": areas, "Adress": addrs,
                      "Latitude": lat, "Longitude": lon})
    out = gp.prepare(S, _centers_df(), mode="auto", con=offline, use_google_links=False)
    assert (out["S"]["geo_source"] == "sheet").all()
    assert out["info"].get("sheet_coords_suspect") and any("تقريبية" in a for a in out["alerts"])
