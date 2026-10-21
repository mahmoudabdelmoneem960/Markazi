"""بناء قاعدة أماكن (gazetteer) من ملف OSM لمصر: أماكن + شوارع + معالم + حدود المحافظات.
الاستخدام (بالـ venv اللي فيه osmium):
    python build_gazetteer.py egypt.osm.pbf gazetteer.sqlite
"""
import json
import os
import sqlite3
import sys
import time

import osmium as o

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))      # جذر المشروع
from distributor.core import geonorm as gn  # noqa: E402

PBF, OUT = sys.argv[1], sys.argv[2]
T0 = time.time()


def log(m):
    print(f"[{time.time() - T0:6.0f}s] {m}", flush=True)


PLACE_RANK = {"city": 1, "town": 2, "borough": 3, "suburb": 3, "quarter": 3, "neighbourhood": 4, "village": 4,
              "hamlet": 5, "isolated_dwelling": 6, "locality": 6, "farm": 6, "city_block": 5, "district": 3}
HIGHWAYS = {"motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential", "living_street",
            "pedestrian", "road", "primary_link", "secondary_link", "tertiary_link", "trunk_link", "service"}
POI_AMENITY_SKIP = {"parking", "bench", "waste_basket", "bicycle_parking", "parking_space", "parking_entrance",
                    "shelter", "vending_machine", "toilets", "drinking_water", "recycling", "telephone", "atm"}
NAME_KEYS = ("name:ar", "name", "alt_name:ar", "alt_name", "old_name:ar", "official_name:ar", "short_name:ar", "name:en")


def names_of(tags):
    """كل أشكال الاسم (عربي أولًا)، بدون تكرار."""
    seen, out = set(), []
    for k in NAME_KEYS:
        v = tags.get(k)
        if not v:
            continue
        for part in str(v).split(";"):
            part = part.strip()
            if part and part not in seen:
                if k == "name:en" and out:           # الإنجليزي بس لو مفيش حاجة تانية
                    continue
                seen.add(part)
                out.append(part)
    return out


def ring_mean(area):
    sx = sy = n = 0
    for ring in area.outer_rings():
        for nd in ring:
            sx += nd.lon
            sy += nd.lat
            n += 1
        if n:
            break
    return (sy / n, sx / n) if n else None


places, streets, pois, govs = [], [], [], []
counts = {"nodes_seen": 0, "ways_seen": 0, "areas_seen": 0, "bad_loc": 0}
geo = o.geom.GeoJSONFactory()

fp = (o.FileProcessor(PBF)
      .with_locations()
      .with_areas(o.filter.TagFilter(("admin_level", "4")))
      .with_filter(o.filter.KeyFilter("name", "name:ar"))
      .with_filter(o.filter.KeyFilter("place", "highway", "amenity", "shop", "tourism", "leisure", "office",
                                      "healthcare", "admin_level")))
