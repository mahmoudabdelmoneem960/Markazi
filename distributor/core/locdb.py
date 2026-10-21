"""قاعدة بيانات المواقع (SQLite، ملف واحد): المراكز، كاش مواقع الطلاب، والتصحيحات اليدوية.

- centers: موقع كل مركز مرة واحدة (مش على كل روم) + مصدره (شيت / لينك Google / يدوي / geocoder) + فحوصات
- geo_cache: نتايج الـ geocoder للعناوين (بمستوى الدقة والثقة) عشان تتحسب مرة واحدة وتفضل ثابتة
- overrides: تصحيحات يدوية لطلاب (بالرقم القومي) وللعناوين، وبتتغلب على أي نتيجة أوتوماتيك
"""
import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from distributor import config
from distributor.core import geonorm as gn

APP_DIR = str(config.BASE_DIR)
DB_PATH = str(config.DB_PATH)
EGYPT_BBOX = (21.5, 31.9, 24.5, 37.3)          # lat_min, lat_max, lon_min, lon_max
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS centers (
  code TEXT PRIMARY KEY, name TEXT, gov TEXT, area TEXT, address TEXT, gmaps_url TEXT,
  lat REAL, lon REAL, source TEXT, verified INTEGER DEFAULT 0,
  link_lat REAL, link_lon REAL, link_diff_m REAL, flags TEXT, note TEXT, updated_at TEXT, link_name TEXT,
  google_lat REAL, google_lon REAL, google_type TEXT, google_addr TEXT, google_gap_m REAL, google_verdict TEXT, google_at TEXT);
