"""محرك توزيع الطلاب على المراكز.

القواعد:
  1. المحافظة إلزامية: الطالب ما يتوزعش خارج محافظته (إلا لو GOV_ALLOW سمحت).
  2. نفس المنطقة أولًا، وبعدها الأقرب مسافة بالطريق.
  3. لازم نفس الليفيل والتراك (كود الجروب: L1 / L3WB ...).
  4. أقصى مسافة MAX_KM. لو المراكز ممتلئة أو أبعد => VP مع السبب.

المسافة: 'osrm' = مسافة طرق فعلية من سيرفر OSRM  |  'factor' = خط مستقيم × معامل (تقدير).
"""
import hashlib
import json
import math
import os
import pickle
import re
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import numpy as np
import pandas as pd

MAX_KM = 50
TOP_CENTERS = 15
DETOUR = 1.3         # معامل تقدير مسافة الطريق من الخط المستقيم (لو OSRM مش مستخدم)
ROAD_GRID = None     # None = إحداثيات كل طالب بدقتها الكاملة (الأدق). رقم مثل 3 = تجميع مواقع ≈110 م (أسرع وتقريبي)
from distributor import config as _config

OSRM_CACHE = str(_config.OSRM_CACHE)
# نقط ثابتة في مصر لـ "بصمة الخريطة": لو مسافاتها اتغيرت يبقى الخريطة اتحدثت والكاش القديم بيتمسح لوحده
FP_PTS = [(30.0444, 31.2357), (31.2001, 29.9187), (31.0409, 31.3785), (24.0889, 32.8998)]
OSRM_TABLE_LIMIT = int(os.environ.get("OSRM_TABLE_LIMIT", "100"))   # --max-table-size في OSRM
OSRM_WORKERS = int(os.environ.get("OSRM_WORKERS", "8"))

# اسم المحافظة عند الطالب (بعد التطبيع) -> كود City في شيت المراكز
GOV_MAP = {
    "الاسكندريه": "alexandria", "البحيره": "beheira", "بني سويف": "beni_suef", "القاهره": "cairo",
    "الدقهليه": "dakahlia", "دمياط": "damietta", "الفيوم": "fayoum", "الغربيه": "gharbia",
    "الاسماعيليه": "ismailia", "كفر الشيخ": "kafr_el_sheikh", "المنيا": "minya",
    "الوادي الجديد": "new_valley", "بورسعيد": "port_said", "الشرقيه": "sharqia", "سوهاج": "sohag",
    "جنوب سيناء": "south_sinai", "السويس": "suez", "الجيزه": "giza", "القليوبيه": "qalyubia",
    "المنوفيه": "monufia", "اسيوط": "assiut", "اسوان": "aswan", "الاقصر": "luxor", "قنا": "qena",
    "البحر الاحمر": "red_sea", "مطروح": "matrouh", "شمال سيناء": "north_sinai",
}
# السماح بمراكز محافظة تانية. فاضي = كل طالب في محافظته بدون استثناء.
GOV_ALLOW = {}

TRACKS = {"cyber": "CY", "data": "DT", "web": "WB", "digital": "DA", "embedded": "EM"}


class JobCancelled(Exception):
    """بيترفع من الـ progress callback لما المستخدم يلغي التوزيع."""


class OSRMError(ValueError):
    """مشكلة في سيرفر OSRM (مش شغال / بيرفض / بيفشل)."""


# ---------------- helpers ----------------
def norm_ar(s):
    if s is None or (not isinstance(s, str) and pd.isna(s)):
        s = ""
    s = str(s).strip().lower()
    s = re.sub("[أإآ]", "ا", s).replace("ة", "ه").replace("ى", "ي")
    return re.sub(r"\s+", " ", s)


@lru_cache(maxsize=None)
def area_key(x):
    """مفتاح المنطقة: بدون ترتيب الكلمات / 'حي' / 'ال' / الهمزات."""
    toks = []
    for w in norm_ar(x).split(" "):
        w = re.sub(r"^ال", "", w)
        if w and w != "حي":
            toks.append(w)
    return frozenset(toks)


@lru_cache(maxsize=None)
def level_key(text):
    """'Level (3): Web' -> L3WB , 'Level 1' -> L1"""
    s = "" if text is None or (not isinstance(text, str) and pd.isna(text)) else str(text)
    m = re.search(r"(\d+)", s)
    if not m:
        return None
    tr = ""
    low = s.lower()
    for k, v in TRACKS.items():
        if k in low:
            tr = v
            break
    return f"L{m.group(1)}{tr}"


def group_key(code):
    m = re.match(r"\s*L(\d+)([A-Za-z]*)_", str(code), re.I)
    return f"L{m.group(1)}{m.group(2).upper()}" if m else None


