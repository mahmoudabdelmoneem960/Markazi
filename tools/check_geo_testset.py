"""فحص تحديد مواقع شيت الاختبار (tools/make_geo_testset.py) بنفس الـgeocoder اللي التوزيع بيستخدمه.
الصف بينجح لو:
  check=distance → النتيجة على بعد tolerance_km من الموقع الصحيح
  check=in_gov   → النتيجة جوه حدود المحافظة الصح
وفي الحالتين بنعلّم لو النتيجة وقعت في محافظة الفخ (مثلا شارع بورسعيد في المنصورة اتحط في بورسعيد).

الشيت Answer Key بيتقرا من نفس ملف الطلاب، والنتيجة بتتكتب في نفس الملف (شيتات Results وSummary).

الاستخدام: python tools/check_geo_testset.py [students.xlsx]"""
import json
import math
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from distributor.core import geocoder as gc  # noqa: E402
from distributor.services import sheets  # noqa: E402
from distributor.core import engine  # noqa: E402

STU = sys.argv[1] if len(sys.argv) > 1 else os.path.join("sample", "students_geo_test.xlsx")
GAZ = os.path.join("data", "gazetteer.sqlite")
APP_DB = os.path.join("data", "app.db")


def km(a, b):
    p = math.pi / 180
    x = (b[1] - a[1]) * p * math.cos((a[0] + b[0]) / 2 * p)
    return 6371 * math.hypot(x, (b[0] - a[0]) * p)


def _in_ring(lon, lat, ring):
    inside, j = False, len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


_GOVS = {}


def gov_of(lat, lon, gaz=GAZ):
    """المحافظة اللي النقطة جوه حدودها (من جدول govs في قاعدة الأماكن)."""
    if not _GOVS:
        _GOVS.update({code: json.loads(rings) for code, rings in sqlite3.connect(gaz).execute("SELECT code, rings FROM govs")})
    for code, polys in _GOVS.items():
        for poly in polys:
            if poly and _in_ring(lon, lat, poly[0]) and not any(_in_ring(lon, lat, h) for h in poly[1:]):
                return code
    return None


def main():
    # نفس تجهيز الشيت والمرساة بتاعة التوزيع: adapt_students + مواقع المراكز اللي في الـDB
    with open(STU, "rb") as f:
        S, _ = sheets.adapt_students(sheets.parse_file(STU, f.read()))
    K = pd.read_excel(STU, sheet_name="Answer Key", dtype={"national_id": str})
    anchors = []
    if os.path.exists(APP_DB):
        anchors = [{"code": r[0], "gov": r[1], "area": r[2], "lat": r[3], "lon": r[4]}
                   for r in sqlite3.connect(APP_DB).execute("SELECT code, gov, area, lat, lon FROM centers WHERE lat IS NOT NULL")]
    gz = gc.Geocoder(GAZ, centers=anchors)

    by_nid = {r["Student NID"]: r for _, r in S.iterrows()}
    out = []
    for _, k in K.iterrows():
        s = by_nid[k["national_id"]]
        g = engine.gov_code(s["gov"])
        res = gz.geocode(g, s["Area"], s["Adress"])
        row = {"id": k["id"], "full_name_ar": k["full_name_ar"], "scenario": k["scenario"], "gov": k["gov"], "area": k["area"] if pd.notna(k["area"]) else "",
               "address": k["address"] if pd.notna(k["address"]) else "", "level": res["level"], "confidence": res["confidence"],
               "matched": res.get("matched", ""), "anchor": res.get("anchor", ""), "lat": res["lat"], "lon": res["lon"],
               "system_map": f"https://www.google.com/maps?q={res['lat']:.6f},{res['lon']:.6f}" if res["lat"] is not None else "",
               "expected_map": k["expected_map"] if pd.notna(k["expected_map"]) else ""}
        got_gov = gov_of(res["lat"], res["lon"]) if res["lat"] is not None else None
        row["landed_gov"] = got_gov
        if res["lat"] is None:
            row["error_km"], row["pass"] = None, False
        elif k["check"] == "distance":
            row["error_km"] = round(km((k["expected_lat"], k["expected_lon"]), (res["lat"], res["lon"])), 2)
            row["pass"] = row["error_km"] <= k["tolerance_km"]
        else:
            row["error_km"], row["pass"] = None, got_gov == k["gov"]
        row["fell_in_trap"] = bool(pd.notna(k["trap_gov"]) and got_gov == k["trap_gov"])
        row["wrong_gov"] = got_gov is not None and got_gov != k["gov"]
        row["note"] = k["note"]
        out.append(row)

    R = pd.DataFrame(out)
    summary = R.groupby("scenario").agg(rows=("pass", "size"), passed=("pass", "sum"), wrong_gov=("wrong_gov", "sum"),
                                         fell_in_trap=("fell_in_trap", "sum"), median_err_km=("error_km", "median")).reset_index()
    summary.loc[len(summary)] = ["TOTAL", len(R), int(R["pass"].sum()), int(R["wrong_gov"].sum()), int(R["fell_in_trap"].sum()),
                                 R["error_km"].median()]
    with pd.ExcelWriter(STU, mode="a", engine="openpyxl", if_sheet_exists="replace") as w:
        R.sort_values(["pass", "scenario"]).to_excel(w, sheet_name="Results", index=False)
        summary.to_excel(w, sheet_name="Summary", index=False)
    pd.set_option("display.width", 200)
    print(summary.to_string(index=False))
    print("->", STU, "(شيتات Results وSummary)")


if __name__ == "__main__":
    main()