CREATE TABLE IF NOT EXISTS geo_cache (
  key TEXT PRIMARY KEY, gov TEXT, area TEXT, address TEXT, lat REAL, lon REAL, level TEXT,
  confidence REAL, matched TEXT, source TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS overrides (
  nid TEXT PRIMARY KEY, lat REAL, lon REAL, note TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS addr_overrides (
  key TEXT PRIMARY KEY, lat REAL, lon REAL, note TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS group_overrides (
  gkey TEXT PRIMARY KEY, gov TEXT, area TEXT, text TEXT, lat REAL, lon REAL, note TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


def now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def connect(path=None):
    path = path or DB_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        with _lock:                               # الإعداد والترحيل (ALTER) لازم يتسلسلوا: الصفحة بتفتح أكتر من اتصال في نفس اللحظة
            for attempt in range(12):             # تحويل الـDB لـWAL أول مرة محتاج قفل: نعيد المحاولة لو عملية تانية ماسكاه
                try:
                    if str(con.execute("PRAGMA journal_mode").fetchone()[0]).lower() != "wal":
                        con.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as e:
                    if attempt == 11 or ("lock" not in str(e).lower() and "busy" not in str(e).lower()):
                        raise
                    time.sleep(0.3)
            con.executescript(SCHEMA)
            have = {r["name"] for r in con.execute("PRAGMA table_info(centers)")}
            for col, typ in (("link_name", "TEXT"), ("google_lat", "REAL"), ("google_lon", "REAL"), ("google_type", "TEXT"),
                             ("google_addr", "TEXT"), ("google_gap_m", "REAL"), ("google_verdict", "TEXT"), ("google_at", "TEXT")):
                if col not in have:                                                           # قاعدة قديمة
                    try:
                        con.execute(f"ALTER TABLE centers ADD COLUMN {col} {typ}")
                    except sqlite3.OperationalError as e:
                        if "duplicate column" not in str(e).lower():                          # عملية تانية سبقتنا
                            raise
            con.commit()
    except Exception:
        con.close()                               # ما نسيبش اتصال مفتوح لو الإعداد فشل
        raise
    return con


def find_gazetteer():
    """مسار قاعدة الأماكن (OSM + GeoNames) لو موجودة."""
    cands = config.GAZETTEER_CANDIDATES
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def gazetteer_fingerprint(path):
    if not path or not os.path.exists(path):
        return None
    st = os.stat(path)
    return f"{st.st_size}-{int(st.st_mtime)}"


# ---------------- قراءة الإحداثيات من نص ----------------
def km(la1, lo1, la2, lo2):
    return math.hypot((la2 - la1) * 110.57, (lo2 - lo1) * 111.32 * math.cos(math.radians((la1 + la2) / 2)))


def in_egypt(lat, lon):
    return lat is not None and lon is not None and EGYPT_BBOX[0] <= lat <= EGYPT_BBOX[1] and EGYPT_BBOX[2] <= lon <= EGYPT_BBOX[3]


def parse_coords(text, strict=False):
    """بيقرا إحداثيات من: 'lat, lon' أو لينك Google طويل. بيرجّع (lat, lon, ملاحظة) أو None.
    ترتيب الأفضلية في اللينك: دبوس المكان (!3d..!4d..) ثم q=lat,lon ثم مركز الخريطة (@lat,lon,Zz).
    مركز الخريطة بيبقى هو المكان بس لو الزووم قريب (15+)، وفي وضع strict (للقراءة الأوتوماتيك من لينكات الشيت)
    بنرفض أي مركز خريطة بزووم بعيد لأنه مش موقع مكان. لو الأرقام اتكتبت بالعكس (lon, lat) بيبدلها."""
    if text is None:
        return None
    t = urllib.parse.unquote(str(text).strip()).translate(gn._DIGITS).replace("٫", ".").replace("،", ",")
    pairs = []
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", t)
    if m:
        pairs.append((float(m.group(1)), float(m.group(2))))
    if not pairs:
        m = re.search(r"[?&](?:q|ll|query|destination" + ("" if strict else "|center") + r")=(-?\d+\.\d+)[, ]\s*(-?\d+\.\d+)", t)
        if m:
            pairs.append((float(m.group(1)), float(m.group(2))))
    if not pairs:
        m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)(?:,(\d+(?:\.\d+)?)z)?", t)
        if m and (not strict or (m.group(3) and float(m.group(3)) >= 15)):
            pairs.append((float(m.group(1)), float(m.group(2))))
    if not pairs and not strict:
        m = re.fullmatch(r"\s*(-?\d{1,3}(?:\.\d+)?)\s*[,;\s]\s*(-?\d{1,3}(?:\.\d+)?)\s*", t)
        if m:
            pairs.append((float(m.group(1)), float(m.group(2))))
    if not pairs:
        return None
    a, b = pairs[0]
    if in_egypt(a, b):
        return a, b, ""
    if in_egypt(b, a):
        return b, a, "اتبدّلت (كانت lon, lat)"
    return None


GMAPS_GAP = 5          # ثواني بين لينكين Google: أسرع من كده جوجل بيرجّع 429
_GM_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
               "Accept-Language": "ar,en;q=0.8"}


def place_name_from_url(url):
    """اسم المكان من لينك Google (/maps/place/<الاسم>/@...) أو ''."""
    m = re.search(r"/maps/place/([^/@?]+)", str(url or ""))
    if not m:
        return ""
    name = urllib.parse.unquote(m.group(1)).replace("+", " ")
    return re.sub(r"[‎‏‪-‮⁦-⁩]", "", name).strip()


def resolve_gmaps_ex(url, tries=3):
    """لينك Google Maps (طويل أو مختصر maps.app.goo.gl) -> (lat, lon, اسم المكان) أو None.
    بنقرا اللينك بعد التحويل بس (دبوس المكان !3d!4d أو مركز خريطة بزووم قريب). لو اللينك راجع صفحة بحث أو مكان من غير
    إحداثيات بنرجّع None ومش بنخمّن (مركز الخريطة الافتراضي في الصفحة بيبقى بعيد مئات الكيلومترات).
    بيشتغل ببطء ومؤدب (جوجل بيحظر لو الطلبات كتير)."""
    if not url or not str(url).startswith("http"):
        return None
    got = parse_coords(url, strict=True)
    if got:
        return got[0], got[1], place_name_from_url(url)
    for attempt in range(tries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=_GM_HEADERS), timeout=25)
            final = r.geturl()
            g = parse_coords(final, strict=True)
            return (g[0], g[1], place_name_from_url(final)) if g else None
        except urllib.error.HTTPError as e:
            time.sleep(4 * (attempt + 1) if e.code == 429 else 2)
        except Exception:  # noqa
            time.sleep(2)
    return None


def resolve_gmaps(url, tries=3):
    """نفس resolve_gmaps_ex بس بيرجّع (lat, lon) أو None."""
    got = resolve_gmaps_ex(url, tries)
    return (got[0], got[1]) if got else None


# ---------------- المراكز ----------------
def _find_col(df, pred):
    for c in df.columns:
        if pred(str(c).strip()):
            return c
    return None


def _s(v):
    """نص من خلية الشيت: الفاضي/NaN/None بيبقى '' (مش كلمة 'nan')."""
    if v is None:
        return ""
    if isinstance(v, float) and math.isnan(v):
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "null") else s