def haversine(lat1, lon1, lat2, lon2):
    p = math.pi / 180
    a = (np.sin((lat2 - lat1) * p / 2) ** 2 +
         np.cos(lat1 * p) * np.cos(lat2 * p) * np.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742 * np.arcsin(np.sqrt(a))


def _find(df, names=None, startswith=None, contains=None):
    for c in df.columns:
        lc = str(c).strip().lower()
        if names and lc in names:
            return c
        if startswith and lc.startswith(startswith):
            return c
        if contains and any(t in lc for t in contains):
            return c
    return None


def _clean_id(series):
    """رقم قومي/كود كنص: بدون .0 وبدون NaN."""
    s = series.astype(str).str.strip().str.replace(r"\.0+$", "", regex=True)
    return s.where(~series.isna(), "")


LAT = {"lat", "latitude", "خط العرض", "عرض"}
LON = {"lon", "lng", "long", "longitude", "خط الطول", "طول"}


# ---------------- OSRM ----------------
def osrm_check(url, probe=None, timeout=8):
    """طلب /table حقيقي صغير. بيرجّع (ok, message, ms). مش بيرمي استثناءات."""
    base = (url or "").strip().rstrip("/")
    if not base:
        return False, "اكتب عنوان سيرفر OSRM.", 0
    if not base.startswith("http"):
        base = "http://" + base
    pts = probe or [(30.0444, 31.2357), (31.2001, 29.9187)]          # القاهرة / الإسكندرية (lat, lon)
    coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in pts)
    q = f"{base}/table/v1/driving/{coords}?annotations=distance"
    t = time.time()
    try:
        data = json.load(urllib.request.urlopen(q, timeout=timeout))
    except urllib.error.HTTPError as e:
        return False, f"السيرفر رد بخطأ HTTP {e.code}. تأكد إنه OSRM وإن العنوان صح.", 0
    except urllib.error.URLError as e:
        why = "الاتصال مرفوض" if ("10061" in str(e.reason) or "refused" in str(e.reason).lower()) else str(e.reason)
        return False, f"مفيش OSRM شغال على {base} ({why}). شغّله (Docker) أو اختار 'تقدير'.", 0
    except Exception as e:  # noqa
        return False, f"تعذر الاتصال بـ {base}: {e}", 0
    ms = int((time.time() - t) * 1000)
    if data.get("code") != "Ok":
        return False, f"OSRM رد بـ {data.get('code')}: {data.get('message', '')}", ms
    d = data.get("distances")
    if not d:
        return False, "OSRM قديم ولا يدعم annotations=distance. حدّث الصورة.", ms
    if d[0][1] is None:
        return False, "OSRM ملقاش طريق بين القاهرة والإسكندرية. ملف الخريطة ناقص؟", ms
    return True, f"OSRM شغال ({ms} ms)", ms


def _osrm_fingerprint(url):
    """مسافات طرق ثابتة بين 4 مدن: بصمة للخريطة اللي السيرفر شغال بيها. None لو فشل."""
    try:
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in FP_PTS)
        q = f"{url.rstrip('/')}/table/v1/driving/{coords}?annotations=distance&generate_hints=false"
        d = json.load(urllib.request.urlopen(q, timeout=20))
        if d.get("code") != "Ok":
            return None
        m = np.array(d["distances"], dtype=float) / 1000
        if np.isnan(m).any():
            return None
        return tuple(round(float(m[i][j]), 1) for i in range(len(FP_PTS)) for j in range(i + 1, len(FP_PTS)))
    except Exception:  # noqa
        return None


_cache_lock = threading.Lock()


def _cache_load(path):
    try:
        with open(path, "rb") as f:
            c = pickle.load(f)
        return c if isinstance(c, dict) else {}
    except Exception:  # noqa
        return {}


def _cache_save(path, cache):
    """كتابة ذرية (ملف مؤقت ثم استبدال) عشان الكاش ما يتلفش لو التوزيع اتقفل في النص."""
    try:
        with _cache_lock:
            tmp = path + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump(cache, f, protocol=4)
            os.replace(tmp, path)
    except Exception:  # noqa
        pass


