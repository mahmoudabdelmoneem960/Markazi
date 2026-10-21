"""التحقق من مواقع المراكز بـ Google Geocoding API (اختياري، بمفتاح المستخدم نفسه).

- بيبعت لجوجل نص بس (اسم المركز + المنطقة + المحافظة، والعنوان). مفيش رقم قومي ولا أسماء طلاب.
- المفتاح بيتقرا من متغير البيئة GOOGLE_API_KEY أو من ملف data/google_key.txt (سطر واحد). عمره ما بيتكتب في الـDB ولا في اللوج ولا بيرجع في أي رد.
- عداد شهري محفوظ في الـDB وحد أقصى (افتراضي 9000 طلب = تحت الـ10,000 المجانية). العداد بيحسب كل طلب HTTP فعلي (بما فيهم إعادة المحاولة).
- القرار بيتاخد بس لما جوجل يدّي نتيجة دقيقة (مبنى/معلم) وتطابق (في حدود 300 م) موقع المركز الحالي أو موقع لينكه.
  غير كده بيتسجل الفرق ويتعرض للمراجعة من غير ما نغيّر حاجة. التصحيح اليدوي عمره ما بيتغيّر (حتى لو اتعدّل أثناء الفحص).
"""
import json
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from distributor import config
from distributor.core import locdb

GEOCODE_URL = os.environ.get("GOOGLE_GEOCODE_URL", "https://maps.googleapis.com/maps/api/geocode/json")
MONTHLY_CAP = int(os.environ.get("GOOGLE_MONTHLY_CAP", "9000"))
DELAY = float(os.environ.get("GOOGLE_DELAY", "0.12"))        # ثواني بين الطلبات
AGREE_M = 300                                                # جوجل بيطابق موقع لو الفرق أقل من كده
FAR_KM = 15                                                  # نتيجة تقريبية بعيدة أكتر من كده عن الموقع الحالي = تحذير
PRECISE_TYPES = ("ROOFTOP", "RANGE_INTERPOLATED")
PLACE_TYPES = ("establishment", "point_of_interest")
KEY_RE = re.compile(r"^[A-Za-z0-9_\-]{20,}$")
EMPTY_WORDS = ("nan", "none", "null")

GOV_AR = {"alexandria": "الإسكندرية", "cairo": "القاهرة", "giza": "الجيزة", "qalyubia": "القليوبية", "dakahlia": "الدقهلية",
          "sharqia": "الشرقية", "gharbia": "الغربية", "menoufia": "المنوفية", "beheira": "البحيرة", "sohag": "سوهاج",
          "damietta": "دمياط", "fayoum": "الفيوم", "minya": "المنيا", "beni_suef": "بني سويف", "kafr_el_sheikh": "كفر الشيخ",
          "port_said": "بورسعيد", "suez": "السويس", "ismailia": "الإسماعيلية", "south_sinai": "جنوب سيناء",
          "new_valley": "الوادي الجديد", "north_sinai": "شمال سيناء", "assiut": "أسيوط", "luxor": "الأقصر", "aswan": "أسوان",
          "qena": "قنا", "red_sea": "البحر الأحمر", "matrouh": "مطروح"}

VERDICT_AR = {
    "same_current": "جوجل بيأكد الموقع الحالي",
    "same_link": "جوجل بيأكد موقع اللينك",
    "fill": "جوجل حدد موقع كان ناقص",
    "differs": "جوجل مختلف عن الشيت واللينك",
    "conflict": "نتيجتين من جوجل مختلفين (مش حاسم)",
    "approx": "جوجل تقريبي (مش حاسم)",
    "approx_far": "جوجل تقريبي وبعيد عن الموقع الحالي",
    "none": "جوجل ملقاش نتيجة",
}

_running = threading.Lock()     # قفل مشترك: فحص جوجل وفحص اللينكات ما يشتغلوش مع بعض


class GoogleError(Exception):
    """خطأ واضح بالعربي (من غير المفتاح) بيوقف الفحص."""


class Cancelled(Exception):
    pass


# ---------------- المفتاح والعداد ----------------
def _read_key_file(p):
    raw = open(p, "rb").read()
    text = None
    for enc in ("utf-8-sig", "utf-16", "cp1256"):
        try:
            text = raw.decode(enc)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    if text is None:
        return None
    for line in text.splitlines():
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        line = re.sub(r"^\s*GOOGLE_API_KEY\s*=\s*", "", line)
        line = line.split("#")[0].strip().strip("\"'").strip()
        return line or None
    return None