def center_records(C):
    """من شيت المراكز (صف لكل روم) -> قاموس مركز واحد لكل Center Code (أول إحداثيات موجودة)."""
    import pandas as pd
    C = C.copy()
    C.columns = [str(c).strip() for c in C.columns]
    lat_c = _find_col(C, lambda c: c.lower() in ("lat", "latitude", "خط العرض"))
    lon_c = _find_col(C, lambda c: c.lower() in ("lon", "lng", "long", "longitude", "خط الطول"))
    addr_c = _find_col(C, lambda c: "عنوان" in c or c.lower() in ("address", "full_address"))
    url_c = next((c for c in C.columns if C[c].astype(str).str.startswith("http").any()), None)
    name_c = "Center Name" if "Center Name" in C.columns else None
    out = {}
    for code, g in C.groupby("Center Code", sort=False):
        rec = {"code": str(code), "gov": _s(g["City"].iloc[0]).lower() if "City" in g else "",
               "area": _s(g["Area"].iloc[0]) if "Area" in g else "", "name": _s(g[name_c].iloc[0]) if name_c else "",
               "address": _s(g[addr_c].dropna().iloc[0]) if addr_c and g[addr_c].notna().any() else "",
               "url": "", "lat": None, "lon": None}
        if url_c is not None:
            urls = g[url_c].dropna().astype(str)
            urls = urls[urls.str.startswith("http")]
            rec["url"] = urls.iloc[0] if len(urls) else ""
        if lat_c is not None and lon_c is not None:
            la = pd.to_numeric(g[lat_c], errors="coerce")
            lo = pd.to_numeric(g[lon_c], errors="coerce")
            ok = la.notna() & lo.notna()
            if ok.any():
                rec["lat"], rec["lon"] = float(la[ok].iloc[0]), float(lo[ok].iloc[0])
        out[rec["code"]] = rec
    return out


