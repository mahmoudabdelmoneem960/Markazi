"""الـgeocoder على قاعدة أماكن صغيرة متصنّعة (من غير الحاجة لـ gazetteer.sqlite الحقيقية)."""
import json
import sqlite3

import pytest

from distributor.core import geocoder as gc
from distributor.core import geonorm as gn

CAP = (31.04, 31.38)            # المنصورة


@pytest.fixture()
def gz(tmp_path):
    p = tmp_path / "gaz.sqlite"
    db = sqlite3.connect(p)
    db.executescript("""
        CREATE TABLE places  (id INTEGER PRIMARY KEY, kind TEXT, rank INTEGER, name TEXT, key TEXT, kfull TEXT, lat REAL, lon REAL, pop INTEGER, gov TEXT, src TEXT DEFAULT 'osm');
        CREATE TABLE streets (id INTEGER PRIMARY KEY, hw TEXT, name TEXT, key TEXT, kfull TEXT, lat REAL, lon REAL, gov TEXT);
        CREATE TABLE pois    (id INTEGER PRIMARY KEY, kind TEXT, subkind TEXT, name TEXT, key TEXT, kfull TEXT, lat REAL, lon REAL, gov TEXT);
        CREATE TABLE govs    (code TEXT PRIMARY KEY, name TEXT, bbox TEXT, rings TEXT);""")
    db.execute("INSERT INTO govs VALUES ('dakahlia', 'الدقهلية', ?, '[]')", (json.dumps([31.0, 30.7, 31.9, 31.4]),))

    def place(name, lat, lon, rank=4, kind="neighbourhood"):
        db.execute("INSERT INTO places(kind,rank,name,key,kfull,lat,lon,gov) VALUES (?,?,?,?,?,?,?, 'dakahlia')",
                   (kind, rank, name, gn.key(name, "place"), gn.key_full(name), lat, lon))

    def street(name, *pts):
        for lat, lon in pts:
            db.execute("INSERT INTO streets(hw,name,key,kfull,lat,lon,gov) VALUES ('residential',?,?,?,?,?, 'dakahlia')",
                       (name, gn.key(name, "street"), gn.key_full(name), lat, lon))

    place("المنصورة", *CAP, rank=1, kind="city")
    place("كفر بدواي", 31.10, 31.40, rank=3, kind="suburb")
    place("ميت خميس", 31.20, 31.40, rank=3, kind="suburb")
    street("شارع عفيفي علي", (31.101, 31.401), (31.102, 31.402))
    street("شارع عفيفي", (31.100, 31.460), (31.101, 31.461))
    street("شارع عفيفي علي حسن", (31.130, 31.430), (31.131, 31.431))
    street("شارع السويس", *[(31.20, 31.40 + 0.005 * i) for i in range(41)])          # شارع طويل ~20 كم
    street("شارع بورسعيد", (31.050, 31.390), (31.051, 31.391), (31.052, 31.392),
           (31.300, 31.700), (31.301, 31.701))                                          # نفس الاسم في مكانين بعيد عن بعض
    street("شارع الشهيد سامح", (31.050, 31.385), (31.051, 31.386))
    place("سعد زغلول", 30.80, 31.20, rank=4, kind="village")                         # قرية بعيدة على اسم شارع
    place("أبو المطامير", 31.15, 31.50, rank=2, kind="town")
    street("شارع سعد زغلول", (31.041, 31.381), (31.042, 31.382), (31.151, 31.501), (31.152, 31.502))
    street("شارع عرابي", (31.043, 31.383), (31.044, 31.384))
    street("شارع متفرع من شارع العربي", (31.080, 31.420), (31.081, 31.421))
    db.commit()
    db.close()
    return gc.Geocoder(str(p))


def km(r, pt):
    return gc._km(r["lat"], r["lon"], pt[0], pt[1])


def test_street_name_with_ali_is_not_cut(gz):
    r = gz.geocode("dakahlia", "كفر بدواي", "شارع عفيفى على - كفر بدواي")
    assert r["level"] == "street" and r["matched"] == "شارع عفيفي علي"
    assert km(r, (31.101, 31.401)) < 0.5


def test_soft_stop_word_never_fuzzy_matches_longer_street(gz):
    """"عفيفي علي" مش موجود بالظبط → نرجع لـ"عفيفي"، مش "عفيفي علي حسن" بالتشابه."""
    r = gz.geocode("dakahlia", "ميت خميس", "شارع عفيفي علي الناصيه")
    assert r["matched"] != "شارع عفيفي علي حسن"


def test_long_street_stays_near_the_area(gz):
    r = gz.geocode("dakahlia", "ميت خميس", "12 شارع السويس - ميت خميس")
    assert r["level"] == "street"
    assert km(r, (31.20, 31.40)) < 1.2


def test_bare_street_repeated_in_gov_goes_to_review(gz):
    r = gz.geocode("dakahlia", "", "شارع بورسعيد")
    assert r["level"] == "markaz" and r["confidence"] <= gc.LEVEL_CONF["markaz"]
    assert "متكرر" in r["matched"]


def test_bare_unique_street_keeps_street_level(gz):
    r = gz.geocode("dakahlia", "", "شارع الشهيد سامح")
    assert r["level"] == "street"


def test_repeated_street_with_area_keeps_street_level(gz):
    r = gz.geocode("dakahlia", "المنصورة", "شارع بورسعيد - المنصورة")
    assert r["level"] == "street" and km(r, (31.051, 31.391)) < 0.5


def test_street_name_is_not_taken_as_the_area(gz):
    """«ش سعد زغلول» في المنصورة: القرية اللي اسمها سعد زغلول (على بعد 30 كم) مش هي المنطقة."""
    r = gz.geocode("dakahlia", "المنصورة", "المنصورة - 28 ش سعد زغلول")
    assert r["level"] == "street" and km(r, (31.0415, 31.3815)) < 0.5


def test_real_area_in_address_beats_street_named_like_a_village(gz):
    r = gz.geocode("dakahlia", "", "116 شارع سعد زغلول - أبو المطامير")
    assert r["level"] == "street" and km(r, (31.1515, 31.5015)) < 0.5


def test_mutafarra_ends_the_street_name(gz):
    r = gz.geocode("dakahlia", "المنصورة", "71 شارع عرابى متفرع من الرئيسي - المنصورة")
    assert r["matched"] == "شارع عرابي"