def get_key():
    """المفتاح أو None (مفيش مفتاح أو شكله مش مفتاح). عمره ما بيتلوج."""
    k = os.environ.get("GOOGLE_API_KEY", "").strip().strip("\"'").strip()
    if not k:
        p = str(config.GOOGLE_KEY_FILE)
        if os.path.exists(p):
            try:
                k = _read_key_file(p) or ""
            except OSError:
                k = ""
    return k if (k and KEY_RE.match(k)) else None


def key_problem():
    """لو في ملف مفتاح لكن شكله غلط (أو مش مقروء) بيرجّع جملة توضح، وإلا ''."""
    if os.environ.get("GOOGLE_API_KEY", "").strip():
        return "" if get_key() else "المتغير GOOGLE_API_KEY موجود لكن شكله مش مفتاح جوجل (لازم 20 حرف أو أكتر من حروف وأرقام و- و_)."
    p = str(config.GOOGLE_KEY_FILE)
    if os.path.exists(p) and not get_key():
        return "ملف data\\google_key.txt موجود لكن مقدرتش أقرا منه مفتاح: لازم سطر واحد فيه المفتاح بس (احفظه UTF-8)."
    return ""


def _month():
    return time.strftime("%Y-%m")


def usage(con):
    r = con.execute("SELECT v FROM meta WHERE k=?", ("google_used_" + _month(),)).fetchone()
    return int(r["v"]) if r else 0


def _bump(con):
    """زيادة العداد بشكل ذري (UPDATE واحد): حتى لو في نسختين من السيستم على نفس الـDB مفيش زيادة بتضيع."""
    k = "google_used_" + _month()
    with locdb._lock:
        con.execute("INSERT OR IGNORE INTO meta(k,v) VALUES (?, '0')", (k,))
        con.execute("UPDATE meta SET v=CAST(v AS INTEGER)+1 WHERE k=?", (k,))
        con.commit()
    return usage(con)


def status(con):
    return {"key": bool(get_key()), "key_problem": key_problem(), "used": usage(con), "cap": MONTHLY_CAP, "month": _month()}


# ---------------- الطلب ----------------
def _clean(s, key):
    s = str(s)
    return s.replace(key, "***") if key else s


def geocode(query, key, timeout=20, tries=3, on_request=None):
    """نتيجة جوجل الأولى كـ dict، أو None لو مفيش نتيجة. بيرمي GoogleError لأخطاء المفتاح/الحد.
    on_request: بتتنادى قبل كل طلب HTTP فعلي (للعد ومراجعة الحد)."""
    params = urllib.parse.urlencode({"address": query, "key": key, "region": "eg", "language": "ar", "components": "country:EG"})
    url = GEOCODE_URL + ("&" if "?" in GEOCODE_URL else "?") + params
    last = ""
    for attempt in range(tries):
        if on_request:
            on_request()
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8", "ignore"))
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 * (attempt + 1))
                continue
            raise GoogleError(f"جوجل رفض الطلب ({last}). اتأكد من المفتاح وإن Geocoding API متفعّل.") from None
        except Exception as e:  # noqa  (ما بنرفعش الاستثناء الأصلي: ممكن يحمل الـURL وفيه المفتاح)
            last = _clean(type(e).__name__, key)
            time.sleep(1.5 * (attempt + 1))
            continue
        st = data.get("status", "")
        if st == "OK" and data.get("results"):
            r0 = data["results"][0]
            g = r0.get("geometry", {})
            loc = g.get("location", {})
            if "lat" not in loc or "lng" not in loc:
                return None
            types = r0.get("types", [])
            ltype = g.get("location_type", "")
            partial = bool(r0.get("partial_match"))
            precise = (not partial) and (ltype in PRECISE_TYPES or any(t in types for t in PLACE_TYPES))
            return {"lat": float(loc["lat"]), "lon": float(loc["lng"]), "type": ltype, "types": types,
                    "partial": partial, "addr": r0.get("formatted_address", ""), "precise": precise}
        if st in ("ZERO_RESULTS", "INVALID_REQUEST"):
            return None
        if st in ("OVER_QUERY_LIMIT", "OVER_DAILY_LIMIT"):
            raise GoogleError("جوجل بيقول إن المفتاح وصل لحد الاستخدام (OVER_QUERY_LIMIT). استنى شوية أو راجع الحصة والفوترة في Google Cloud.")
        if st == "REQUEST_DENIED":
            msg = _clean(data.get("error_message", ""), key)
            raise GoogleError("جوجل رفض المفتاح (REQUEST_DENIED). اتأكد إن Geocoding API متفعّل، والفوترة شغالة، "
                              "والمفتاح مش مقيّد بـ HTTP referrers (لازم يكون مقيّد على Geocoding API بس)."
                              + (f" رسالة جوجل: {msg}" if msg else ""))
        last = st
        time.sleep(1.5)
    raise GoogleError(f"تعذر الاتصال بجوجل ({last}). جرّب تاني.")