def osrm_matrix(url, src, dst, limit=OSRM_TABLE_LIMIT, progress=None, workers=OSRM_WORKERS):
    """مسافات الطرق (كم) من كل نقطة في src لكل نقطة في dst عن طريق OSRM /table بالتوازي.
    src, dst: مصفوفات (n,2) من [lat, lon]. طلب فاشل => NaN (والمحرك بيقدّر مكانه).
    لو السيرفر بيفشل باستمرار بيرمي OSRMError بسرعة بدل ما يعلّق."""
    url = url.rstrip("/")
    out = np.full((len(src), len(dst)), np.nan)
    dlim = max(1, min(len(dst), limit // 2))
    slim = max(1, limit - dlim)
    lock = threading.Lock()
    stat = {"ok": 0, "fail": 0, "err": "", "exc": None}
    stop = threading.Event()

    def one(task):
        if stop.is_set():
            return
        d0, s0 = task
        dchunk, schunk = dst[d0:d0 + dlim], src[s0:s0 + slim]
        pts = np.vstack([schunk, dchunk])
        coords = ";".join(f"{lon:.6f},{lat:.6f}" for lat, lon in pts)
        srcs = ";".join(map(str, range(len(schunk))))
        dsts = ";".join(map(str, range(len(schunk), len(pts))))
        q = f"{url}/table/v1/driving/{coords}?sources={srcs}&destinations={dsts}&annotations=distance&generate_hints=false"
        ok, err = False, ""
        for attempt in range(2):
            if stop.is_set():
                return
            try:
                data = json.load(urllib.request.urlopen(q, timeout=60))
                if data.get("code") == "Ok":
                    out[s0:s0 + len(schunk), d0:d0 + len(dchunk)] = np.array(data["distances"], dtype=float) / 1000.0
                    ok = True
                    break
                err = f"{data.get('code')}: {data.get('message', '')}"
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "ignore")[:150]
                except Exception:  # noqa
                    pass
                err = f"HTTP {e.code} {body}"
                if "TooBig" in body:
                    err = ("OSRM رفض حجم الطلب (TooBig). شغّله بـ --max-table-size 100 أو أكبر، "
                           "أو اضبط OSRM_TABLE_LIMIT.")
                    break
            except Exception as e:  # noqa
                err = repr(e)
            time.sleep(0.4 * (attempt + 1))
        with lock:
            if ok:
                stat["ok"] += 1
            else:
                stat["fail"] += 1
                stat["err"] = err
                if (stat["ok"] == 0 and stat["fail"] >= 6) or (stat["fail"] >= 25 and stat["fail"] > stat["ok"]):
                    stop.set()
        if progress and d0 == 0:
            try:
                progress(len(schunk))
            except Exception as e:  # noqa  (مثلًا JobCancelled)
                stat["exc"] = e
                stop.set()

    tasks = [(d0, s0) for d0 in range(0, len(dst), dlim) for s0 in range(0, len(src), slim)]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, tasks))
    if stat["exc"] is not None:
        raise stat["exc"]
    if stop.is_set():
        raise OSRMError(f"سيرفر OSRM بيفشل ({stat['fail']} طلب فشل، {stat['ok']} نجح). آخر خطأ: {stat['err']}")
    return out, stat


