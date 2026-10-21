"""دمج أماكن GeoNames (الأماكن المأهولة بأسماء عربية) في gazetteer.sqlite بدون تكرار الأماكن الموجودة في OSM.
الاستخدام: python add_geonames.py gazetteer.sqlite EG.txt
(آمن للتكرار: بيمسح صفوف geonames القديمة الأول)"""
import csv
import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # جذر المشروع
from distributor.core import geonorm as gn  # noqa: E402

DB, TXT = sys.argv[1], sys.argv[2]
csv.field_size_limit(10 ** 7)
db = sqlite3.connect(DB)
cols = [r[1] for r in db.execute("PRAGMA table_info(places)")]
if "src" not in cols:
    db.execute("ALTER TABLE places ADD COLUMN src TEXT DEFAULT 'osm'")
db.execute("DELETE FROM places WHERE src='geonames'")

# حدود المحافظات (نفس منطق البناء)
gov_polys = {code: {"bbox": json.loads(bb), "polys": json.loads(rg)} for code, bb, rg in db.execute("SELECT code,bbox,rings FROM govs")}


def _ring_inside(x, y, ring):
    c = False
    n = len(ring)
    j = n - 1
    for i in range(n):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-300) + xi:
            c = not c
        j = i
    return c


def gov_of(lat, lon):
    for code, g in gov_polys.items():
        b = g["bbox"]
        if b[0] <= lon <= b[2] and b[1] <= lat <= b[3]:
            for p in g["polys"]:
                if _ring_inside(lon, lat, p[0]) and not any(_ring_inside(lon, lat, h) for h in p[1:]):
                    return code
    return None


def km(la1, lo1, la2, lo2):
    return math.hypot((la2 - la1) * 110.57, (lo2 - lo1) * 111.32 * math.cos(math.radians((la1 + la2) / 2)))


# مفاتيح OSM الموجودة (للتخلص من التكرار: نفس المفتاح وأقل من 3 كم)
existing = {}
for key, lat, lon in db.execute("SELECT key,lat,lon FROM places WHERE src='osm' OR src IS NULL"):
    existing.setdefault(key, []).append((lat, lon))

KIND = {"PPLA": ("town", 2), "PPLA2": ("town", 2), "PPLA3": ("town", 3), "PPLC": ("city", 1), "PPL": ("village", 4),
        "PPLX": ("neighbourhood", 4), "PPLL": ("hamlet", 5), "PPLF": ("farm", 6), "PPLQ": ("locality", 6), "PPLH": ("locality", 6)}
rows, skipped_dup, no_ar = [], 0, 0
with open(TXT, encoding="utf-8", newline="") as f:
    for r in csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
        if len(r) < 15 or r[6] != "P":
            continue
        kind, rank = KIND.get(r[7], ("village", 4))
        lat, lon = float(r[4]), float(r[5])
        pop = int(r[14]) if r[14].isdigit() else None
        names = [n for n in [r[1]] + r[3].split(",") if gn.has_arabic(n)]
        if not names:
            no_ar += 1
            continue
        gov = gov_of(lat, lon)
        seen = set()
        for nm in names:
            k = gn.key(nm, "place")
            if not k or k in seen:
                continue
            seen.add(k)
            if any(km(lat, lon, la, lo) < 3.0 for la, lo in existing.get(k, [])):
                skipped_dup += 1
                continue
            rows.append((kind, rank, nm, k, gn.key_full(nm), lat, lon, pop, gov, "geonames"))
db.executemany("INSERT INTO places(kind,rank,name,key,kfull,lat,lon,pop,gov,src) VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
db.execute("CREATE INDEX IF NOT EXISTS ix_places_src ON places(src)")
db.commit()
n_osm = db.execute("SELECT COUNT(*) FROM places WHERE src='osm' OR src IS NULL").fetchone()[0]
print(f"geonames rows added: {len(rows):,} | skipped duplicates of OSM (same name <3 km): {skipped_dup:,} | places without Arabic name skipped: {no_ar:,}")
print(f"places now: OSM {n_osm:,} + GeoNames {len(rows):,} = {n_osm + len(rows):,}")
print("by governorate (geonames):", dict(db.execute("SELECT COALESCE(gov,'NULL'),COUNT(*) FROM places WHERE src='geonames' GROUP BY gov ORDER BY 2 DESC LIMIT 8").fetchall()))
db.close()