# ---------------- الاستعلامات والقرار ----------------
_FARSI = str.maketrans({"ی": "ي", "ک": "ك", "ہ": "ه", "ھ": "ه"})


def _txt(s):
    """نص نضيف لجوجل: حروف العرض (ﺷﺎرع) بتتحوّل لحروف عادية (NFKC) والياء/الكاف الفارسي لعربي، وnan/None بتتشال."""
    if s is None:
        return ""
    s = unicodedata.normalize("NFKC", str(s)).translate(_FARSI)
    s = re.sub(r"\s+", " ", s).strip()
    return "" if s.lower() in EMPTY_WORDS else s


def build_queries(row):
    gov = GOV_AR.get(row["gov"], _txt(row["gov"]))
    name, area, addr = _txt(row["name"]), _txt(row["area"]), _txt(row["address"])
    qs = []
    if name:
        qs.append(" ".join(x for x in (name, area, gov) if x))
    if addr:
        a = addr if (gov and gov in addr) else f"{addr} {gov}".strip()
        if a not in qs:
            qs.append(a)
    return qs


def pick_best(results):
    """أول نتيجة دقيقة (ولو اتنين دقيقين مختلفين أكتر من 300 م = conflict)، وإلا أول نتيجة تقريبية."""
    if not results:
        return None
    prec = [g for g in results if g["precise"]]
    if prec:
        g = dict(prec[0])
        for o in prec[1:]:
            if locdb.km(g["lat"], g["lon"], o["lat"], o["lon"]) * 1000 > AGREE_M:
                g["conflict"] = True
        return g
    return dict(results[0])


def decide(cur, link, g, manual=False):
    """cur / link: (lat, lon) أو None، g: نتيجة جوجل أو None.
    بيرجّع {verdict, apply: None|'verify'|'link'|'google', gap_cur_m, gap_link_m}.
    القاعدة: بنغيّر بس لما نتيجة جوجل دقيقة وبتطابق (في حدود 300 م) الموقع الحالي أو موقع اللينك."""
    if g is None:
        return {"verdict": "none", "apply": None, "gap_cur_m": None, "gap_link_m": None}
    gap_cur = locdb.km(cur[0], cur[1], g["lat"], g["lon"]) * 1000 if cur else None
    gap_link = locdb.km(link[0], link[1], g["lat"], g["lon"]) * 1000 if link else None
    out = {"gap_cur_m": gap_cur, "gap_link_m": gap_link, "apply": None}
    if g.get("conflict"):
        out["verdict"] = "conflict"
    elif not g["precise"]:
        out["verdict"] = "approx_far" if (gap_cur is not None and gap_cur > FAR_KM * 1000) else "approx"
    elif cur is None:
        if link is None:
            out["verdict"], out["apply"] = "fill", "google"
        elif gap_link <= AGREE_M:
            out["verdict"], out["apply"] = "same_link", "link"
        else:
            out["verdict"] = "differs"
    elif gap_cur <= AGREE_M:
        out["verdict"], out["apply"] = "same_current", "verify"
    elif link is not None and gap_link <= AGREE_M:
        out["verdict"], out["apply"] = "same_link", "link"
    else:
        out["verdict"] = "differs"
    if manual:                      # التصحيح اليدوي عمره ما بيتغيّر: لو جوجل بيطابق اللينك بس، ده اختلاف للمراجعة
        if out["apply"] == "link":
            out["verdict"] = "differs"
        out["apply"] = None
    return out


# ---------------- التخطيط والتنفيذ ----------------
def plan(con, scope="unverified"):
    """المراكز اللي هتتفحص وعدد الطلبات المتوقع. scope: 'unverified' (غير المتأكد منها) أو 'all'."""
    codes, n = [], 0
    for r in con.execute("SELECT * FROM centers ORDER BY gov, code"):
        if scope != "all" and r["verified"] and r["lat"] is not None:
            continue
        qs = build_queries(r)
        if qs:
            codes.append(r["code"])
            n += len(qs)
    return codes, n


def running():
    return _running.locked()