# ---------------- main ----------------
def distribute(S, C, max_km=MAX_KM, progress=lambda p, m: None, road_mode="factor", detour=DETOUR,
               osrm_url="", road_grid=ROAD_GRID, use_cache=True, cache_path=OSRM_CACHE):
    """يرجّع dict فيه النتايج والتقارير. progress(p, msg) بيتنادى للتقدم وممكن يرمي JobCancelled.
    road_grid: None (الافتراضي) = إحداثيات كل طالب بدقتها الكاملة. رقم (3 ≈ 110 م) = تجميع مواقع تقريبي أسرع بس أقل دقة.
    use_cache: حفظ المسافات الدقيقة على الديسك لتسريع التشغيلات الجاية (من غير أي تغيير في النتيجة)."""
    warnings, alerts = [], []
    S = S.copy().reset_index(drop=True)
    C = C.copy().reset_index(drop=True)
    S.columns = [str(c).strip() for c in S.columns]
    C.columns = [str(c).strip() for c in C.columns]
    progress(2, "فحص الأعمدة")

    # ---- columns: students ----
    lvl_col = _find(S, startswith="level")
    gov_col = _find(S, names={"gov", "governorate", "المحافظة", "المحافظه"})
    area_col = _find(S, names={"area", "المنطقة", "المنطقه"})
    s_lat, s_lon = _find(S, names=LAT), _find(S, names=LON)
    missing = [n for n, c in [("Level", lvl_col), ("gov (المحافظة)", gov_col), ("Area (المنطقة)", area_col),
                              ("Latitude", s_lat), ("Longitude", s_lon)] if c is None]
    if missing:
        raise ValueError("شيت الطلاب ناقص الأعمدة دي: " + "، ".join(missing))

    # ---- columns: centers ----
    need = ["Group Code", "Center Code", "City", "Area"]
    miss = [c for c in need if c not in C.columns]
    cap_col = _find(C, contains=["سعة", "سعه", "capacity"])
    c_lat, c_lon = _find(C, names=LAT), _find(C, names=LON)
    if cap_col is None:
        miss.append("سعة القاعة")
    if c_lat is None or c_lon is None:
        miss.append("Latitude / Longitude")
    if miss:
        raise ValueError("شيت المراكز ناقص الأعمدة دي: " + "، ".join(miss))

    progress(5, "تجهيز بيانات المراكز")
    C["cap"] = pd.to_numeric(C[cap_col], errors="coerce").fillna(0).astype(int)
    C["key"] = C["Group Code"].map(group_key)
    C["lat"] = pd.to_numeric(C[c_lat], errors="coerce")
    C["lon"] = pd.to_numeric(C[c_lon], errors="coerce")
    bad_code = C[C["key"].isna()]
    if len(bad_code):
        warnings.append(f"{len(bad_code)} جروب كوده مش بصيغة L1_xxxx أو L3WB_xxxx وتم تجاهلهم.")
        C = C[C["key"].notna()]
    # الإحداثيات مكتوبة قدام أول روم بس: نعممها على كل رومز نفس المركز
    for col in ("lat", "lon"):
        C[col] = C.groupby("Center Code")[col].transform(lambda s: s.dropna().iloc[0] if s.notna().any() else np.nan)
    no_coord = sorted(C[C.lat.isna()]["Center Code"].unique())
    if no_coord:
        warnings.append(f"{len(no_coord)} مركز مالهمش إحداثيات وتم تجاهلهم: " + ", ".join(map(str, no_coord[:30])) +
                        (" ..." if len(no_coord) > 30 else ""))
        C = C[C.lat.notna()]
    C = C.reset_index(drop=True)
    if not len(C):
        raise ValueError("شيت المراكز مفيهوش جروبات صالحة (كود الجروب أو الإحداثيات ناقصة).")

    centers = C.drop_duplicates("Center Code").reset_index(drop=True)
    centers["gov"] = centers["City"].map(lambda x: str(x).strip().lower())
    centers["akey"] = centers["Area"].map(area_key)
    nC = len(centers)
    cidx = {c: i for i, c in enumerate(centers["Center Code"])}
    c_lat_a, c_lon_a, c_gov = centers.lat.to_numpy(float), centers.lon.to_numpy(float), centers.gov.to_numpy(object)
    c_code = centers["Center Code"].to_numpy(object)
    c_name = centers["Center Name"].to_numpy(object) if "Center Name" in centers.columns else np.full(nC, "", object)
    c_city = centers["City"].to_numpy(object)
    c_area = centers["Area"].to_numpy(object)
    c_akey = centers["akey"].to_numpy(object)

    groups = {}      # (center_idx, key) -> [group dicts]
    cc_arr, gc_arr = C["Center Code"].to_numpy(object), C["Group Code"].to_numpy(object)
    cap_arr, key_arr = C["cap"].to_numpy(), C["key"].to_numpy(object)
    day_arr = C["Day"].to_numpy(object) if "Day" in C.columns else np.full(len(C), "", object)
    time_arr = C["Time"].to_numpy(object) if "Time" in C.columns else np.full(len(C), "", object)
    for k in range(len(C)):
        groups.setdefault((cidx[cc_arr[k]], key_arr[k]), []).append(
            {"group": gc_arr[k], "left": int(cap_arr[k]), "cap": int(cap_arr[k]),
             "day": day_arr[k], "time": time_arr[k]})
    seats_left = {k: sum(g["cap"] for g in gl) for k, gl in groups.items()}      # للتخطي السريع
    has = {}
    for (ci, k) in groups:
        has.setdefault(k, np.zeros(nC, bool))[ci] = True

    # ---- students ----
    progress(12, "تجهيز بيانات الطلاب")
    n = len(S)
    key_a = np.empty(n, dtype=object)
    gov_a = np.empty(n, dtype=object)
    ak_a = np.empty(n, dtype=object)
    for i, (lv, gv, ar) in enumerate(zip(S[lvl_col], S[gov_col], S[area_col])):
        key_a[i] = level_key(lv)
        gov_a[i] = GOV_MAP.get(norm_ar(gv))
        ak_a[i] = area_key(ar)
    lat_a = pd.to_numeric(S[s_lat], errors="coerce").to_numpy(float)
    lon_a = pd.to_numeric(S[s_lon], errors="coerce").to_numpy(float)
    unk = sorted({str(g) for g, c in zip(S[gov_col], gov_a) if c is None})
    if unk:
        warnings.append("أسماء محافظات غير معروفة (الطلاب دول هيتحولوا VP، ضيف الاسم في GOV_MAP): " + "، ".join(unk[:20]))

    # ---- فحص الرقم القومي ----
    nid_col = _find(S, names={"student nid", "nid", "national id", "الرقم القومي", "رقم قومي"}, contains=["nid"])
    if nid_col is not None:
        S[nid_col] = _clean_id(S[nid_col])
        nid = S[nid_col]
        bad_nid = int((~nid.str.fullmatch(r"\d{14}")).sum())
        uniq = int(nid.nunique())
        if bad_nid > 0.01 * n or (n - uniq) > 0.01 * n:
            alerts.append(
                f"الرقم القومي مش سليم: {bad_nid:,} من {n:,} طالب مش 14 رقم (غالبًا اتحول لصيغة علمية زي "
                f"3.10033E+13 وقت حفظ الـ CSV)، والقيم المختلفة {uniq:,} بس. النتيجة ما تنفعش لتحديد الطالب أو "
                "مقارنته بنسخة تانية. صدّر شيت الطلاب تاني وعمود الرقم القومي Text. وعمود row_no في النتيجة "
                "هو رقم الصف في الملف المرفوع للرجوع ليه.")
        elif bad_nid:
            warnings.append(f"{bad_nid} رقم قومي مش 14 رقم (راجعهم): row_no = " +
                            ", ".join(str(i + 2) for i in np.nonzero(~nid.str.fullmatch(r"\d{14}").to_numpy())[0][:15]))

    # ---- فحص منطقية الإحداثيات ----
    # مركز بعيد أكتر من 80 كم عن وسيط مراكز محافظته، أو طالب بعيد أكتر من 100 كم عن وسيط طلاب محافظته
    cmed = centers.groupby("gov")[["lat", "lon"]].transform("median")
    c_off = haversine(c_lat_a, c_lon_a, cmed.lat.to_numpy(float), cmed.lon.to_numpy(float))
    sus_c = centers.loc[c_off > 80, ["Center Code", "City", "lat", "lon"]]
    if len(sus_c):
        warnings.append(f"إحداثيات {len(sus_c)} مركز مشكوك فيها (بعيدة عن باقي مراكز محافظتها): " + "، ".join(
            f"{r['Center Code']} ({r['lat']:.3f},{r['lon']:.3f})" for _, r in sus_c.iterrows()))
    dup = centers.groupby(["lat", "lon"])["Center Code"].apply(list)
    dup = dup[dup.apply(len) > 1]
    if len(dup):
        warnings.append("مراكز بنفس الإحداثيات بالظبط (غالبًا نسخ بالغلط): " + " | ".join("+".join(v) for v in dup))
    eq = centers[centers.lat == centers.lon]
    if len(eq):
        warnings.append("مراكز خط العرض فيها = خط الطول (غلط تسجيل): " + "، ".join(eq["Center Code"]))
    S_sus = np.zeros(n, dtype=bool)
    ok_s = np.array([g is not None for g in gov_a]) & ~np.isnan(lat_a) & ~np.isnan(lon_a)
    if ok_s.any():
        tmp = pd.DataFrame({"g": gov_a[ok_s], "la": lat_a[ok_s], "lo": lon_a[ok_s]})
        smed = tmp.groupby("g")[["la", "lo"]].transform("median")
        S_sus[ok_s] = haversine(tmp.la.to_numpy(), tmp.lo.to_numpy(), smed.la.to_numpy(), smed.lo.to_numpy()) > 100
        if S_sus.sum():
            warnings.append(f"{int(S_sus.sum())} طالب إحداثياتهم بعيدة جدًا (>100 كم) عن باقي طلاب محافظتهم "
                            "(غالبًا Geocoding غلط) - اتعلّموا في عمود coord_suspect.")

    gov_allowed_cache, area_eq_cache = {}, {}

    def allowed_mask(g):
        if g not in gov_allowed_cache:
            ok = {g, *GOV_ALLOW.get(g, [])}
            gov_allowed_cache[g] = np.array([x in ok for x in c_gov])
        return gov_allowed_cache[g]

    def area_eq(ak, g):
        k = (ak, g)
        if k not in area_eq_cache:
            area_eq_cache[k] = np.array([a == ak and cg == g for a, cg in zip(c_akey, c_gov)])
        return area_eq_cache[k]

    nearest_km = np.full(n, np.nan)
    nearest_ci = np.full(n, -1)
    reason = [""] * n
    pairs = []

    # ---- مسافات الطرق (OSRM) لكل طالب × مراكز محافظته ----
    road = None
    road_points = 0
    cache_hits = 0
    computed = 0
    fallback_cnt = 0
    failed_chunks = 0
    if road_mode == "osrm":
        osrm_url = osrm_url.strip().rstrip("/")
        if osrm_url and not osrm_url.startswith("http"):
            osrm_url = "http://" + osrm_url
        progress(14, "اختبار الاتصال بسيرفر OSRM")
        ok, msg, _ = osrm_check(osrm_url, probe=[(c_lat_a[0], c_lon_a[0]), (c_lat_a[-1], c_lon_a[-1])]
                                if nC > 1 and (c_lat_a[0], c_lon_a[0]) != (c_lat_a[-1], c_lon_a[-1]) else None)
        if not ok:
            raise OSRMError(msg)
        road = np.full((n, nC), np.nan)
        valid = np.nonzero(ok_s)[0]
        # الطلاب القريبين جدًا من بعض (نفس خلية الشبكة) بيتحسبلهم موقع واحد: أسرع بكتير ودقة كافية
        dec = 6 if road_grid is None else int(road_grid)
        scale = 10 ** dec
        cell = (np.round(np.nan_to_num(lat_a) * scale).astype(np.int64) * 100_000_000
                + np.round(np.nan_to_num(lon_a) * scale).astype(np.int64))
        by_gov = {}
        for i in valid:
            by_gov.setdefault(gov_a[i], []).append(i)
        plan = []
        for g, lst in by_gov.items():
            dest = np.nonzero(allowed_mask(g))[0]
            if not len(dest):
                continue
            idxs = np.array(lst)
            _, inv = np.unique(cell[idxs], return_inverse=True)
            cnt = np.bincount(inv)
            src_pts = np.column_stack([np.bincount(inv, weights=lat_a[idxs]) / cnt,
                                       np.bincount(inv, weights=lon_a[idxs]) / cnt])
            plan.append((idxs, inv, src_pts, dest))
        done = [0]
        total = sum(len(p[2]) for p in plan)
        road_points = total
        # كاش المسافات الدقيقة على الديسك: بيتجاهل لوحده لو الخريطة اتغيرت، وبيتحدث بس للمواقع الجديدة
        fp = _osrm_fingerprint(osrm_url) if use_cache else None
        cache = _cache_load(cache_path) if fp else {}
        if not fp or cache.get("fp") != fp or not isinstance(cache.get("gov"), dict):
            cache = {"fp": fp, "gov": {}}          # بدون كاش (مقفول أو البصمة فشلت)، أو الكاش قديم/تالف
        osrm_t0 = time.time()
        for idxs, inv, src_pts, dest in plan:
            def tick(k):
                done[0] += k
                el = time.time() - osrm_t0
                eta = (f" - باقي حوالي {int(el * (total - done[0]) / max(done[0] - cache_hits, 1))} ث"
                       if done[0] - cache_hits > 200 else "")
                progress(15 + int(55 * min(done[0], total) / max(total, 1)),
                         f"حساب مسافات الطرق ({min(done[0], total):,} / {total:,} موقع، لـ {len(valid):,} طالب){eta}")
            dst_pts = np.column_stack([c_lat_a[dest], c_lon_a[dest]])
            sig = hashlib.md5(json.dumps([[str(c_code[j]), round(float(c_lat_a[j]), 6), round(float(c_lon_a[j]), 6)]
                                          for j in dest]).encode()).hexdigest()
            gc = cache["gov"].setdefault(sig, {})
            keys = [(int(round(la * 1e6)), int(round(lo * 1e6))) for la, lo in src_pts]
            m = np.full((len(src_pts), len(dest)), np.nan)
            need = []
            for k, key in enumerate(keys):
                row = gc.get(key)
                if row is not None:
                    m[k] = row
                else:
                    need.append(k)
            hit = len(keys) - len(need)
            if hit:
                cache_hits += hit
                tick(hit)
            if need:
                res, st = osrm_matrix(osrm_url, src_pts[need], dst_pts, progress=tick)
                failed_chunks += st["fail"]
                m[need] = res
                for r_i, k in enumerate(need):
                    if not np.isnan(res[r_i]).any():
                        gc[keys[k]] = res[r_i].copy()
                computed += len(need)
                if fp:
                    _cache_save(cache_path, cache)
            road[np.ix_(idxs, dest)] = m[inv]
    if road_mode == "osrm":
        road_label = "مسافة طريق (OSRM)" + ("" if road_grid is None else f"، تجميع مواقع ≈{int(111000 / 10 ** int(road_grid))} م")
    else:
        road_label = f"تقدير طريق (x{detour})"

    CH = 4000
    H = None
    h0 = 0
    for i in range(n):
        if i % 2000 == 0 and road is None:
            progress(15 + int(55 * i / max(n, 1)), f"حساب المسافات ({i:,} / {n:,})")
        if i % CH == 0:
            h0, hi = i, min(n, i + CH)
            H = haversine(lat_a[h0:hi, None], lon_a[h0:hi, None], c_lat_a[None, :], c_lon_a[None, :])
        if np.isnan(lat_a[i]) or np.isnan(lon_a[i]):
            reason[i] = "موقع الطالب غير محدد (إحداثيات ناقصة) - راجع الشيت"
            continue
        if key_a[i] is None:
            reason[i] = "الليفيل غير معروف - راجع الشيت"
            continue
        if gov_a[i] is None:
            reason[i] = "اسم المحافظة غير معروف - راجع الشيت"
            continue
        if key_a[i] not in has:
            reason[i] = f"لا يوجد في أي مركز جروب {key_a[i]} - مقترح VP"
            continue
        base = has[key_a[i]] & allowed_mask(gov_a[i])
        if not base.any():
            reason[i] = f"لا يوجد في محافظته أي مركز به {key_a[i]} - مقترح VP"
            continue
        straight = H[i - h0]
        if road is None:
            d = straight * detour
        else:
            d = road[i].copy()
            bad = np.isnan(d)
            if bad[base].any():                       # مسار غير متاح => تقدير بدل ما نخسر الطالب
                d[bad] = straight[bad] * detour
                fallback_cnt += 1
        idx = np.nonzero(base)[0]
        jn = idx[int(np.argmin(d[idx]))]
        nearest_km[i] = round(float(d[jn]), 1)
        nearest_ci[i] = jn
        within = idx[d[idx] <= max_km]
        if not len(within):
            reason[i] = (f"أبعد من {max_km:g} كم: أقرب مركز في محافظته {nearest_km[i]} كم "
                         f"({c_code[jn]}) - مقترح VP"
                         + (" - غالبًا إحداثيات الطالب غلط" if S_sus[i] else ""))
            continue
        eqm = area_eq(ak_a[i], gov_a[i])
        tier = (~eqm[within]).astype(int)
        order = within[np.lexsort((d[within], tier))][:TOP_CENTERS]
        for j in order:
            pairs.append((int(not eqm[j]), float(d[j]), i, int(j)))

    # ---- greedy: نفس المنطقة أولًا ثم الأقرب ----
    progress(72, "توزيع الطلاب")
    pairs.sort()
    res = {}
    for tier, dist, i, j in pairs:
        if i in res:
            continue
        gk = (j, key_a[i])
        if seats_left[gk] <= 0:
            continue
        gl = [g for g in groups[gk] if g["left"] > 0]
        g = max(gl, key=lambda x: x["left"])     # توزيع متساوي بين جروبات المركز
        g["left"] -= 1
        seats_left[gk] -= 1
        res[i] = (j, g, round(dist, 1), tier)

    for i in range(n):
        if i not in res and not reason[i]:
            reason[i] = f"كل مراكز المحافظة داخل {max_km:g} كم ممتلئة - مقترح VP"

    # ---- output (vectorized) ----
    progress(80, "تجهيز النتائج")
    status = np.full(n, "VP", dtype=object)
    f_center = np.full(n, "", dtype=object)
    f_name = np.full(n, "", dtype=object)
    f_city = np.full(n, "", dtype=object)
    f_area = np.full(n, "", dtype=object)
    f_same = np.full(n, "", dtype=object)
    f_group = np.full(n, "", dtype=object)
    f_day = np.full(n, "", dtype=object)
    f_time = np.full(n, "", dtype=object)
    f_near = np.full(n, "", dtype=object)
    f_note = np.array(reason, dtype=object)
    dist_out = np.full(n, np.nan)
    a_idx = np.fromiter(res.keys(), dtype=np.int64, count=len(res))
    a_j = np.fromiter((res[i][0] for i in a_idx), dtype=np.int64, count=len(a_idx))
    if len(a_idx):
        a_tier = np.fromiter((res[i][3] for i in a_idx), dtype=np.int64, count=len(a_idx))
        status[a_idx] = "تم التوزيع"
        f_center[a_idx] = c_code[a_j]
        f_name[a_idx] = c_name[a_j]
        f_city[a_idx] = c_city[a_j]
        f_area[a_idx] = c_area[a_j]
        f_same[a_idx] = np.where(a_tier == 0, "نعم", "لا")
        f_group[a_idx] = [res[i][1]["group"] for i in a_idx]
        f_day[a_idx] = [res[i][1]["day"] for i in a_idx]
        f_time[a_idx] = [str(res[i][1]["time"]) for i in a_idx]
        dist_out[a_idx] = [res[i][2] for i in a_idx]
        f_note[a_idx] = ""
    vp = status == "VP"
    dist_out[vp] = nearest_km[vp]                           # للـ VP: أقرب مركز في المحافظة
    near_mask = vp & (nearest_ci >= 0)
    f_near[near_mask] = c_code[nearest_ci[near_mask]]

    out = S.copy()
    out.insert(0, "row_no", np.arange(n) + 2)               # رقم الصف في الملف المرفوع (الصف 1 = العناوين)
    out["status"] = status
    out["center_code"] = f_center
    out["center_name"] = f_name
    out["center_city"] = f_city
    out["center_area"] = f_area
    out["same_area"] = f_same
    out["group_code"] = f_group
    out["day"] = f_day
    out["time"] = f_time
    out["distance_km"] = dist_out
    out["nearest_center"] = f_near
    out["note"] = f_note
    out["coord_suspect"] = np.where(S_sus, "نعم", "")
    out["level_code"] = key_a
    out["gov_code"] = gov_a
    out["distance_type"] = road_label

    # قايمة الإحداثيات المشبوهة (للداش بورد والإكسل)
    sc = []
    same_xy = centers.groupby(["lat", "lon"])["Center Code"].apply(list).to_dict()
    for i_c in range(nC):
        why = []
        if c_off[i_c] > 80:
            why.append(f"بعيد {int(c_off[i_c])} كم عن باقي مراكز المحافظة")
        if c_lat_a[i_c] == c_lon_a[i_c]:
            why.append("خط العرض = خط الطول")
        others = [x for x in same_xy.get((c_lat_a[i_c], c_lon_a[i_c]), []) if x != c_code[i_c]]
        if others:
            why.append("نفس الإحداثيات مع " + "+".join(others))
        if why:
            sc.append({"center_code": c_code[i_c], "center_name": c_name[i_c], "gov": c_city[i_c],
                       "lat": c_lat_a[i_c], "lon": c_lon_a[i_c], "problem": " | ".join(why)})
    sus_centers = pd.DataFrame(sc, columns=["center_code", "center_name", "gov", "lat", "lon", "problem"])
    sus_cols = [c for c in ["row_no", nid_col, gov_col, area_col, lvl_col] if c]
    sus_students = out.loc[S_sus, sus_cols + ["status", "note"]].copy()
    sus_students["lat"], sus_students["lon"] = lat_a[S_sus], lon_a[S_sus]

    # ---- group load ----
    load_rows = []
    for (ci, k), gl in groups.items():
        for g in gl:
            load_rows.append({"center_code": c_code[ci], "center_name": c_name[ci], "gov": c_city[ci],
                              "area": c_area[ci], "level_code": k, "group_code": g["group"], "day": g["day"],
                              "time": str(g["time"]), "capacity": g["cap"], "assigned": g["cap"] - g["left"],
                              "lat": c_lat_a[ci], "lon": c_lon_a[ci]})
    load = pd.DataFrame(load_rows)
    load["occupancy_%"] = (100 * load["assigned"] / load["capacity"].replace(0, np.nan)).round(0)

    # ---- reports ----
    progress(88, "إعداد التقارير")
    out["_vp"] = vp
    by_gov = (out.groupby(gov_col).agg(students=("status", "size"), assigned=("_vp", lambda s: int((~s).sum())),
                                       vp=("_vp", "sum")).reset_index().rename(columns={gov_col: "gov"}))
    by_gov["vp_%"] = (100 * by_gov.vp / by_gov.students).round(1)
    by_level = (out.groupby("level_code", dropna=False).agg(students=("status", "size"),
                                                           assigned=("_vp", lambda s: int((~s).sum())),
                                                           vp=("_vp", "sum")).reset_index())
    seats_lv = load.groupby("level_code").capacity.sum().rename("seats_all_egypt")
    by_level = by_level.merge(seats_lv, on="level_code", how="left")

    # العجز: طلاب (محافظة × ليفيل) مقابل مقاعد نفس المحافظة
    seats_g = load.assign(gov_l=load["gov"].str.lower()).groupby(["gov_l", "level_code"]).capacity.sum()
    dem = out.groupby(["gov_code", "level_code"]).size()
    dem_vp = out[out._vp].groupby(["gov_code", "level_code"]).size()
    # سعة الجروب الجديد = السعة الأكثر تكرارًا في الشيت لنفس الليفيل/التراك (وإلا الأكثر تكرارًا عمومًا)
    pos = load[load.capacity > 0].capacity
    overall_cap = int(pos.mode().iloc[0]) if len(pos) else 20
    cap_by_key = {k: int(v.mode().iloc[0]) for k, v in load[load.capacity > 0].groupby("level_code").capacity}
    rows = []
    for (g, k), cnt in dem.items():
        seats = int(seats_g.get((g, k), 0))
        vp_n = int(dem_vp.get((g, k), 0))
        deficit = max(0, cnt - seats)
        gcap = cap_by_key.get(k, overall_cap)
        rows.append({"gov": g, "level_code": k, "students": int(cnt), "seats_in_gov": seats, "vp": vp_n,
                     "deficit": deficit, "group_capacity": gcap,
                     "extra_groups_needed": math.ceil(deficit / gcap)})
    deficit_df = pd.DataFrame(rows, columns=["gov", "level_code", "students", "seats_in_gov", "vp", "deficit",
                                             "group_capacity", "extra_groups_needed"])
    deficit_df = deficit_df.sort_values(["deficit", "vp"], ascending=False)
    deficit_df = deficit_df[(deficit_df.deficit > 0) | (deficit_df.vp > 0)]

    vp_area = (out[out._vp].groupby([gov_col, area_col, "level_code"]).size().rename("vp_students")
               .reset_index().sort_values("vp_students", ascending=False))
    center_load = (load.groupby(["center_code", "center_name", "gov", "area"])
                   .agg(groups=("group_code", "size"), seats=("capacity", "sum"), assigned=("assigned", "sum"))
                   .reset_index())
    center_load["occupancy_%"] = (100 * center_load.assigned / center_load.seats.replace(0, np.nan)).round(0)

    # ---- QGIS layers ----
    pts = out.drop(columns=["_vp"]).copy()
    pts["lat"], pts["lon"] = lat_a, lon_a
    pts = pts.dropna(subset=["lat", "lon"])
    nid_vals = out[nid_col].to_numpy(object) if nid_col else out["row_no"].to_numpy(object)
    lines_df = pd.DataFrame({
        "student": nid_vals[a_idx] if len(a_idx) else [],
        "center_code": c_code[a_j] if len(a_idx) else [],
        "distance_km": dist_out[a_idx] if len(a_idx) else [],
        "wkt": [f"LINESTRING({lon_a[i]} {lat_a[i]}, {c_lon_a[j]} {c_lat_a[j]})" for i, j in zip(a_idx, a_j)],
    })

    out = out.drop(columns=["_vp"])
    vp_reasons = Counter(re.sub(r"\s*\([A-Za-z]+_C\d+\)", "", re.sub(r"[\d.]+ كم", "X كم", r))
                         for r in out.loc[out.status == "VP", "note"])
    if fallback_cnt:
        warnings.append(f"{fallback_cnt} طالب مسافتهم اتقدّرت (x{detour}) لأن OSRM فشل في {failed_chunks} طلب "
                        "أو مالقاش طريق ليهم.")

    assigned_mask = out.status == "تم التوزيع"
    summary = {
        "students": int(n), "assigned": int(assigned_mask.sum()), "vp": int((~assigned_mask).sum()),
        "same_area": int((out.same_area == "نعم").sum()),
        "suspect": int(S_sus.sum()), "distance_label": road_label, "road_points": int(road_points),
        "road_cache_hits": int(cache_hits), "road_computed": int(computed),
        "avg_km": round(float(np.nanmean(dist_out[a_idx])) if len(a_idx) else 0.0, 1),
        "centers": int(nC), "groups": int(len(load)), "seats": int(load.capacity.sum()),
        "vp_reasons": [{"reason": k, "count": v} for k, v in vp_reasons.most_common()],
        "warnings": warnings, "alerts": alerts,
    }
    progress(92, "حفظ الملفات")
    return {
        "summary": summary, "students": out, "load": load, "by_gov": by_gov, "by_level": by_level,
        "deficit": deficit_df, "vp_area": vp_area, "center_load": center_load,
        "sus_centers": sus_centers, "sus_students": sus_students,
        "points": pts, "lines": lines_df,
        "centers_pts": centers[["Center Code", "Center Name", "City", "Area", "lat", "lon"]]
        if "Center Name" in centers.columns else centers[["Center Code", "City", "Area", "lat", "lon"]],
    }