log("reading PBF (two passes: relations first, then everything)...")
last = time.time()
for obj in fp:
    if time.time() - last > 30:
        last = time.time()
        log(f"progress: places={len(places):,} streets={len(streets):,} pois={len(pois):,} govs={len(govs)}")
    try:
        tags = obj.tags
        if obj.is_area():
            counts["areas_seen"] += 1
            if not obj.from_way() and tags.get("admin_level") == "4" and tags.get("boundary") in ("administrative", None):
                code = gn.gov_code(tags.get("name:ar") or tags.get("name"))
                if code:
                    gj = json.loads(geo.create_multipolygon(obj))
                    govs.append((code, tags.get("name:ar") or tags.get("name"), tags.get("name:en"), obj.orig_id(), gj))
                    log(f"governorate polygon: {code} ({tags.get('name:ar') or tags.get('name')})")
                continue
            place = tags.get("place")
            if place in PLACE_RANK:
                c = ring_mean(obj)
                if c:
                    for nm in names_of(tags):
                        places.append((place, PLACE_RANK[place], nm, c[0], c[1], tags.get("population")))
            elif any(tags.get(k) for k in ("amenity", "shop", "tourism", "leisure", "office", "healthcare")):
                c = ring_mean(obj)
                if c:
                    kind = next(k for k in ("amenity", "shop", "tourism", "leisure", "office", "healthcare") if tags.get(k))
                    if not (kind == "amenity" and tags.get(kind) in POI_AMENITY_SKIP):
                        for nm in names_of(tags):
                            pois.append((kind, tags.get(kind), nm, c[0], c[1]))
        elif obj.is_node():
            counts["nodes_seen"] += 1
            place = tags.get("place")
            if place in PLACE_RANK:
                for nm in names_of(tags):
                    places.append((place, PLACE_RANK[place], nm, obj.lat, obj.lon, tags.get("population")))
                continue
            for k in ("amenity", "shop", "tourism", "leisure", "office", "healthcare"):
                if tags.get(k):
                    if not (k == "amenity" and tags.get(k) in POI_AMENITY_SKIP):
                        for nm in names_of(tags):
                            pois.append((k, tags.get(k), nm, obj.lat, obj.lon))
                    break
        elif obj.is_way():
            counts["ways_seen"] += 1
            hw = tags.get("highway")
            if hw in HIGHWAYS and hw != "service" or (hw == "service" and tags.get("name")):
                nds = obj.nodes
                if len(nds) >= 2:
                    mid = nds[len(nds) // 2]
                    for nm in names_of(tags):
                        streets.append((hw, nm, mid.lat, mid.lon))
    except o.InvalidLocationError:
        counts["bad_loc"] += 1
    except Exception as e:  # noqa
        counts["bad_loc"] += 1
        if counts["bad_loc"] < 5:
            log(f"warn: {type(e).__name__}: {e}")

log(f"parsed: places={len(places):,} streets={len(streets):,} pois={len(pois):,} govs={len(govs)} | {counts}")

# ---------------- حدود المحافظات: تبسيط + point-in-polygon ----------------


def dp(points, tol):
    """Douglas-Peucker (تكراري)."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        ax, ay = points[a]
        bx, by = points[b]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        mi, md = -1, 0.0
        for i in range(a + 1, b):
            px, py = points[i]
            if den == 0:
                d = ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
            else:
                t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / den))
                d = ((px - (ax + t * dx)) ** 2 + (py - (ay + t * dy)) ** 2) ** 0.5
            if d > md:
                mi, md = i, d
        if md > tol:
            keep[mi] = True
            stack += [(a, mi), (mi, b)]
    return [p for p, k in zip(points, keep) if k]


gov_polys = {}      # code -> {"bbox":(minx,miny,maxx,maxy), "polys":[ [outer, hole1, ...], ... ]}
for code, nm_ar, nm_en, osm_id, gj in govs:
    polys = []
    mp = gj["coordinates"] if gj["type"] == "MultiPolygon" else [gj["coordinates"]]
    for poly in mp:
        polys.append([dp([(x, y) for x, y in ring], 0.0003) for ring in poly])      # الحدود أصلًا خشنة: تبسيط خفيف
    xs = [x for p in polys for x, _ in p[0]]
    ys = [y for p in polys for _, y in p[0]]
    g = gov_polys.setdefault(code, {"bbox": (min(xs), min(ys), max(xs), max(ys)), "polys": [], "name": nm_ar, "osm_id": osm_id})
    g["polys"] += polys                           # علاقات مكررة لنفس المحافظة: كل واحدة لوحدها (مش even-odd مشترك)
    b = g["bbox"]
    g["bbox"] = (min(b[0], min(xs)), min(b[1], min(ys)), max(b[2], max(xs)), max(b[3], max(ys)))
log(f"governorate polygons: {len(gov_polys)} govs, polygons {sum(len(g['polys']) for g in gov_polys.values())}, "
    f"vertices {sum(len(r) for g in gov_polys.values() for p in g['polys'] for r in p):,}")


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


def inside(x, y, polys):
    for p in polys:
        if _ring_inside(x, y, p[0]) and not any(_ring_inside(x, y, h) for h in p[1:]):
            return True
    return False

_cell = {}


def gov_of(lat, lon):
    k = (int(lat * 200), int(lon * 200))               # خلية ≈ 550 م
    if k in _cell:
        return _cell[k]
    x, y = (k[1] + 0.5) / 200, (k[0] + 0.5) / 200
    res = None
    for code, g in gov_polys.items():
        b = g["bbox"]
        if b[0] <= x <= b[2] and b[1] <= y <= b[3] and inside(x, y, g["polys"]):
            res = code
            break
    _cell[k] = res
    return res


# ---------------- كتابة SQLite ----------------
if os.path.exists(OUT):
    os.remove(OUT)
db = sqlite3.connect(OUT)
db.executescript("""
CREATE TABLE places  (id INTEGER PRIMARY KEY, kind TEXT, rank INTEGER, name TEXT, key TEXT, kfull TEXT, lat REAL, lon REAL, pop INTEGER, gov TEXT);
CREATE TABLE streets (id INTEGER PRIMARY KEY, hw TEXT, name TEXT, key TEXT, kfull TEXT, lat REAL, lon REAL, gov TEXT);
CREATE TABLE pois    (id INTEGER PRIMARY KEY, kind TEXT, subkind TEXT, name TEXT, key TEXT, kfull TEXT, lat REAL, lon REAL, gov TEXT);
CREATE TABLE govs    (code TEXT PRIMARY KEY, name TEXT, bbox TEXT, rings TEXT);
CREATE TABLE meta    (k TEXT PRIMARY KEY, v TEXT);
""")
t = time.time()
rows = []
for kind, rank, nm, la, lo, pop in places:
    k = gn.key(nm, "place")
    if k:
        try:
            p = int(str(pop).replace(",", "")) if pop else None
        except ValueError:
            p = None
        rows.append((kind, rank, nm, k, gn.key_full(nm), la, lo, p, gov_of(la, lo)))
db.executemany("INSERT INTO places(kind,rank,name,key,kfull,lat,lon,pop,gov) VALUES (?,?,?,?,?,?,?,?,?)", rows)
log(f"places written: {len(rows):,}")
rows = []
for hw, nm, la, lo in streets:
    k = gn.key(nm, "street")
    if k:
        rows.append((hw, nm, k, gn.key_full(nm), la, lo, gov_of(la, lo)))
db.executemany("INSERT INTO streets(hw,name,key,kfull,lat,lon,gov) VALUES (?,?,?,?,?,?,?)", rows)
log(f"streets written: {len(rows):,}")
rows = []
for kind, sub, nm, la, lo in pois:
    k = gn.key(nm, "poi")
    if k:
        rows.append((kind, sub, nm, k, gn.key_full(nm), la, lo, gov_of(la, lo)))
db.executemany("INSERT INTO pois(kind,subkind,name,key,kfull,lat,lon,gov) VALUES (?,?,?,?,?,?,?,?)", rows)
log(f"pois written: {len(rows):,}")
for code, g in gov_polys.items():
    db.execute("INSERT INTO govs VALUES (?,?,?,?)", (code, g["name"], json.dumps(g["bbox"]), json.dumps(g["polys"])))
db.execute("INSERT INTO meta VALUES ('built_from', ?)", (os.path.basename(PBF),))
db.execute("INSERT INTO meta VALUES ('built_at', ?)", (time.strftime("%Y-%m-%d %H:%M"),))
db.executescript("""
CREATE INDEX ix_places_key ON places(gov, key);  CREATE INDEX ix_places_rank ON places(gov, rank);
CREATE INDEX ix_streets_key ON streets(gov, key); CREATE INDEX ix_streets_lat ON streets(gov, lat);
CREATE INDEX ix_pois_key ON pois(gov, key);       CREATE INDEX ix_pois_lat ON pois(gov, lat);
""")
db.commit()
unk = {t: db.execute(f"SELECT COUNT(*) FROM {t} WHERE gov IS NULL").fetchone()[0] for t in ("places", "streets", "pois")}
log(f"done. entries with no governorate (outside polygons): {unk}; file {os.path.getsize(OUT) / 1e6:.0f} MB")
db.close()