def validate_center(con, code, gov, lat, lon):
    """فحوصات منطقية لموقع مركز. بيرجّع قايمة تحذيرات (فاضية = سليم)."""
    flags = []
    if lat is None or lon is None:
        return ["مفيش إحداثيات"]
    if not in_egypt(lat, lon):
        flags.append("برا حدود مصر")
    if abs(lat - lon) < 1e-9:
        flags.append("خط العرض = خط الطول")
    for r in con.execute("SELECT code FROM centers WHERE code<>? AND lat IS NOT NULL AND ABS(lat-?)<1e-6 AND ABS(lon-?)<1e-6",
                         (code, lat, lon)):
        flags.append(f"نفس إحداثيات المركز {r['code']}")
    peers = [(r["lat"], r["lon"]) for r in con.execute(
        "SELECT lat,lon FROM centers WHERE code<>? AND gov=? AND lat IS NOT NULL", (code, gov))]
    if len(peers) >= 3:
        med_la = sorted(p[0] for p in peers)[len(peers) // 2]
        med_lo = sorted(p[1] for p in peers)[len(peers) // 2]
        d = km(lat, lon, med_la, med_lo)
        if d > 80:
            flags.append(f"بعيد {int(d)} كم عن باقي مراكز المحافظة")
    return flags


def centers_upsert(C, source="sheet", con=None):
    """بيحدّث جدول المراكز من شيت المراكز. التصحيح اليدوي ما بيتمسحش. بيرجّع تقرير."""
    own = con is None
    con = con or connect()
    rep = {"new": 0, "updated_coords": 0, "kept_manual": 0, "no_coords": 0, "differs_from_manual": [], "moved": []}
    with _lock:
        for rec in center_records(C).values():
            row = con.execute("SELECT * FROM centers WHERE code=?", (rec["code"],)).fetchone()
            lat, lon = rec["lat"], rec["lon"]
            if row is None:
                con.execute("INSERT INTO centers(code,name,gov,area,address,gmaps_url,lat,lon,source,verified,updated_at) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (rec["code"], rec["name"], rec["gov"], rec["area"], rec["address"], rec["url"], lat, lon,
                             source if lat is not None else None, 0, now()))
                rep["new"] += 1
                if lat is None:
                    rep["no_coords"] += 1
                continue
            con.execute("UPDATE centers SET name=?, gov=?, area=?, address=COALESCE(NULLIF(?, ''), address), "
                        "gmaps_url=COALESCE(NULLIF(?, ''), gmaps_url) WHERE code=?",
                        (rec["name"], rec["gov"], rec["area"], rec["address"], rec["url"], rec["code"]))
            if lat is None:
                if row["lat"] is None:
                    rep["no_coords"] += 1
                continue
            if row["source"] in ("manual", "google"):          # تصحيح يدوي أو متأكد بجوجل: الشيت ما بيغيّرهوش
                rep["kept_manual"] += 1
                if row["lat"] is not None and km(lat, lon, row["lat"], row["lon"]) > 0.2:
                    rep["differs_from_manual"].append(rec["code"])
                continue
            if row["lat"] is None or km(lat, lon, row["lat"], row["lon"]) > 0.002:
                if row["lat"] is not None and km(lat, lon, row["lat"], row["lon"]) > 0.05:
                    rep["moved"].append(rec["code"])
                # الموقع اتغيّر من الشيت: أي "متأكد" وأي فحص جوجل/لينك قديم ما بقاش ينطبق على الموقع الجديد
                ld = km(lat, lon, row["link_lat"], row["link_lon"]) * 1000 if row["link_lat"] is not None else None
                con.execute("UPDATE centers SET lat=?, lon=?, source=?, updated_at=?, verified=0, link_diff_m=?, "
                            "google_lat=NULL, google_lon=NULL, google_type=NULL, google_addr=NULL, google_gap_m=NULL, "
                            "google_verdict=NULL, google_at=NULL WHERE code=?",
                            (lat, lon, source, now(), ld, rec["code"]))
                rep["updated_coords"] += 1
        # فحوصات كل المراكز
        for r in con.execute("SELECT code,gov,lat,lon FROM centers").fetchall():
            fl = validate_center(con, r["code"], r["gov"], r["lat"], r["lon"]) if r["lat"] is not None else []
            con.execute("UPDATE centers SET flags=? WHERE code=?", (" | ".join(fl), r["code"]))
        con.commit()
    if own:
        con.close()
    return rep


def center_set(con, code, lat, lon, note="", source="manual"):
    row = con.execute("SELECT gov, link_lat, link_lon, google_lat, google_lon FROM centers WHERE code=?", (code,)).fetchone()
    if row is None:
        raise KeyError(code)
    ld = km(lat, lon, row["link_lat"], row["link_lon"]) * 1000 if row["link_lat"] is not None else None       # الفروق بتتحسب على الموقع الجديد
    gg = km(lat, lon, row["google_lat"], row["google_lon"]) * 1000 if row["google_lat"] is not None else None
    with _lock:
        con.execute("UPDATE centers SET lat=?, lon=?, source=?, verified=1, note=?, updated_at=?, link_diff_m=?, google_gap_m=? WHERE code=?",
                    (lat, lon, source, note, now(), ld, gg, code))
        fl = validate_center(con, code, row["gov"], lat, lon)
        con.execute("UPDATE centers SET flags=? WHERE code=?", (" | ".join(fl), code))
        con.commit()
    return fl


def centers_coords(con):
    return {r["code"]: (r["lat"], r["lon"], r["source"]) for r in con.execute("SELECT code,lat,lon,source FROM centers")}


# ---------------- كاش geocoder والتصحيحات ----------------
def geo_key(gov, area, address):
    return f"{gov}|{gn.norm(area)}|{gn.norm(address)}"


def cache_prepare(con, fp):
    """لو الخريطة/المراكز اتغيرت، نمسح نتايج الـ geocoder الأوتوماتيك (مش نتايج Google ولا التصحيح اليدوي)."""
    with _lock:
        cur = con.execute("SELECT v FROM meta WHERE k='geo_fp'").fetchone()
        if (cur["v"] if cur else None) != fp:
            con.execute("DELETE FROM geo_cache WHERE source NOT IN ('google','manual')")
            con.execute("INSERT OR REPLACE INTO meta(k,v) VALUES ('geo_fp', ?)", (fp,))
            con.commit()


def cache_get_many(con, keys):
    out = {}
    keys = list(keys)
    for i in range(0, len(keys), 800):
        part = keys[i:i + 800]
        q = ",".join("?" * len(part))
        for r in con.execute(f"SELECT key,lat,lon,level,confidence,matched,source FROM geo_cache WHERE key IN ({q})", part):
            out[r["key"]] = (r["lat"], r["lon"], r["level"], r["confidence"], r["matched"], r["source"])
    return out


def cache_put_many(con, rows):
    """rows: (key, gov, area, address, lat, lon, level, confidence, matched, source)"""
    with _lock:
        con.executemany("INSERT OR REPLACE INTO geo_cache(key,gov,area,address,lat,lon,level,confidence,matched,source,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)", [r + (now(),) for r in rows])
        con.commit()


def overrides_get(con, nids):
    out = {}
    nids = [n for n in nids if n]
    for i in range(0, len(nids), 800):
        part = nids[i:i + 800]
        q = ",".join("?" * len(part))
        for r in con.execute(f"SELECT nid,lat,lon FROM overrides WHERE nid IN ({q})", part):
            out[r["nid"]] = (r["lat"], r["lon"])
    return out


def addr_overrides_get(con, keys):
    out = {}
    keys = list(keys)
    for i in range(0, len(keys), 800):
        part = keys[i:i + 800]
        q = ",".join("?" * len(part))
        for r in con.execute(f"SELECT key,lat,lon FROM addr_overrides WHERE key IN ({q})", part):
            out[r["key"]] = (r["lat"], r["lon"])
    return out


def override_set(con, lat, lon, nid=None, key=None, note=""):
    with _lock:
        if nid:
            con.execute("INSERT OR REPLACE INTO overrides(nid,lat,lon,note,updated_at) VALUES (?,?,?,?,?)",
                        (nid, lat, lon, note, now()))
        elif key:
            con.execute("INSERT OR REPLACE INTO addr_overrides(key,lat,lon,note,updated_at) VALUES (?,?,?,?,?)",
                        (key, lat, lon, note, now()))
        else:
            raise ValueError("لازم رقم قومي أو مفتاح عنوان")
        con.commit()


def group_overrides_get(con, gkeys):
    out = {}
    gkeys = list(gkeys)
    for i in range(0, len(gkeys), 800):
        part = gkeys[i:i + 800]
        q = ",".join("?" * len(part))
        for r in con.execute(f"SELECT gkey,lat,lon FROM group_overrides WHERE gkey IN ({q})", part):
            out[r["gkey"]] = (r["lat"], r["lon"])
    return out


def group_override_set(con, gkey, gov, area, text, lat, lon, note=""):
    with _lock:
        con.execute("INSERT OR REPLACE INTO group_overrides(gkey,gov,area,text,lat,lon,note,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                    (gkey, gov, area, text, lat, lon, note, now()))
        con.commit()


def group_override_delete(con, gkey):
    with _lock:
        con.execute("DELETE FROM group_overrides WHERE gkey=?", (gkey,))
        con.commit()


def override_delete(con, nid=None, key=None):
    with _lock:
        if nid:
            con.execute("DELETE FROM overrides WHERE nid=?", (nid,))
        if key:
            con.execute("DELETE FROM addr_overrides WHERE key=?", (key,))
        con.commit()


def status(con):
    gz = find_gazetteer()
    return {
        "db_path": DB_PATH, "centers": con.execute("SELECT COUNT(*) FROM centers").fetchone()[0],
        "centers_with_coords": con.execute("SELECT COUNT(*) FROM centers WHERE lat IS NOT NULL").fetchone()[0],
        "centers_verified": con.execute("SELECT COUNT(*) FROM centers WHERE verified=1").fetchone()[0],
        "geo_cache": con.execute("SELECT COUNT(*) FROM geo_cache").fetchone()[0],
        "overrides": con.execute("SELECT COUNT(*) FROM overrides").fetchone()[0] +
                     con.execute("SELECT COUNT(*) FROM addr_overrides").fetchone()[0] +
                     con.execute("SELECT COUNT(*) FROM group_overrides").fetchone()[0],
        "gazetteer": gz, "gazetteer_mb": round(os.path.getsize(gz) / 1e6) if gz else None,
    }