def verify_centers(con, codes, key, progress=None, cancelled=None, out=None):
    """بيفحص المراكز واحد واحد، بيحدّث الـDB، وبيرجّع ملخص. بيرمي GoogleError/Cancelled.
    out: dict اختياري بيتملا أول بأول (عشان الملخص الجزئي يفضل متاح لو الفحص وقف في النص)."""
    if not _running.acquire(blocking=False):
        raise GoogleError("في فحص شغال دلوقتي (جوجل أو لينكات). استنى لما يخلص.")
    try:
        out = out if out is not None else {}
        out.update({"checked": 0, "requests": 0, "applied": [], "review": [], "none": [], "skipped": [], "verdicts": {}})

        def count_request():
            if usage(con) >= MONTHLY_CAP:
                raise GoogleError(f"وصلنا للحد الشهري اللي ضابطينه ({MONTHLY_CAP} طلب) فوقفت عشان ما نعدّيش المجاني. "
                                  "الفحص اتحفظ لحد هنا، وتقدر تكمل الشهر الجاي.")
            _bump(con)
            out["requests"] += 1

        for i, code in enumerate(codes):
            if cancelled and cancelled():
                raise Cancelled()
            row = con.execute("SELECT * FROM centers WHERE code=?", (code,)).fetchone()
            if row is None:
                continue
            if progress:
                progress(int(100 * i / max(len(codes), 1)), f"جوجل: مركز {code} ({i + 1} / {len(codes)})")
            results = []
            for q in build_queries(row):
                g = geocode(q, key, on_request=count_request)
                if g:
                    results.append(g)
                time.sleep(DELAY)
            g = pick_best(results)
            cur = (row["lat"], row["lon"]) if row["lat"] is not None else None
            link = (row["link_lat"], row["link_lon"]) if row["link_lat"] is not None else None
            dec = decide(cur, link, g, manual=(row["source"] == "manual"))
            with locdb._lock:
                # المستخدم ممكن يكون عدّل المركز أثناء الطلبات: نراجع الحالة الحالية قبل أي كتابة
                row2 = con.execute("SELECT source, lat, lon FROM centers WHERE code=?", (code,)).fetchone()
                if row2 is None or row2["source"] != row["source"] or (row2["lat"], row2["lon"]) != (row["lat"], row["lon"]):
                    out["skipped"].append(code)         # اتعدّل أثناء الطلبات: مانكتبش حكم محسوب على الحالة القديمة
                    continue
                if dec["verdict"] in ("differs", "conflict", "approx_far") and row["source"] != "manual":
                    con.execute("UPDATE centers SET verified=0 WHERE code=?", (code,))      # الدليل الأحدث بيتعارض مع "متأكد"
                if dec["apply"] == "verify":
                    con.execute("UPDATE centers SET verified=1 WHERE code=?", (code,))
                elif dec["apply"] == "link":
                    prev = f" (الموقع السابق {cur[0]:.6f}, {cur[1]:.6f})" if cur else ""
                    locdb.center_set(con, code, link[0], link[1], note="اتأكد بجوجل (يطابق لينك الموقع)" + prev, source="google")
                elif dec["apply"] == "google":
                    locdb.center_set(con, code, g["lat"], g["lon"], note="موقع من جوجل", source="google")
                now = con.execute("SELECT lat, lon FROM centers WHERE code=?", (code,)).fetchone()
                gap_now = locdb.km(now["lat"], now["lon"], g["lat"], g["lon"]) * 1000 if (g and now["lat"] is not None) else None
                con.execute("UPDATE centers SET google_lat=?, google_lon=?, google_type=?, google_addr=?, google_gap_m=?, "
                            "google_verdict=?, google_at=? WHERE code=?",
                            (g["lat"] if g else None, g["lon"] if g else None, (g or {}).get("type", ""), (g or {}).get("addr", ""),
                             gap_now, dec["verdict"], locdb.now(), code))
                con.commit()
            out["checked"] += 1
            out["verdicts"][dec["verdict"]] = out["verdicts"].get(dec["verdict"], 0) + 1
            if dec["apply"] in ("link", "google"):
                out["applied"].append({"code": code, "action": dec["apply"], "from": cur,
                                       "to": (link if dec["apply"] == "link" else (g["lat"], g["lon"]))})
            if dec["verdict"] in ("differs", "conflict", "approx_far"):
                out["review"].append({"code": code, "verdict": dec["verdict"], "gap_cur_m": dec["gap_cur_m"],
                                      "gap_link_m": dec["gap_link_m"]})
            if dec["verdict"] == "none":
                out["none"].append(code)
        out["used"], out["cap"] = usage(con), MONTHLY_CAP
        return out
    finally:
        _running.release()
